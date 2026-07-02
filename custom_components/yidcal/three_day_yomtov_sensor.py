# three_day_yomtov_sensor.py
"""
Binary sensor: "3 Days Yom Tov"

ON from candle-lighting on Erev through Alos the morning after
a continuous Shabbos + Yom Tov block (3+ days of no melacha).

Attributes:
  • שבת ואח"כ יום טוב  – True when Shabbos starts the block
  • יום טוב ואח"כ שבת  – True when YT starts the block
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.core import HomeAssistant

from hdate import HDateInfo

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .yidcal_lib.calcache import is_yom_tov as _cached_is_yom_tov
from .yidcal_lib.zman_compute import (
    dawn_for_date,
    round_half_up as _round_half_up,
    sunset_for_date,
)
from .zman_sensors import get_geo


class ThreeDayYomTovSensor(YidCalSpecialDevice, RestoreEntity, BinarySensorEntity):
    """
    ON during a continuous block that contains BOTH a pure Shabbos
    (Shabbos that is NOT also Yom Tov) AND at least one Yom Tov day.

    This distinguishes true 3-day blocks from normal 2-day YT where
    one day merely falls on Shabbos.
    """

    _attr_name = "3 Days Yom Tov"
    _attr_icon = "mdi:calendar-weekend"
    _attr_unique_id = "yidcal_three_day_yomtov"
    # Self-driven minute tick; without this HA's 30-second poller ALSO ran
    # the full 400-day scan twice a minute.
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_three_day_yomtov"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None
        self._attr_extra_state_attributes = {}
        # Cached result of the last full scan + clock-jump bookkeeping.
        self._win_start = None
        self._win_end = None
        self._shabbos_first = False
        self._have_window = False
        self._computed_on = None
        self._last_tick = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)

        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = last.state == "on"

        await self.async_update()

        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ── helpers ──────────────────────────────────────────────────────

    def _is_yom_tov(self, d: date) -> bool:
        """True if d is Yom Tov (cached — pure function of date+diaspora)."""
        return _cached_is_yom_tov(d, self._diaspora)

    def _is_no_melacha(self, d: date) -> bool:
        """True if d is Shabbos OR Yom Tov."""
        return d.weekday() == 5 or self._is_yom_tov(d)

    def _find_block(self, start: date) -> date:
        """
        Given start (first day of block), extend forward while
        consecutive days are no-melacha.  Returns the last day.
        """
        end = start
        while self._is_no_melacha(end + timedelta(days=1)):
            end += timedelta(days=1)
        return end

    def _block_has_both(self, start: date, end: date) -> bool:
        """
        True when the block:
          • spans at least 3 calendar days, AND
          • contains at least one PURE Shabbos (Shabbos that is NOT also YT), AND
          • contains at least one Yom Tov day.

        The length check is essential for Israel mode, where YT is 1 day.
        Without it, 2-day blocks (e.g. Fri-YT + Sat-Shabbos for Pesach D1,
        Pesach D7, or Shavuot in Israel) would incorrectly trigger this
        sensor. In Israel, only RH (the sole 2-day YT) can produce a true
        3-day block by stacking with Shabbos. Diaspora behavior is
        unaffected since diaspora YT is 2 days, so any block containing
        both a pure Shabbos and a YT is already ≥3 days.
        """
        if (end - start).days + 1 < 3:
            return False

        has_pure_shabbos = False
        has_yt = False
        d = start
        while d <= end:
            is_yt = self._is_yom_tov(d)
            is_shabbos = d.weekday() == 5
            if is_shabbos and not is_yt:
                has_pure_shabbos = True
            if is_yt:
                has_yt = True
            if has_pure_shabbos and has_yt:
                return True
            d += timedelta(days=1)
        return False

    def _shabbos_first_day(self, start: date) -> bool:
        """True when the first day of the block is Shabbos (not YT)."""
        return start.weekday() == 5 and not self._is_yom_tov(start)

    # ── main update ──────────────────────────────────────────────────

    async def async_update(self, now: datetime | None = None) -> None:
        """Cheap minute tick — full 400-day scan only on a crossing.

        Recompute when: the cached window ended, the civil date rolled
        (daily self-heal), or the clock STEPPED (manual time-walk / NTP:
        now < last_tick, or gap > 90s). Every other tick just re-evaluates
        ``window_start <= now < window_end`` against cached datetimes.
        Mirrors the ZmanimCoordinator's crossing-check design, so manual
        clock jumps still update the sensor within one minute.
        """
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz
        now = (now or datetime.now(tz)).astimezone(tz)

        last = self._last_tick
        self._last_tick = now
        stepped = last is None or now < last or (now - last) > timedelta(seconds=90)
        need_scan = (
            self._computed_on is None
            or stepped
            or now.date() != self._computed_on
            or (self._have_window and self._win_end is not None and now >= self._win_end)
        )
        if not need_scan:
            self._apply_cached(now)
            return

        await self._full_scan(now)

    def _apply_cached(self, now: datetime) -> None:
        if self._have_window and self._win_start is not None:
            is_on = self._win_start <= now < self._win_end
            self._attr_is_on = is_on
            self._attr_extra_state_attributes = {
                'שבת ואח"כ יום טוב': str(bool(self._shabbos_first and is_on)).lower(),
                'יום טוב ואח"כ שבת': str(bool((not self._shabbos_first) and is_on)).lower(),
            }
        else:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                'שבת ואח"כ יום טוב': "false",
                'יום טוב ואח"כ שבת': "false",
            }

    async def _full_scan(self, now: datetime) -> None:
        tz = self._tz
        ref_date = now.date()

        # Scan from a few days back (we might be inside a block's window)
        # through up to ~400 days forward to find the current/next block.
        scan_start = ref_date - timedelta(days=4)

        found = False
        d = scan_start
        for _ in range(410):
            if self._is_no_melacha(d):
                block_end = self._find_block(d)
                if self._block_has_both(d, block_end):
                    # Compute window
                    erev = d - timedelta(days=1)
                    window_start = _round_half_up(
                        sunset_for_date(geo=self._geo, tz=tz, base_date=erev)
                        - timedelta(minutes=self._candle)
                    )

                    morning_after = block_end + timedelta(days=1)
                    window_end = _round_half_up(
                        dawn_for_date(geo=self._geo, tz=tz, base_date=morning_after)
                    )

                    if now < window_end:
                        # Cache; the minute tick evaluates against it until
                        # window_end (or a clock step) forces a rescan.
                        self._win_start = window_start
                        self._win_end = window_end
                        self._shabbos_first = self._shabbos_first_day(d)
                        self._have_window = True
                        found = True
                        break

                # Skip past this block
                d = block_end + timedelta(days=1)
            else:
                d += timedelta(days=1)

        if not found:
            self._win_start = self._win_end = None
            self._shabbos_first = False
            self._have_window = False

        self._computed_on = now.date()
        self._apply_cached(now)
