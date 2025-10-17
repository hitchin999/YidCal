from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
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

    Special case:
      • If the span’s LAST day is Friday (YT → Shabbos), end at **Shabbos candle-lighting** (sunset − candle_offset).

    In the diaspora, Shemini Atzeres → Simchas Torah is treated as one span.

    Attributes always show the current active span's window, or the next upcoming span if none is active:
      Now, Window_Start, Window_End, Activation_Logic
    """
    _attr_name = "No Melucha – Yom Tov"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_yomtov"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_no_melucha_yomtov"

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
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ---- helpers ----

    def _sunset(self, d) -> datetime:
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _span_end(self, start: datetime.date) -> datetime.date:
        """
        Given the first halachic day of a Yom Tov span, return the LAST halachic date of that span.
        (In the diaspora, Shemini Atzeres immediately followed by Simchas Torah is treated as one span.)
        """
        end = start
        while HDateInfo(end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            end += timedelta(days=1)

        if self._diaspora:
            n_end  = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
            n_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(hebrew=True, prefix_day=False)
            if n_end == "שמיני עצרת" and n_next == "שמחת תורה":
                end = end + timedelta(days=1)
        return end

    def _first_day_if_span_starts(self, d: datetime.date) -> datetime.date | None:
        """Return d if a Yom Tov span starts on d (yesterday not YT), else None."""
        if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            return d
        return None

    def _find_active_span(self, now_local: datetime) -> tuple[datetime.date | None, datetime.date | None]:
        """
        If we're currently inside a YT span, return (first_day, last_day).
        Scan a generous window around 'now' to avoid edge timing issues.
        """
        base = now_local.date()
        for i in range(-7, 60):  # a full week back, two months forward
            d = base + timedelta(days=i)
            first = self._first_day_if_span_starts(d)
            if not first:
                continue
            end = self._span_end(first)
            sdt = self._sunset(first - timedelta(days=1)) - timedelta(minutes=self._candle)
            # default end at havdalah…
            edt = self._sunset(end) + timedelta(minutes=self._havdalah)
            # …BUT if YT leads into Shabbos (end on Friday), cut at Shabbos candle-lighting
            if end.weekday() == 4:  # Friday
                edt = self._sunset(end) - timedelta(minutes=self._candle)
            if sdt <= now_local < edt:
                return first, end
        return None, None

    def _next_span_first_day_after(self, ref: datetime.date) -> datetime.date | None:
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

        # 1) If in a span → use that; otherwise pick the next upcoming span (up to ~1 year ahead)
        start_d, end_d = self._find_active_span(now)
        if start_d is None:
            nxt = self._next_span_first_day_after(now.date())
            if nxt:
                start_d, end_d = nxt, self._span_end(nxt)

        if start_d is not None and end_d is not None:
            # 2) Build the window for that span
            start_dt = self._sunset(start_d - timedelta(days=1)) - timedelta(minutes=self._candle)

            # default: end at havdalah of last YT day
            end_dt = self._sunset(end_d) + timedelta(minutes=self._havdalah)
            # special case: if last YT day is Friday → end at Shabbos candle-lighting
            if end_d.weekday() == 4:  # Friday
                end_dt = self._sunset(end_d) - timedelta(minutes=self._candle)

            window_start = _round_half_up(start_dt)
            window_end   = _round_ceil(end_dt)

            # 3) State is ON iff now inside the chosen window
            self._attr_is_on = (window_start <= now < window_end)

            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": window_start.isoformat(),
                "Window_End": window_end.isoformat(),
                "Activation_Logic": "On for any Yom Tov, including if Yom Tov is on Shabbos: candle(before first day) → havdalah(after last day).",
            }
            return

        # No span within the next year (extremely unlikely) → publish blanks
        self._attr_is_on = False
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": "",
            "Window_End": "",
            "Activation_Logic": "On for any Yom Tov, including if Yom Tov is on Shabbos: candle(before first day) → havdalah(after last day).",
        }
