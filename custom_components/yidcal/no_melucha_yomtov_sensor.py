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

    Early Yom Tov:
      • If early YT is enabled/effective for the Erev-YT date, move the start earlier
        based on sensor.yidcal_early_yomtov_yt_start_time (or fallback sensor).
      • Early does NOT override the Motza'ei-Shabbos start case.

    In the diaspora, Shemini Atzeres → Simchas Torah is treated as one span.

    Attributes always show the current active span's window, or the next
    upcoming span if none is active:
      Now, Window_Start, Window_End, Early_Start_Used, Early_Start_Time, Activation_Logic
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

    def _get_effective_early_yomtov_start(self, erev_date):
        """
        Try to read effective early YT start for a given EREV date.

        Primary sensor:
          sensor.yidcal_early_yomtov_yt_start_time
        Fallback sensor (if you combined in one):
          sensor.yidcal_early_shabbos_yt_start_time

        Expected attribute dict:
          effective_yomtov_start_by_date[YYYY-MM-DD] -> ISO datetime
        Also tolerates:
          effective_yt_start_by_date
        """
        for ent_id in (
            "sensor.yidcal_early_yomtov_yt_start_time",
            "sensor.yidcal_early_shabbos_yt_start_time",
        ):
            try:
                st = self.hass.states.get(ent_id)
                if not st:
                    continue
                eff = (
                    st.attributes.get("effective_yomtov_start_by_date")
                    or st.attributes.get("effective_yt_start_by_date")
                    or {}
                )
                iso = eff.get(erev_date.isoformat())
                if not iso:
                    continue
                dt = datetime.fromisoformat(iso)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=self._tz)
                return dt.astimezone(self._tz)
            except Exception:
                continue
        return None

    def _span_end(self, start) -> datetime.date:
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
        if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(
            d - timedelta(days=1), diaspora=self._diaspora
        ).is_yom_tov:
            return d
        return None

    def _span_window(self, start: datetime.date, end: datetime.date) -> tuple[datetime, datetime, datetime | None]:
        """
        Raw window for a pure-YT span, with special cases + early-start override.
        Returns (start_dt, end_dt, early_dt_used_or_none).
        """
        prev_day = start - timedelta(days=1)
        next_day = end + timedelta(days=1)

        # Base start: candles before first YT day
        start_dt = self._sunset(prev_day) - timedelta(minutes=self._candle)

        # Special case: span starts right after Shabbos (prev day is Shabbos, not YT)
        starts_after_shabbos = (
            prev_day.weekday() == 5
            and not HDateInfo(prev_day, diaspora=self._diaspora).is_yom_tov
        )
        if starts_after_shabbos:
            start_dt = self._sunset(prev_day) + timedelta(minutes=self._havdalah)
            early_dt = None  # don't early-override in this case
        else:
            # Early YT override for the EREV date
            early_dt = self._get_effective_early_yomtov_start(prev_day)
            if early_dt and early_dt < start_dt:
                start_dt = early_dt

        # Base end: havdalah after last YT day
        end_dt = self._sunset(end) + timedelta(minutes=self._havdalah)

        # Special case: span leads into Shabbos (next day is Shabbos, not YT)
        if next_day.weekday() == 5 and not HDateInfo(next_day, diaspora=self._diaspora).is_yom_tov:
            shabbos_eve = next_day - timedelta(days=1)  # Friday
            end_dt = self._sunset(shabbos_eve) - timedelta(minutes=self._candle)

        return start_dt, end_dt, early_dt

    def _find_active_span(self, now_local: datetime):
        base = now_local.date()
        for i in range(-7, 60):
            d = base + timedelta(days=i)
            first = self._first_day_if_span_starts(d)
            if not first:
                continue

            end = self._span_end(first)
            start_dt, end_dt, _early = self._span_window(first, end)

            window_start = _round_half_up(start_dt)
            window_end   = _round_ceil(end_dt)

            if window_start <= now_local < window_end:
                return first, end

        return None, None

    def _next_span_first_day_after(self, ref) -> datetime.date | None:
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

        start_d, end_d = self._find_active_span(now)
        if start_d is None:
            nxt = self._next_span_first_day_after(now.date())
            if nxt:
                start_d, end_d = nxt, self._span_end(nxt)

        if start_d is not None and end_d is not None:
            start_dt, end_dt, early_raw = self._span_window(start_d, end_d)

            window_start = _round_half_up(start_dt)
            window_end   = _round_ceil(end_dt)

            self._attr_is_on = window_start <= now < window_end

            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": window_start.isoformat(),
                "Window_End": window_end.isoformat(),
                "Early_Start_Used": bool(early_raw and early_raw < (self._sunset(start_d - timedelta(days=1)) - timedelta(minutes=self._candle))),
                "Early_Start_Time": early_raw.isoformat() if early_raw else "",
                "Activation_Logic": (
                    "ON for any contiguous Yom Tov span. "
                    "If the day before the span is Shabbos (and not Yom Tov), "
                    "start at Motza'ei Shabbos (havdalah). "
                    "If the span leads directly into Shabbos, end at Friday candle-lighting. "
                    "If Early Yom Tov is enabled/effective for Erev-YT, start earlier. "
                    "In the diaspora, Shemini Atzeres and Simchas Torah are treated as one span."
                ),
            }
            return

        self._attr_is_on = False
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": "",
            "Window_End": "",
            "Early_Start_Used": False,
            "Early_Start_Time": "",
            "Activation_Logic": (
                "ON for any contiguous Yom Tov span. "
                "If the day before the span is Shabbos (and not Yom Tov), "
                "start at Motza'ei Shabbos (havdalah). "
                "If the span leads directly into Shabbos, end at Friday candle-lighting. "
                "If Early Yom Tov is enabled/effective for Erev-YT, start earlier. "
                "In the diaspora, Shemini Atzeres and Simchas Torah are treated as one span."
            ),
        }
