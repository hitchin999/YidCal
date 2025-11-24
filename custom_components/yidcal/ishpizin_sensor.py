from __future__ import annotations

import datetime
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .device import YidCalDisplayDevice
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

ISHPIZIN_NAMES = ["אברהם", "יצחק", "יעקב", "משה", "אהרן", "יוסף", "דוד"]
ISHPIZIN_STATES = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

WEEKDAYS_YI = ["מאנטאג","דינסטאג","מיטוואך","דאנערשטאג","פרייטאג","שבת קודש","זונטאג"]

# ---------------- Hebrew year formatting (5787 -> תשפ״ז) ---------------------
_GERESH = "\u05F3"; _GERSHAYIM = "\u05F4"
_UNITS = {1:"א",2:"ב",3:"ג",4:"ד",5:"ה",6:"ו",7:"ז",8:"ח",9:"ט"}
_TENS = {10:"י",20:"כ",30:"ל",40:"מ",50:"נ",60:"ס",70:"ע",80:"פ",90:"צ"}
_HUNDREDS = {100:"ק",200:"ר",300:"ש",400:"ת"}

def _hebrew_year_string(year: int) -> str:
    y = year % 1000
    parts: list[str] = []
    for h in (400,300,200,100):
        if y >= h:
            parts.append(_HUNDREDS[h]); y -= h
    if 10 <= y <= 19:
        if y == 15: parts.append("טו"); y = 0
        elif y == 16: parts.append("טז"); y = 0
        else: parts.append(_TENS[10]); y -= 10
    for t in (90,80,70,60,50,40,30,20,10):
        if y >= t: parts.append(_TENS[t]); y -= t; break
    if y in _UNITS: parts.append(_UNITS[y])
    s = "".join(parts)
    return s[:-1] + _GERSHAYIM + s[-1] if len(s) >= 2 else (s + _GERESH if s else s)

def _hebrew_day_label(i: int, diaspora: bool) -> str:
    """
    Label the 7 Sukkos nights (i=0..6).
    Galus: nights 0–1 are YT; 2–5 CH"M (א..ד); 6 = הושענא רבה.
    EY:    night 0 is YT; 1–5 CH"M (א..ה); 6 = הושענא רבה.
    """
    if diaspora:
        if i <= 1:
            return f"{('א','ב')[i]}׳ דיום טוב"
        if i == 6:
            return "הושענא רבה"
        # i = 2..5 -> א..ד
        return f"{('א','ב','ג','ד')[i-2]}׳ דחול המועד"
    else:
        if i == 0:
            return "א׳ דיום טוב"
        if i == 6:
            return "הושענא רבה"
        # i = 1..5 -> א..ה
        return f"{('א','ב','ג','ד','ה')[i-1]}׳ דחול המועד"

# ------------------------------- Sensor --------------------------------------

class IshpizinSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    _attr_icon = "mdi:account-group"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset   = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_unique_id = "yidcal_ishpizin"
        self.entity_id = "sensor.yidcal_ishpizin"
        self._attr_name = "Ishpizin"
        self._attr_native_value = ""
        self._attr_extra_state_attributes = {f"אושפיזא ד{name}": False for name in ISHPIZIN_NAMES}

        cfg = hass.data[DOMAIN]["config"]
        self._diaspora: bool = cfg.get("diaspora", True)  # <-- NEW

    @property
    def options(self) -> list[str]:
        return ISHPIZIN_STATES

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ISHPIZIN_STATES:
            self._attr_native_value = last.state
            for key in self._attr_extra_state_attributes:
                if key in last.attributes:
                    self._attr_extra_state_attributes[key] = last.attributes.get(key, False)
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        today = now.date()
        heb_year_now = PHebrewDate.from_pydate(today).year

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Flip schedule to NEXT YEAR after Motza'ei Simchas Torah:
        #   galus → 23 Tishrei; EY → 22 Tishrei
        st_day = 23 if self._diaspora else 22
        st_gdate = PHebrewDate(heb_year_now, 7, st_day).to_pydate()
        st_sun = sun(loc.observer, date=st_gdate, tzinfo=tz)
        motzaei_st = st_sun["sunset"] + timedelta(minutes=self._havdalah_offset)
        schedule_year = heb_year_now + 1 if now >= motzaei_st else heb_year_now

        attrs: dict[str, object] = {f"אושפיזא ד{name}": False for name in ISHPIZIN_NAMES}
        lines: list[str] = []
        active_state = ""

        for i, name in enumerate(ISHPIZIN_NAMES):
            # 15–21 Tishrei nights for the displayed schedule year
            gdate = PHebrewDate(schedule_year, 7, 15 + i).to_pydate()
            prev_gdate = gdate - timedelta(days=1)

            weekday_yi = WEEKDAYS_YI[gdate.weekday()]
            label = _hebrew_day_label(i, self._diaspora)  # <-- UPDATED

            lines.append(f"{weekday_yi} {label}:\nאושפיזא ד{name}.")

            # Active window for *current* year's state (not the displayed schedule):
            s_prev = sun(loc.observer, date=prev_gdate, tzinfo=tz)
            # Night 1 begins at candle-lighting (unless Erev Sukkos is Shabbos → then start at havdalah)
            if i == 0:
                if prev_gdate.weekday() == 5:  # Erev Sukkos fell on Shabbos
                    start = s_prev["sunset"] + timedelta(minutes=self._havdalah_offset)
                else:
                    start = s_prev["sunset"] - timedelta(minutes=self._candle_offset)
            else:
                # Nights 2–7 start at tzeis
                start = s_prev["sunset"] + timedelta(minutes=self._havdalah_offset)
            s_curr = sun(loc.observer, date=gdate, tzinfo=tz)
            end = s_curr["sunset"] + timedelta(minutes=self._havdalah_offset)

            if schedule_year == heb_year_now and start <= now < end:
                active_state = f"אושפיזא ד{name}"
                attrs[active_state] = True

        self._attr_native_value = active_state if active_state in ISHPIZIN_STATES else ""
        self._attr_name = "Ishpizin"
        attrs["די סקעדזשועל איז פאר יאר"] = _hebrew_year_string(schedule_year)
        attrs["Ishpizin Schedule"] = "\n\n".join(lines)
        attrs["Possible states"] = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

        self._attr_extra_state_attributes = attrs
