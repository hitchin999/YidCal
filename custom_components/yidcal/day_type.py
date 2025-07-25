from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity

from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

# Days to exclude when detecting festivals
FAST_DAYS = {
    "צום גדליה",
    "תענית אסתר",
    "צום עשרה בטבת",
    "צום שבעה עשר בתמוז",
    "תשעה באב",
    "תשעה באב נדחה",
    "ט׳ באב",
    "ט׳ באב נדחה",
    "י׳ בטבת",
    "י׳ בטבת נדחה",
    "י״ז בתמוז",
    "י״ז בתמוז נדחה",
}

# All possible states
POSSIBLE_STATES = [
    "Any Other Day",
    "Erev",
    "Motzi",
    "Shabbos",
    "Yom Tov",
    "Shabbos & Yom Tov",
    "Fast Day",
    "Chol Hamoed",
    "Shabbos & Chol Hamoed",
]


def _is_yomtov(pydate: datetime.date) -> bool:
    """Festival detection: try pyluach, exclude FAST_DAYS and Chol Hamoed."""
    try:
        name = PHebrewDate.from_pydate(pydate).festival(hebrew=True, include_working_days=False)
        if name:
            return True
        return False
    except Exception:
        return False


def _is_chol_hamoed(pydate: datetime.date) -> bool:
    """Check if the given date is Chol Hamoed."""
    try:
        name_with = PHebrewDate.from_pydate(pydate).festival(hebrew=True, include_working_days=True)
        name_no = PHebrewDate.from_pydate(pydate).festival(hebrew=True, include_working_days=False)
        _LOGGER.debug(f"Festival with work for {pydate}: {name_with}")
        _LOGGER.debug(f"Festival no work for {pydate}: {name_no}")
        return bool(name_with and not name_no and name_with in ["פסח", "סוכות"])
    except Exception:
        return False


def _is_fast_day(pydate: datetime.date) -> bool:
    """Check if the given date is a fast day."""
    try:
        name = PHebrewDate.from_pydate(pydate).fast_day(hebrew=True)
        return bool(name)
    except Exception:
        return False


class DayTypeSensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_name = "Day Type"
    _attr_unique_id = "yidcal_day_type"
    entity_id = "sensor.yidcal_day_type"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state = ""
        # initialize attributes
        self._attr_extra_state_attributes = {s.replace(' ', '_'): False for s in POSSIBLE_STATES}
        self._attr_extra_state_attributes["possible_states"] = POSSIBLE_STATES

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._state = last.state or ""
            for k in list(self._attr_extra_state_attributes):
                if k != "possible_states":
                    self._attr_extra_state_attributes[k] = bool(last.attributes.get(k))
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    def _check_is_fast(self, hd: PHebrewDate) -> bool:
        if hd.month == 7 and hd.day == 3:
            return True
        if hd.month == 10 and hd.day == 10:
            return True
        if hd.month == 4 and hd.day == 17:
            return True
        if hd.month in (12, 13) and hd.day == 13:
            return True
        if hd.month == 5 and hd.day == 9 and hd.weekday() != 7:
            return True
        if hd.month == 5 and hd.day == 10 and hd.weekday() == 1 and PHebrewDate(hd.year, 5, 9).weekday() == 7:
            return True
        return False

    def _is_minor_fast(self, hd: PHebrewDate) -> bool:
        return self._check_is_fast(hd) and hd.month != 5

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()

        # Civil sun times for today
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        solar = sun(loc.observer, date=today, tzinfo=tz)
        dawn = solar["sunrise"] - timedelta(minutes=72)
        sunset_today = solar["sunset"]
        candle_cut = sunset_today - timedelta(minutes=self._candle_offset)
        havdalah_today = sunset_today + timedelta(minutes=self._havdalah_offset)

        # Effective pydate for current Hebrew day
        effective_pydate = today if now < havdalah_today else today + timedelta(days=1)

        # --- Shabbos window (calculated for the nearest/current Shabbos) ---
        shabbos_eve = today
        while shabbos_eve.weekday() != 4:
            shabbos_eve -= timedelta(days=1)
        shabbos_day = shabbos_eve + timedelta(days=1)
        shabbos_start = sun(loc.observer, date=shabbos_eve, tzinfo=tz)["sunset"] - timedelta(minutes=self._candle_offset)
        shabbos_end = sun(loc.observer, date=shabbos_day, tzinfo=tz)["sunset"] + timedelta(minutes=self._havdalah_offset)

        # --- Festival window detection ---
        dates_to_check = [today - timedelta(days=1), today, today + timedelta(days=1)]
        fest_dates = sorted(d for d in dates_to_check if _is_yomtov(d))
        if fest_dates:
            start_date = fest_dates[0]
            end_date = fest_dates[-1]
            # festival start: eve of first festival day
            eve = start_date - timedelta(days=1)
            fest_start = sun(loc.observer, date=eve, tzinfo=tz)["sunset"] - timedelta(minutes=self._candle_offset)
            # festival end: sunset+havdalah of last festival day
            fest_end = sun(loc.observer, date=end_date, tzinfo=tz)["sunset"] + timedelta(minutes=self._havdalah_offset)
            # if within festival window
            if fest_start <= now < fest_end:
                state = "Yom Tov"
                if shabbos_start <= now < shabbos_end:
                    state = "Shabbos & Yom Tov"
                attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
                attrs["possible_states"] = POSSIBLE_STATES
                self._state = state
                self._attr_extra_state_attributes = attrs
                return
            # holiday motzi: immediately after fest_end → 2 AM next day
            motzi_start = fest_end
            motzi_end = datetime.datetime.combine(end_date + timedelta(days=1), time(2, 0)).replace(tzinfo=tz)
            if motzi_start <= now < motzi_end:
                if _is_chol_hamoed(effective_pydate):
                    state = "Chol Hamoed"
                else:
                    state = "Motzi"
                attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
                attrs["possible_states"] = POSSIBLE_STATES
                self._state = state
                self._attr_extra_state_attributes = attrs
                return

        # --- Standard Motzi: only if yesterday was Yom Tov ---
        prev_date = today - timedelta(days=1)
        raw_motzi = _is_yomtov(prev_date)
        prev_sunset = sun(loc.observer, date=prev_date, tzinfo=tz)["sunset"]
        motzi_start = prev_sunset + timedelta(minutes=self._havdalah_offset)
        motzi_end = datetime.datetime.combine(today, time(2, 0)).replace(tzinfo=tz)
        if raw_motzi and motzi_start <= now < motzi_end:
            if _is_chol_hamoed(effective_pydate):
                state = "Chol Hamoed"
            else:
                state = "Motzi"
            attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
            attrs["possible_states"] = POSSIBLE_STATES
            self._state = state
            self._attr_extra_state_attributes = attrs
            return

        # --- Erev: dawn → candlelighting for Shabbos or Yom Tov eve ---
        is_yom_tom = _is_yomtov(today + timedelta(days=1))
        is_fast_tomorrow = _is_fast_day(today + timedelta(days=1))
        if dawn <= now < candle_cut and not is_fast_tomorrow and ((today.weekday() == 4 and not _is_yomtov(today)) or is_yom_tom):
            state = "Erev"
            attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
            attrs["possible_states"] = POSSIBLE_STATES
            self._state = state
            self._attr_extra_state_attributes = attrs
            return

        # --- Shabbos on Friday evening or Saturday day ---
        if shabbos_start <= now < shabbos_end:
            state = "Shabbos"
            if _is_yomtov(shabbos_day):
                state = "Shabbos & Yom Tov"
            elif _is_chol_hamoed(shabbos_day):
                state = "Shabbos & Chol Hamoed"
            attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
            attrs["possible_states"] = POSSIBLE_STATES
            self._state = state
            self._attr_extra_state_attributes = attrs
            return

        # --- Chol Hamoed ---
        if _is_chol_hamoed(effective_pydate):
            state = "Chol Hamoed"
            attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
            attrs["possible_states"] = POSSIBLE_STATES
            self._state = state
            self._attr_extra_state_attributes = attrs
            return

        # --- Fast days ---
        effective_pydate = today if now < havdalah_today else today + timedelta(days=1)
        effective_hd = PHebrewDate.from_pydate(effective_pydate)
        is_fast = self._check_is_fast(effective_hd)
        if not is_fast and now >= havdalah_today:
            previous_hd = PHebrewDate.from_pydate(today)
            if self._check_is_fast(previous_hd):
                is_fast = True
                effective_pydate = today
        if is_fast:
            end_solar = sun(loc.observer, date=effective_pydate, tzinfo=tz)
            end_time = end_solar["sunset"] + timedelta(minutes=self._havdalah_offset)
            in_fast = now < end_time
            if self._is_minor_fast(effective_hd) and now < sunset_today and now < dawn:
                in_fast = False
            if in_fast:
                state = "Fast Day"
                attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
                attrs["possible_states"] = POSSIBLE_STATES
                self._state = state
                self._attr_extra_state_attributes = attrs
                return

        # --- Motzi on Saturday evening ---
        if shabbos_end <= now < datetime.datetime.combine(shabbos_day + timedelta(days=1), time(2, 0)).replace(tzinfo=tz):
            state = "Motzi"
            attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
            attrs["possible_states"] = POSSIBLE_STATES
            self._state = state
            self._attr_extra_state_attributes = attrs
            return

        # --- Default: Any Other Day ---
        state = "Any Other Day"
        attrs = {s.replace(' ', '_'): (s == state) for s in POSSIBLE_STATES}
        attrs["possible_states"] = POSSIBLE_STATES
        self._state = state
        self._attr_extra_state_attributes = attrs

    @property
    def state(self) -> str:
        return self._state
