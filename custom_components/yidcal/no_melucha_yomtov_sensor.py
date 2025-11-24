from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class NoMeluchaYomTovSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    ON for any contiguous Yom Tov span:
      candle(before first day) → havdalah(after last day).

    Special cases:
      • If the span starts right after Shabbos (prev day is Shabbos, not YT),
        start at Motza'ei Shabbos (havdalah), not at the prior evening's candles.
      • If the span leads into Shabbos (day *after* the last YT day is Shabbos,
        and that day is not Yom Tov), end at **Friday** candle-lighting
        (sunset(Friday) − candle_offset).

    In the diaspora, Shemini Atzeres → Simchas Torah is treated as one span.

    Attributes always show the current active span's window, or the next
    upcoming span if none is active:
      Now, Window_Start, Window_End, Activation_Logic
    """
    _attr_name = "No Melucha – Yom Tov"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_yomtov"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_no_melucha_yomtov"
        self._attr_unique_id = "yidcal_no_melucha_yomtov"

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
        await self.async_update()

        # Recalc every top-of-minute to match rounded Motzi / Zman windows
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ---- helpers ----

    def _sunset(self, d) -> datetime:
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _span_end(self, start) -> datetime.date:
        """
        Given the first halachic day of a Yom Tov span, return the LAST halachic date of that span.
        (In the diaspora, Shemini Atzeres immediately followed by Simchas Torah is treated as one span.)
        """
        end = start
        while HDateInfo(end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            end += timedelta(days=1)

        if self._diaspora:
            n_end = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
            n_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(
                hebrew=True, prefix_day=False
            )
            if n_end == "שמיני עצרת" and n_next == "שמחת תורה":
                end = end + timedelta(days=1)
        return end

    def _first_day_if_span_starts(self, d) -> datetime.date | None:
        """Return d if a Yom Tov span starts on d (yesterday not YT), else None."""
        if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(
            d - timedelta(days=1), diaspora=self._diaspora
        ).is_yom_tov:
            return d
        return None

    def _span_window(self, start: datetime.date, end: datetime.date) -> tuple[datetime, datetime]:
        """
        Compute the raw (unrounded) window for a pure-Yom-Tov span:
          start_dt .. end_dt (datetimes in self._tz).
        Applies the Motza'ei-Shabbos and YT→Shabbos special cases.
        """
        # Day before first YT day
        prev_day = start - timedelta(days=1)
        # Day after last YT day
        next_day = end + timedelta(days=1)

        # Base start: candles before first YT day
        start_dt = self._sunset(prev_day) - timedelta(minutes=self._candle)

        # If the span starts right after Shabbos (prev day is Shabbos and not YT),
        # start at Motza'ei Shabbos (havdalah), i.e. do NOT include Shabbos in this sensor.
        if prev_day.weekday() == 5 and not HDateInfo(prev_day, diaspora=self._diaspora).is_yom_tov:
            start_dt = self._sunset(prev_day) + timedelta(minutes=self._havdalah)

        # Base end: havdalah after LAST YT day
        end_dt = self._sunset(end) + timedelta(minutes=self._havdalah)

        # If the span leads directly into Shabbos (next_day is Shabbos and not YT),
        # end at **Friday** candle-lighting, not Motza'ei Shabbos.
        if next_day.weekday() == 5 and not HDateInfo(next_day, diaspora=self._diaspora).is_yom_tov:
            shabbos_eve = next_day - timedelta(days=1)  # Friday
            end_dt = self._sunset(shabbos_eve) - timedelta(minutes=self._candle)

        return start_dt, end_dt

    def _find_active_span(self, now_local: datetime):
        """
        If we're currently inside a YT span, return (first_day, last_day).
        Scan a generous window around 'now' to avoid edge timing issues.

        IMPORTANT: detection uses the *rounded* window (same as final state),
        so we don't cut off earlier than the other rounded sensors.
        """
        base = now_local.date()
        for i in range(-7, 60):  # a full week back, two months forward
            d = base + timedelta(days=i)
            first = self._first_day_if_span_starts(d)
            if not first:
                continue

            end = self._span_end(first)
            start_dt, end_dt = self._span_window(first, end)

            # Use the same rounding semantics as async_update
            window_start = _round_half_up(start_dt)
            window_end   = _round_ceil(end_dt)

            if window_start <= now_local < window_end:
                return first, end

        return None, None

    def _next_span_first_day_after(self, ref) -> datetime.date | None:
        """Find the first halachic day of the next YT span after (or on) ref, up to ~1 year ahead."""
        for i in range(0, 370):
            d = ref + timedelta(days=i)
            first = self._first_day_if_span_starts(d)
            if first:
                return first
        return None

    # ---- main ----

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        # 1) If in a span → use that; otherwise pick the next upcoming span
        start_d, end_d = self._find_active_span(now)
        if start_d is None:
            nxt = self._next_span_first_day_after(now.date())
            if nxt:
                start_d, end_d = nxt, self._span_end(nxt)

        if start_d is not None and end_d is not None:
            # 2) Build the window for that span (pure YT, with special cases)
            start_dt, end_dt = self._span_window(start_d, end_d)

            window_start = _round_half_up(start_dt)
            window_end = _round_ceil(end_dt)

            # 3) State is ON iff now inside this pure-YT window
            self._attr_is_on = window_start <= now < window_end

            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": window_start.isoformat(),
                "Window_End": window_end.isoformat(),
                "Activation_Logic": (
                    "ON for any contiguous Yom Tov span. "
                    "If the day before the span is Shabbos (and not Yom Tov), "
                    "start at Motza'ei Shabbos (havdalah). "
                    "If the span leads directly into Shabbos, end at Friday candle-lighting. "
                    "In the diaspora, Shemini Atzeres and Simchas Torah are treated as one span."
                ),
            }
            return

        # No span found (very unlikely) → publish blanks
        self._attr_is_on = False
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": "",
            "Window_End": "",
            "Activation_Logic": (
                "ON for any contiguous Yom Tov span. "
                "If the day before the span is Shabbos (and not Yom Tov), "
                "start at Motza'ei Shabbos (havdalah). "
                "If the span leads directly into Shabbos, end at Friday candle-lighting. "
                "In the diaspora, Shemini Atzeres and Simchas Torah are treated as one span."
            ),
        }
