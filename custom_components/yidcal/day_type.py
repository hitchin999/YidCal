# custom_components/yidcal/day_type.py
from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity

from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

# Hebrew fast days names
FAST_DAYS = {
    "יום הכיפורים",
    "צום גדליה",
    "תענית אסתר",
    "צום עשרה בטבת",
    "צום שבעה עשר בתמוז",
    "תשעה באב",
    "תשעה באב נדחה",
}

# All possible states exposed
POSSIBLE_STATES = [
    "Any Other Day",
    "Erev",
    "Motzi",
    "Shabbos",
    "Yom Tov",
    "Shabbos & Yom Tov",
    "Fast Day",
]

class DayTypeSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Reports the current day type with precise windows:
       - Motzi: from yesterday's havdalah until 2:00 AM today
       - Erev: from alos (72m before sunrise) until candle-lighting
       - Shabbos: from Friday sunset-candle_offset until Saturday sunset+havdalah_offset
       - Yom Tov: from eve sunset-candle_offset until day sunset+havdalah_offset
       - Combined Shabbos & Yom Tov when both overlap
       - Fast Days: from alos until sunset+havdalah_offset (special Tish'a B'Av logic)
       - Otherwise: Any Other Day
       Exposes boolean attrs for each plus possible_states list."""

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
        # initialize attrs
        self._attr_extra_state_attributes = {
            "any_other_day": False,
            "erev": False,
            "motzi": False,
            "shabbos": False,
            "yom_tov": False,
            "shabbos_and_yom_tov": False,
            "fast_day": False,
            "possible_states": POSSIBLE_STATES,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._state = last.state or ""
            for k in self._attr_extra_state_attributes:
                if k != "possible_states":
                    self._attr_extra_state_attributes[k] = bool(last.attributes.get(k))
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()
        yesterday = today - timedelta(days=1)

        # setup location for sun times
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_today = sun(loc.observer, date=today, tzinfo=tz)
        dawn = sun_today["sunrise"] - timedelta(minutes=72)
        sunset_today = sun_today["sunset"]
        sun_yest = sun(loc.observer, date=yesterday, tzinfo=tz)
        sunset_yest = sun_yest["sunset"]

        # compute candle-lighting threshold for today
        candle = sunset_today - timedelta(minutes=self._candle_offset)

        # holiday name (only Yom Tov)
        hd_py    = PHebrewDate.from_pydate(today)
        hol_name = hd_py.holiday(hebrew=True) or ""

        # ─── manual fast-day detection ───
        # (all except 9 Av & deferred start at dawn; 9/10 Av & YK by candle-lighting)
        is_fast = False
        # Gedalia — 3 Tishrei
        if hd_py.month == 7 and hd_py.day == 3 and now >= dawn:
            is_fast = True
        # 10 Tevet
        elif hd_py.month == 10 and hd_py.day == 10 and now >= dawn:
            is_fast = True
        # 17 Tammuz
        elif hd_py.month == 4 and hd_py.day == 17 and now >= dawn:
            is_fast = True
        # Ta’anit Esther — 13 Adar
        elif hd_py.month in (12, 13) and hd_py.day == 13 and now >= dawn:
            is_fast = True
        # Yom Kippur, Tish’a B’Av & deferred
        elif hol_name in {"יום הכיפורים", "תשעה באב", "תשעה באב נדחה"}:
            
            # use candle-lighting from yesterday
            tb_start = sunset_yest - timedelta(minutes=self._candle_offset)
            if now >= tb_start:
                is_fast = True

        hd_today = HDateInfo(today, diaspora=True)
        is_yomtov_today = hd_today.is_yom_tov
        hd_tomorrow = HDateInfo(today + timedelta(days=1), diaspora=True)
        is_yomtov_tomorrow = hd_tomorrow.is_yom_tov

        # day-of-week
        wday = today.weekday()  # Mon=0 … Sun=6
        is_shabbos_today = (wday == 5)

        # raw flags
        raw_erev_shabbos = (wday == 4) and not is_yomtov_today
        raw_erev_holiday = is_yomtov_tomorrow and not is_shabbos_today
        raw_motzi_shabbos = (yesterday.weekday() == 5)
        raw_motzi_holiday = is_yomtov_today

        # windows
        in_motzi_window = (sunset_yest + timedelta(minutes=self._havdalah_offset) <= now <
                            datetime.datetime.combine(today, datetime.time(2, 0)).replace(tzinfo=tz))
        in_erev_window = (dawn <= now < candle)
        fast_start = (sunset_yest - timedelta(minutes=self._candle_offset)) if hol_name in {"תשעה באב", "תשעה באב נדחה"} else dawn
        in_fast_window = (fast_start <= now < sunset_today + timedelta(minutes=self._havdalah_offset) and is_fast)
        in_shabbos_window = ((sunset_yest - timedelta(minutes=self._candle_offset)) <= now < sunset_today + timedelta(minutes=self._havdalah_offset) and is_shabbos_today)
        in_yomtov_window = ((sunset_yest - timedelta(minutes=self._candle_offset)) <= now < sunset_today + timedelta(minutes=self._havdalah_offset) and is_yomtov_today)
        in_shabbos_and_yomtov = in_shabbos_window and in_yomtov_window

        # final flags
        in_erev = in_erev_window and (raw_erev_shabbos or raw_erev_holiday)
        in_motzi = in_motzi_window and (raw_motzi_shabbos or raw_motzi_holiday)

        # pick state with proper priority
        if in_motzi:
            state = "Motzi"
        elif in_erev:
            state = "Erev"
        elif in_shabbos_and_yomtov:
            state = "Shabbos & Yom Tov"
        elif in_shabbos_window:
            state = "Shabbos"
        elif in_yomtov_window:
            state = "Yom Tov"
        elif in_fast_window:
            state = "Fast Day"
        else:
            state = "Any Other Day"

        # write state + attrs
        self._state = state
        self._attr_extra_state_attributes = {
            "any_other_day": (state == "Any Other Day"),
            "erev": in_erev,
            "motzi": in_motzi,
            "shabbos": in_shabbos_window,
            "yom_tov": in_yomtov_window,
            "shabbos_and_yom_tov": in_shabbos_and_yomtov,
            "fast_day": in_fast_window,
            "possible_states": POSSIBLE_STATES,
        }

    @property
    def state(self) -> str:
        return self._state
