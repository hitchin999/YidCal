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
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


def _alos_mga_72(cal: ZmanimCalendar, tz: ZoneInfo) -> datetime:
    sr = cal.sunrise().astimezone(tz)
    return _round_half_up(sr - timedelta(minutes=72))


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
        """True if d is Yom Tov (hdate)."""
        return HDateInfo(d, diaspora=self._diaspora).is_yom_tov

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
        True when the block contains:
          • at least one PURE Shabbos (Shabbos that is NOT also YT)
          • at least one Yom Tov day
        """
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

    def _shabbos_first(self, start: date) -> bool:
        """True when the first day of the block is Shabbos (not YT)."""
        return start.weekday() == 5 and not self._is_yom_tov(start)

    # ── main update ──────────────────────────────────────────────────

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz
        now = (now or datetime.now(tz)).astimezone(tz)
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
                    cal_erev = ZmanimCalendar(geo_location=self._geo, date=erev)
                    window_start = _round_half_up(
                        cal_erev.sunset().astimezone(tz)
                        - timedelta(minutes=self._candle)
                    )

                    morning_after = block_end + timedelta(days=1)
                    cal_after = ZmanimCalendar(geo_location=self._geo, date=morning_after)
                    window_end = _alos_mga_72(cal_after, tz)

                    if now < window_end:
                        # This is either current or next qualifying block
                        is_on = window_start <= now < window_end
                        self._attr_is_on = is_on

                        shabbos_first = self._shabbos_first(d)
                        self._attr_extra_state_attributes = {
                            'שבת ואח"כ יום טוב': shabbos_first and is_on,
                            'יום טוב ואח"כ שבת': (not shabbos_first) and is_on,
                        }
                        found = True
                        break

                # Skip past this block
                d = block_end + timedelta(days=1)
            else:
                d += timedelta(days=1)

        if not found:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                'שבת ואח"כ יום טוב': False,
                'יום טוב ואח"כ שבת': False,
            }
