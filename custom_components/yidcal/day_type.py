from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import logging

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from hdate import HDateInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from .zman_sensors import get_geo

from .device import YidCalDevice
from .const import DOMAIN

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

def _round_half_up(dt: datetime.datetime) -> datetime.datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)

def _round_ceil(dt: datetime.datetime) -> datetime.datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

def _is_yomtov(pydate: datetime.date) -> bool:
    """Festival detection: try pyluach, exclude FAST_DAYS and Chol Hamoed."""
    try:
        name = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=False
        )
        return bool(name)
    except Exception:
        return False

def _is_chol_hamoed(pydate: datetime.date) -> bool:
    """Check if the given date is Chol Hamoed."""
    try:
        name_with = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=True
        )
        name_no = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=False
        )
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

def _attrs_for_state(state: str) -> dict:
    flags = {name: (name == state) for name in POSSIBLE_STATES}
    return {
        **flags,
        "Possible states": POSSIBLE_STATES,
    }

class DayTypeSensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_name = "Day Type"
    _attr_unique_id = "yidcal_day_type"
    entity_id = "sensor.yidcal_day_type"
    _attr_icon = "mdi:calendar-check"
    _attr_device_class = "enum"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_native_value = "Any Other Day"
        self._attr_extra_state_attributes = _attrs_for_state(self._attr_native_value)

        cfg = hass.data[DOMAIN]["config"]
        self._diaspora = cfg.get("diaspora", True)
        self._geo = None

    @property
    def options(self) -> list[str]:
        return POSSIBLE_STATES

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    def _set_state(self, state: str) -> None:
        self._attr_native_value = state
        self._attr_extra_state_attributes = _attrs_for_state(state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        last = await self.async_get_last_state()
        if last and last.state in POSSIBLE_STATES:
            self._set_state(last.state)
        else:
            self._set_state("Any Other Day")

        await self.async_update()

        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    def _check_is_fast(self, hd: PHebrewDate) -> bool:
        # Tzom Gedaliah
        if hd.month == 7 and hd.day == 3 and hd.weekday() != 7:
            return True
        if hd.month == 7 and hd.day == 4 and hd.weekday() == 1 and PHebrewDate(hd.year, 7, 3).weekday() == 7:
            return True
        # Asara B'Tevet
        if hd.month == 10 and hd.day == 10 and hd.weekday() != 7:
            return True
        if hd.month == 10 and hd.day == 11 and hd.weekday() == 1 and PHebrewDate(hd.year, 10, 10).weekday() == 7:
            return True
        # Shiv'a Asar B'Tammuz
        if hd.month == 4 and hd.day == 17 and hd.weekday() != 7:
            return True
        if hd.month == 4 and hd.day == 18 and hd.weekday() == 1 and PHebrewDate(hd.year, 4, 17).weekday() == 7:
            return True
        # Ta'anit Esther (advanced to Thursday if on Shabbat)
        adar_month = 13 if ((hd.year * 7 + 1) % 19) < 7 else 12
        if hd.month == adar_month and hd.day == 13 and hd.weekday() != 7:
            return True
        if hd.month == adar_month and hd.day == 11 and hd.weekday() == 5 and PHebrewDate(hd.year, adar_month, 13).weekday() == 7:
            return True
        # Tisha B'Av
        if hd.month == 5 and hd.day == 9 and hd.weekday() != 7:
            return True
        if hd.month == 5 and hd.day == 10 and hd.weekday() == 1 and PHebrewDate(hd.year, 5, 9).weekday() == 7:
            return True
        return False

    def _is_minor_fast(self, hd: PHebrewDate) -> bool:
        return self._check_is_fast(hd) and hd.month != 5

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        cfg = self.hass.data[DOMAIN]["config"]
        tz = ZoneInfo(cfg["tzname"])
        now_local = (now or dt_util.now()).astimezone(tz)
        today = now_local.date()

        diaspora = cfg.get("diaspora", True)

        def sunset_on(d: datetime.date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(tz)

        def sunrise_on(d: datetime.date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunrise().astimezone(tz)

        raw_dawn = sunrise_on(today) - timedelta(minutes=72)
        raw_sunset_today = sunset_on(today)
        raw_candle_cut = raw_sunset_today - timedelta(minutes=self._candle_offset)
        raw_havdalah_today = raw_sunset_today + timedelta(minutes=self._havdalah_offset)

        dawn = _round_half_up(raw_dawn)
        candle_cut = _round_half_up(raw_candle_cut)
        havdalah_today = _round_ceil(raw_havdalah_today)

        def is_yomtov(pydate: datetime.date) -> bool:
            try:
                return HDateInfo(pydate, diaspora=diaspora).is_yom_tov
            except Exception:
                return False

        effective_pydate = today if now_local < havdalah_today else today + timedelta(days=1)

        # --- Shabbos window (nearest current Shabbos), with rounding ---
        shabbos_eve = today
        while shabbos_eve.weekday() != 4:
            shabbos_eve -= timedelta(days=1)
        shabbos_day = shabbos_eve + timedelta(days=1)

        raw_shabbos_start = sunset_on(shabbos_eve) - timedelta(minutes=self._candle_offset)
        raw_shabbos_end = sunset_on(shabbos_day) + timedelta(minutes=self._havdalah_offset)

        shabbos_start = _round_half_up(raw_shabbos_start)
        shabbos_end = _round_ceil(raw_shabbos_end)

        # --- Festival window detection (rounded) ---
        dates_to_check = [today - timedelta(days=1), today, today + timedelta(days=1)]
        fest_dates = sorted(d for d in dates_to_check if is_yomtov(d))
        if fest_dates:
            start_date = fest_dates[0]
            end_date = fest_dates[-1]

            eve = start_date - timedelta(days=1)

            if eve.weekday() == 5:
                raw_fest_start = sunset_on(eve) + timedelta(minutes=self._havdalah_offset)
                fest_start = _round_ceil(raw_fest_start)
            else:
                raw_fest_start = sunset_on(eve) - timedelta(minutes=self._candle_offset)
                fest_start = _round_half_up(raw_fest_start)

            raw_fest_end = sunset_on(end_date) + timedelta(minutes=self._havdalah_offset)
            fest_end = _round_ceil(raw_fest_end)

            if fest_start <= now_local < fest_end:
                if shabbos_start <= now_local < shabbos_end:
                    if _is_chol_hamoed(shabbos_day):
                        state = "Shabbos & Chol Hamoed"
                    elif is_yomtov(shabbos_day):
                        state = "Shabbos & Yom Tov"
                    else:
                        state = "Shabbos"
                else:
                    state = "Yom Tov"
                self._set_state(state)
                return

            motzi_start = fest_end
            motzi_end = datetime.datetime.combine(
                end_date + timedelta(days=1), time(2, 0), tz
            )
            if motzi_start <= now_local < motzi_end:
                if _is_chol_hamoed(effective_pydate):
                    state = "Chol Hamoed"
                    if shabbos_start <= now_local < shabbos_end:
                        state = "Shabbos & Chol Hamoed"
                else:
                    if shabbos_start <= now_local < shabbos_end:
                        state = "Shabbos"
                    else:
                        state = "Motzi"
                self._set_state(state)
                return

        # --- Standard Motzi: only if yesterday was Yom Tov ---
        prev_date = today - timedelta(days=1)
        raw_motzi = is_yomtov(prev_date)
        raw_motzi_start = sunset_on(prev_date) + timedelta(minutes=self._havdalah_offset)
        motzi_start = _round_ceil(raw_motzi_start)
        motzi_end = datetime.datetime.combine(today, time(2, 0), tz)

        if raw_motzi and motzi_start <= now_local < motzi_end:
            if _is_chol_hamoed(effective_pydate):
                state = "Chol Hamoed"
                if shabbos_start <= now_local < shabbos_end:
                    state = "Shabbos & Chol Hamoed"
            else:
                if shabbos_start <= now_local < shabbos_end:
                    state = "Shabbos"
                else:
                    state = "Motzi"
            self._set_state(state)
            return

        # --- Erev: dawn → candlelighting for Friday or YT-eve ---
        is_yom_tom = is_yomtov(today + timedelta(days=1))
        is_fast_tomorrow = _is_fast_day(today + timedelta(days=1))
        is_shabbos_today = (today.weekday() == 5)

        if (
            dawn <= now_local < candle_cut
            and not is_fast_tomorrow
            and not is_shabbos_today
            and ((today.weekday() == 4 and not is_yomtov(today)) or is_yom_tom)
        ):
            self._set_state("Erev")
            return

        # --- Shabbos on Friday evening or Saturday day ---
        if shabbos_start <= now_local < shabbos_end:
            if _is_chol_hamoed(shabbos_day):
                state = "Shabbos & Chol Hamoed"
            elif is_yomtov(shabbos_day):
                state = "Shabbos & Yom Tov"
            else:
                state = "Shabbos"
            self._set_state(state)
            return

        # --- Chol Hamoed ---
        if _is_chol_hamoed(effective_pydate):
            self._set_state("Chol Hamoed")
            return

        # --- Fast days ---
        effective_pydate = today if now_local < havdalah_today else today + timedelta(days=1)
        effective_hd = PHebrewDate.from_pydate(effective_pydate)
        is_fast = self._check_is_fast(effective_hd)

        if is_fast:
            raw_end_time = sunset_on(effective_pydate) + timedelta(minutes=self._havdalah_offset)
            end_time = _round_ceil(raw_end_time)

            if self._is_minor_fast(effective_hd):
                start_time = datetime.datetime.combine(
                    effective_pydate, time(2, 0), tz
                )
            else:
                raw_start_time = sunset_on(effective_pydate - timedelta(days=1))
                start_time = _round_half_up(raw_start_time)

            if start_time <= now_local < end_time:
                if shabbos_start <= now_local < shabbos_end:
                    if _is_chol_hamoed(shabbos_day):
                        state = "Shabbos & Chol Hamoed"
                    elif is_yomtov(shabbos_day):
                        state = "Shabbos & Yom Tov"
                    else:
                        state = "Shabbos"
                    self._set_state(state)
                else:
                    self._set_state("Fast Day")
                return

        # --- Motzi on Saturday evening (plain Shabbos) ---
        motzi_end_shabbos = datetime.datetime.combine(
            shabbos_day + timedelta(days=1), time(2, 0), tz
        )
        if shabbos_end <= now_local < motzi_end_shabbos:
            self._set_state("Motzi")
            return

        self._set_state("Any Other Day")
