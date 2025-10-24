# holiday_sensor.py
"""
Separate HolidaySensor for YidCal integration.
Handles Jewish holidays, fast days, and custom periods with time-aware logic,
restores its last state across reboots, and filters the visible state
through a whitelist while still exposing all flags.
"""

from __future__ import annotations
import datetime
import math
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging
from .device import YidCalDevice

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation
from hdate import HDateInfo
from hdate.translator import set_language
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from pyluach.parshios import getparsha_string

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)

class HolidaySensor(YidCalDevice, RestoreEntity, SensorEntity):
    """
    Tracks Jewish holidays, fasts, and custom periods with time-aware logic.
    - Restores its last visible state on reboot
    - Exposes ALL holiday flags as attributes
    - Uses ALLOWED_HOLIDAYS to pick exactly one for its state
    """
    _attr_name = "Holiday"
    _attr_icon = "mdi:calendar-star"
    _attr_device_class = "enum"
    
    FAST_FLAGS = [
        "יום הכיפורים",
        "צום גדליה",
        "תענית אסתר",
        "צום עשרה בטבת",
        "צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה",
    ]

    # ─── THE FULL SET of every holiday you detect (for attributes) ───
    ALL_HOLIDAYS: list[str] = [
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",
        "ראש השנה ב׳",
        "ראש השנה א׳ וב׳",
        "מוצאי ראש השנה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות",
        "סוכות א׳",
        "סוכות ב׳",
        "סוכות א׳ וב׳",
        "א׳ דחול המועד סוכות",
        "ב׳ דחול המועד סוכות",
        "ג׳ דחול המועד סוכות",
        "ד׳ דחול המועד סוכות",
        "חול המועד סוכות",
        "שבת חול המועד סוכות",
        "הושענא רבה",
        "שמיני עצרת",
        "שמחת תורה",
        "שמיני עצרת/שמחת תורה",
        "מוצאי סוכות",
        "אסרו חג סוכות",
        "ערב חנוכה",
        "חנוכה",
        "זאת חנוכה",
        "שובבים",
        "שובבים ת\"ת",
        "צום עשרה בטבת",
        "ט\"ו בשבט",
        "תענית אסתר",
        "פורים",
        "שושן פורים",
        "ליל בדיקת חמץ",
        "ערב פסח מוקדם",
        "שבת ערב פסח",
        "ערב פסח",
        "פסח",
        "פסח א׳",
        "פסח ב׳",
        "פסח א׳ וב׳",
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "חול המועד פסח",
        "שבת חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "שביעי/אחרון של פסח",
        "מוצאי פסח",
        "אסרו חג פסח",
        "פסח שני",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",
        "שבועות ב׳",
        "שבועות א׳ וב׳",
        "מוצאי שבועות",
        "אסרו חג שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "ערב תשעה באב",
        "תשעה באב",
        "תשעה באב נדחה",
        "מוצאי תשעה באב",
        "יום כיפור קטן",
        "ראש חודש",
        "שבת ראש חודש",
    ]

    # ─── Only these may become the sensor.state ───
    ALLOWED_HOLIDAYS: list[str] = [
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",   # displayed as: א׳ דראש השנה
        "ראש השנה ב׳",   # displayed as: ב׳ דראש השנה
        "מוצאי ראש השנה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות א׳",      # displayed as: א׳ דסוכות
        "סוכות ב׳",      # displayed as: ב׳ דסוכות
        "א׳ דחול המועד סוכות",
        "ב׳ דחול המועד סוכות",
        "ג׳ דחול המועד סוכות",
        "ד׳ דחול המועד סוכות",
        "הושענא רבה",
        "שמיני עצרת",
        "שמחת תורה",
        "מוצאי סוכות",
        "אסרו חג סוכות",
        "ערב חנוכה",
        "חנוכה",
        "זאת חנוכה",
        "צום עשרה בטבת",
        "ט\"ו בשבט",
        "תענית אסתר",
        "פורים",
        "שושן פורים",
        "ליל בדיקת חמץ",
        "ערב פסח",
        "פסח א׳",        # displayed as: א׳ דפסח
        "פסח ב׳",        # displayed as: ב׳ דפסח
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "מוצאי פסח",
        "אסרו חג פסח",
        "פסח שני",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",     # displayed as: א׳ דשבועות
        "שבועות ב׳",     # displayed as: ב׳ דשבועות
        "מוצאי שבועות",
        "אסרו חג שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "ערב תשעה באב",
        "תשעה באב",
        "תשעה באב נדחה",
        "מוצאי תשעה באב",
        "יום כיפור קטן",
    ]

    # ─── Window‐type map: holiday‑name → named window key ───────────
    WINDOW_TYPE: dict[str, str] = {
        "א׳ סליחות":                     "havdalah_candle",
        "ערב ראש השנה":                  "alos_candle",
        "ראש השנה א׳":                   "candle_havdalah",
        "ראש השנה ב׳":                   "havdalah_havdalah",
        "ראש השנה א׳ וב׳":                "candle_both",
        "צום גדליה":                      "alos_havdalah",
        "שלוש עשרה מדות":                 "alos_candle",
        "ערב יום כיפור":                   "candle_candle",
        "יום הכיפורים":                    "candle_havdalah",
        "ערב סוכות":                      "alos_candle",
        "סוכות א׳":                       "candle_havdalah",
        "סוכות ב׳":                       "havdalah_havdalah",
        "סוכות א׳ וב׳":                    "candle_both",
        "א׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ב׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ג׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ד׳ דחול המועד סוכות":               "havdalah_havdalah",
        "חול המועד סוכות":                  "havdalah_havdalah",
        "הושענא רבה":                     "havdalah_candle",
        "שמיני עצרת":                      "candle_havdalah",
        "שמחת תורה":                     "havdalah_havdalah",
        "אסרו חג סוכות":                   "havdalah_havdalah",
        "ערב חנוכה":                      "alos_havdalah",
        "חנוכה":                         "havdalah_havdalah",
        "זאת חנוכה":                      "havdalah_havdalah",
        "שובבים":                        "havdalah_havdalah",
        "שובבים ת\"ת":                   "havdalah_havdalah",
        "צום עשרה בטבת":                 "alos_havdalah",
        "ט\"ו בשבט":                     "havdalah_havdalah",
        "תענית אסתר":                     "alos_havdalah",
        "פורים":                         "havdalah_havdalah",
        "שושן פורים":                     "havdalah_havdalah",
        "ליל בדיקת חמץ":                   "candle_alos",
        "ערב פסח מוקדם":                  "alos_candle",
        "שבת ערב פסח":                   "candle_havdalah",
        "ערב פסח":                       "alos_candle",
        "פסח א׳":                        "candle_havdalah",
        "פסח ב׳":                        "havdalah_havdalah",
        "פסח א׳ וב׳":                     "candle_both",
        "א׳ דחול המועד פסח":                "havdalah_havdalah",
        "ב׳ דחול המועד פסח":                "havdalah_havdalah",
        "ג׳ דחול המועד פסח":                "havdalah_havdalah",
        "ד׳ דחול המועד פסח":                "havdalah_havdalah",
        "חול המועד פסח":                  "havdalah_candle",
        "שביעי של פסח":                   "candle_havdalah",
        "אחרון של פסח":                   "havdalah_havdalah",
        "אסרו חג פסח":                    "havdalah_havdalah",
        "פסח שני":                       "havdalah_havdalah",
        "ל\"ג בעומר":                    "havdalah_havdalah",
        "ערב שבועות":                    "alos_candle",
        "שבועות א׳":                     "candle_havdalah",
        "שבועות ב׳":                     "havdalah_havdalah",
        "שבועות א׳ וב׳":                  "candle_both",
        "אסרו חג שבועות":                "havdalah_havdalah",
        "צום שבעה עשר בתמוז":             "alos_havdalah",
        "ערב תשעה באב":                 "alos_havdalah",
        "תשעה באב":                    "candle_havdalah",
        "תשעה באב נדחה":                "candle_havdalah",
        "יום כיפור קטן":                  "alos_havdalah",
        "ראש חודש":                    "havdalah_havdalah",
    }
    
    @staticmethod
    def _base_attrs() -> dict[str, bool | str | list[str]]:
        """Fresh attributes dict with all flags False + countdowns. (No 'Possible states' here.)"""
        attrs = {name: False for name in HolidaySensor.ALL_HOLIDAYS}
        attrs["מען פאַסט אויס און"] = ""  # fast ends in
        attrs["מען פאַסט אַן און"] = ""   # fast starts in
        return attrs

    # Two-day chagim where we want "א׳ ד<שם>" instead of "<שם> א׳"
    _TWO_DAY_BASES = {"ראש השנה", "סוכות", "פסח", "שבועות"}
    
    @staticmethod
    def _flip_two_day_format(name: str) -> str:
        """
        Turn '<base> א׳/ב׳' into 'א׳/ב׳ ד<base>' but only for bases in _TWO_DAY_BASES.
        Leaves anything else unchanged.
        """
        # Hebrew geresh is U+05F3 (׳). Your strings already use it, so match that.
        if name.endswith(" א׳") or name.endswith(" ב׳"):
            base = name[:-3]           # strip space + letter + geresh
            day_letter = name[-2]      # 'א' or 'ב'
            if base in HolidaySensor._TWO_DAY_BASES:
                return f"{day_letter}׳ ד{base}"
        return name

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "holiday"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        # initial state + full attrs
        self._attr_native_value: str = ""
        self._attr_extra_state_attributes: dict[str, bool | str] = {}

        # Hebrew names
        set_language("he")

    async def async_added_to_hass(self) -> None:
        # Restore last state/attributes on startup
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
    
        # Always start from a clean base that includes "Possible states"
        attrs = self._base_attrs()
    
        if last:
            # Merge back only known flags + countdowns (ignore unknown keys)
            for k, v in (last.attributes or {}).items():
                if k in self.ALL_HOLIDAYS or k in ("מען פאַסט אויס און", "מען פאַסט אַן און"):
                    attrs[k] = v
            self._attr_native_value = last.state or ""
        else:
            self._attr_native_value = ""
            
        attrs["Possible states"] = [self._flip_two_day_format(n) for n in self.ALLOWED_HOLIDAYS]
        self._attr_extra_state_attributes = attrs
    
        # schedule minute‐interval updates via base‐class wrapper
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, bool | str]:
        return self._attr_extra_state_attributes
        
    @property
    def options(self) -> list[str]:
        """Return list of possible values for Home Assistant automation UI."""
        # Preserve the original ALLOWED_HOLIDAYS order, but render each through the flip
        opts = []
        seen = set()
        for n in self.ALLOWED_HOLIDAYS:
            m = self._flip_two_day_format(n)
            if m not in seen:
                opts.append(m)
                seen.add(m)
    
        # Include empty state at the end
        return opts + [""]

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None:
            return

        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        actual_date = now.date()
        wd = now.weekday()
        geo = await get_geo(self.hass)
        cal = ZmanimCalendar(geo_location=geo, date=actual_date)
        actual_sunset = cal.sunset().astimezone(tz)

        # Compute roll‐points
        candle_cut = actual_sunset - timedelta(minutes=self._candle_offset)
        havdalah_cut = actual_sunset + timedelta(minutes=self._havdalah_offset)

        # festival_date rolls at havdalah
        if now >= havdalah_cut:
            festival_date = actual_date + timedelta(days=1)
        else:
            festival_date = actual_date

        wd_fest = wd if festival_date == actual_date else (wd + 1) % 7

        # detect_date rolls at candle‐lighting
        if now >= candle_cut:
            detect_date = actual_date + timedelta(days=1)
        else:
            detect_date = actual_date

        wd_py = wd if detect_date == actual_date else (wd + 1) % 7
        # Sunset rolling for sunset-start events
        sunset_cut = actual_sunset
        sunset_detect_date = actual_date + timedelta(days=1) if now >= sunset_cut else actual_date
        hd_sunset = PHebrewDate.from_pydate(sunset_detect_date)
        wd_sunset = wd if sunset_detect_date == actual_date else (wd + 1) % 7
        # Anchor sunsets around festival_date
        cal_fest = ZmanimCalendar(geo_location=geo, date=festival_date)
        prev_cal = ZmanimCalendar(geo_location=geo, date=festival_date - timedelta(days=1))
        next_cal = ZmanimCalendar(geo_location=geo, date=festival_date + timedelta(days=1))
        prev_sunset = prev_cal.sunset().astimezone(tz)
        festival_sunset = cal_fest.sunset().astimezone(tz)
        next_sunset = next_cal.sunset().astimezone(tz)
        tomorrow_cal = ZmanimCalendar(geo_location=geo, date=actual_date + timedelta(days=1))
        tomorrow_sunset = tomorrow_cal.sunset().astimezone(tz)
        # Align dawn with the festival day for consistent daytime windows
        dawn = cal_fest.sunrise().astimezone(tz) - timedelta(minutes=72)
        # Round dawn as per zman_alos.py
        if dawn.second >= 30:
            dawn += timedelta(minutes=1)
        dawn = dawn.replace(second=0, microsecond=0)
        #_LOGGER.debug(f"Dawn: {dawn}, now: {now}, festival_date: {festival_date}")
        
        # Hebrew dates
        hd_py = PHebrewDate.from_pydate(detect_date)
        hd_fest = PHebrewDate.from_pydate(festival_date)
        hd_py_fast = PHebrewDate.from_pydate(actual_date)
        havdalah_date = actual_date + timedelta(days=1) if now >= havdalah_cut else actual_date
        hd_havdalah = PHebrewDate.from_pydate(havdalah_date)
        # Special case for Bedikat Chametz (deferred to 13 Nisan if Erev Pesach on Shabbat)
        hd_erev_pesach = PHebrewDate(hd_py.year, 1, 14)
        erev_greg = hd_erev_pesach.to_pydate()
        bedikat_day = 13 if erev_greg.weekday() == 5 else 14
        is_bedikat_day = (hd_py.month == 1 and hd_py.day == bedikat_day)
        #_LOGGER.debug(f"Bedikat: prev_sunset={prev_sunset}, dawn={dawn}, now={now}, is_bedikat_day={is_bedikat_day}")
        is_erev_pesach_on_shabbos = (erev_greg.weekday() == 5)  # 14 Nisan is Shabbat

        # Debug Hebrew date
        #_LOGGER.debug(f"Current time: {now}, Hebrew date (hd_py): {hd_py.month}/{hd_py.day}, "
        #              f"hd_fest: {hd_fest.month}/{hd_fest.day}, hd_py_fast: {hd_py_fast.month}/{hd_py_fast.day}")

        # Build windows
        candle_havdalah_start, candle_havdalah_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            festival_sunset + timedelta(minutes=self._havdalah_offset),
        )
        candle_both_start, candle_both_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            next_sunset + timedelta(minutes=self._havdalah_offset),
        )
        alos_havdalah_start, alos_havdalah_end = (
            dawn,
            festival_sunset + timedelta(minutes=self._havdalah_offset),
        )
        alos_candle_start, alos_candle_end = (
            dawn,
            festival_sunset - timedelta(minutes=self._candle_offset),
        )
        candle_alos_start, candle_alos_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            dawn,
        )
        shabbat_second = festival_date.weekday() == 5
        if shabbat_second:
            havdalah_havdalah_start = prev_sunset - timedelta(minutes=self._candle_offset)
        else:
            havdalah_havdalah_start = prev_sunset + timedelta(minutes=self._havdalah_offset)
        havdalah_havdalah_end = festival_sunset + timedelta(minutes=self._havdalah_offset)
        havdalah_candle_start, havdalah_candle_end = (
            prev_sunset - timedelta(minutes=self._havdalah_offset),
            festival_sunset - timedelta(minutes=self._candle_offset),
        )
        candle_candle_start, candle_candle_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            tomorrow_sunset - timedelta(minutes=self._candle_offset),
        )

        # leap-year for Shovavim
        year = hd_py.year
        is_leap = ((year * 7 + 1) % 19) < 7

        #Tzom Gedalye Deferred
        h_year = year if hd_py.month >= 7 else year + 1
        gedaliah_day = 3
        tishrei_3_greg = PHebrewDate(h_year, 7, 3).to_pydate()
        if tishrei_3_greg.weekday() == 5:
            gedaliah_day = 4

        av9_greg = PHebrewDate(hd_fest.year, 5, 9).to_pydate()
        is_tisha_on_shabbat = av9_greg.weekday() == 5

        # ─── Fast start/end times
        # Default for regular fasts
        start_time_fast = dawn
        end_time = actual_sunset + timedelta(minutes=self._havdalah_offset)

        # Force default for minor fasts to prevent extension
        if (hd_py_fast.month == 7 and hd_py_fast.day == gedaliah_day) or \
           (hd_py_fast.month == 10 and hd_py_fast.day == 10) or \
           (hd_py_fast.month == 4 and hd_py_fast.day == 17) or \
           (hd_py.month in (12, 13) and hd_py.day == 13):
            start_time_fast = dawn
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset)
        # Override for 25-hour fasts and their Erev
        elif hd_py.month == 7 and hd_py.day == 9:  # Erev Yom Kippur
            start_time_fast = candle_cut
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_py.month == 7 and hd_py.day == 10:  # Yom Kippur
            start_time_fast = prev_sunset - timedelta(minutes=self._candle_offset)
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if detect_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 8 and now < actual_sunset:  # Erev Tisha B'Av
            start_time_fast = actual_sunset
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and (hd_fest.day == 8 and now >= actual_sunset or hd_fest.day == 9):  # Tisha B'Av
            start_time_fast = prev_sunset if hd_fest.day == 9 else actual_sunset
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if hd_fest.day == 9 and festival_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 10 and wd_fest == 6:  # Deferred Tisha B'Av day
            start_time_fast = prev_sunset
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if festival_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 9 and wd_fest == 5:  # Erev for Deferred Tisha B'Av (Av 9 on Shabbat)
            start_time_fast = actual_sunset
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)

        #_LOGGER.debug(f"Fast times: start_time_fast={start_time_fast}, end_time={end_time}, now={now}")

        # Build raw attrs (always includes 'Possible states')
        attrs = self._base_attrs()

        # Alef Slichos
        if hd_py.month == 6 and 21 <= hd_py.day <= 26 and wd_py == 6:
            attrs["א׳ סליחות"] = True
        # Erev Rosh Hashanah
        if hd_py.month == 6 and hd_py.day == 29:
            attrs["ערב ראש השנה"] = True

        # Rosh Hashanah
        if hd_py.month == 7 and hd_py.day == 1 or hd_fest.month == 7 and hd_fest.day == 1:
            attrs["ראש השנה א׳"] = True
            attrs["ראש השנה א׳ וב׳"] = True
        if hd_fest.month == 7 and hd_fest.day == 2 or hd_havdalah.month == 7 and hd_havdalah.day == 2:
            attrs["ראש השנה ב׳"] = True
            attrs["ראש השנה א׳ וב׳"] = True
        # Tzom Gedaliah
        if hd_py_fast.month == 7 and hd_py_fast.day == gedaliah_day and dawn <= now <= end_time:
            attrs["צום גדליה"] = True

        if hd_py.month == 7 and ((hd_py.day == 8 and wd_py in [0, 1, 3]) or (hd_py.day == 6 and wd_py == 3)):
            attrs["שלוש עשרה מדות"] = True

        # Yom Kippur
        if hd_py.month == 7 and hd_py.day == 9:
            attrs["ערב יום כיפור"] = True
        if (hd_py.month == 7 and hd_py.day == 10) or (hd_fest.month == 7 and hd_fest.day == 10):
            attrs["יום הכיפורים"] = True

        # Sukkot
        if hd_py.month == 7:
            if hd_py.day == 14:
                attrs["ערב סוכות"] = True
            if (hd_py.day == 15) or (hd_fest.month == 7 and hd_fest.day == 15):
                attrs["סוכות א׳"] = True
                attrs["סוכות א׳ וב׳"] = True
            if (hd_fest.month == 7 and hd_fest.day == 16) or (hd_havdalah.month == 7 and hd_havdalah.day == 16):
                attrs["סוכות ב׳"] = True
                attrs["סוכות א׳ וב׳"] = True
            if hd_fest.day == 17:
                attrs["א׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_fest.day == 18:
                attrs["ב׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_fest.day == 19:
                attrs["ג׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_fest.day == 20:
                attrs["ד׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_fest.day == 21:
                attrs["הושענא רבה"] = True
            if (hd_py.month == 7 and hd_py.day == 22) or (hd_fest.month == 7 and hd_fest.day == 22):
                attrs["שמיני עצרת"] = True
            if (hd_py.month == 7 and hd_py.day == 23) or (hd_fest.month == 7 and hd_fest.day == 23):
                attrs["שמחת תורה"] = True
            if hd_fest.day == 24:
                attrs["אסרו חג סוכות"] = True

        # Chanukah & Erev at dawn (Kislev 9) and Zot Chanukah (Tevet 10)
        if hd_fest.month == 9:
            if hd_fest.day == 24:
                attrs["ערב חנוכה"] = True
            if 25 <= hd_fest.day <= 30:
                attrs["חנוכה"] = True
        elif hd_fest.month == 10:
            if hd_fest.day == 1:
                attrs["חנוכה"] = True
            if hd_fest.day == 2:
                attrs["חנוכה"] = True
                attrs["זאת חנוכה"] = True

        # Shovavim
        parsha = (getparsha_string(hd_fest) or "").upper()
        #_LOGGER.debug(f"Current parsha: {parsha}")
        shov_base = ["SHEMOS", "VA'EIRA", "BO", "BESHALACH", "YISRO", "MISHPATIM"]
        shov_ext  = shov_base + ["TERUMAH", "TETZAVEH"]
        
        # Shovavim: always on for base weeks; in leap years also on for Terumah/Tetzaveh
        attrs["שובבים"] = (parsha in shov_base) or (is_leap and parsha in shov_ext)
        
        # Shovavim TAT: only in leap years (base + Terumah/Tetzaveh)
        attrs["שובבים ת\"ת"] = is_leap and (parsha in shov_ext)

        # Tzom Tevet
        if hd_py_fast.month == 10 and hd_py_fast.day == 10 and dawn <= now <= end_time:
            attrs["צום עשרה בטבת"] = True

        # Tu BiShvat
        if hd_fest.month == 11 and hd_fest.day == 15:
            attrs["ט\"ו בשבט"] = True

        # Purim
        if hd_fest.month in (12, 13):
            if hd_fest.day == 13 and dawn <= now <= end_time:
                attrs["תענית אסתר"] = True
            if hd_fest.day == 14:
                attrs["פורים"] = True
            if hd_fest.day == 15:
                attrs["שושן פורים"] = True

        # Bedikat Chametz
        if is_bedikat_day:
            if prev_sunset <= now < dawn:
                attrs["ליל בדיקת חמץ"] = True

        # Pesach & Erev
        if hd_py.month == 1:
            # 1) Friday before when Erev Pesach falls on Shabbos
            if is_erev_pesach_on_shabbos and (hd_py.month == 1 and hd_py.day == 13):
                attrs["ערב פסח מוקדם"] = True
            
            # 2) The Shabbos that *is* Erev Pesach (halachic Shabbos via wd_fest/hd_fest)
            if is_erev_pesach_on_shabbos and (wd_fest == 5 and hd_fest.month == 1 and hd_fest.day == 14):
                attrs["שבת ערב פסח"] = True
            # Turn on Erev Pesach on 14 Nisan (normal) OR on 13 Nisan when 14 falls on Shabbos (מוקדם)
            if (hd_py.day == 14) or (is_erev_pesach_on_shabbos and hd_py.day == 13):
                attrs["ערב פסח"] = True
            if (hd_py.day == 15) or (hd_fest.month == 1 and hd_fest.day == 15):
                attrs["פסח א׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if (hd_fest.month == 1 and hd_fest.day == 16) or (hd_havdalah.month == 1 and hd_havdalah.day == 16):
                attrs["פסח ב׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if hd_fest.day == 17:
                attrs["א׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_fest.day == 18:
                attrs["ב׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_fest.day == 19:
                attrs["ג׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_fest.day == 20:
                attrs["ד׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if (hd_py.month == 1 and hd_py.day == 21) or (hd_fest.month == 1 and hd_fest.day == 21):
                attrs["שביעי של פסח"] = True
            if (hd_py.month == 1 and hd_py.day == 22) or (hd_fest.month == 1 and hd_fest.day == 22):
                attrs["אחרון של פסח"] = True
            if hd_fest.day == 23:
                attrs["אסרו חג פסח"] = True

        # Pesach Sheini & Lag BaOmer
        if hd_fest.month == 2:
            if hd_fest.day == 14:
                attrs["פסח שני"] = True
            if hd_fest.day == 18:
                attrs["ל\"ג בעומר"] = True

        # Shavuot & Erev
        if hd_py.month == 3:
            if hd_py.day == 5:
                attrs["ערב שבועות"] = True
            if hd_py.day == 6 or hd_fest.month == 3 and hd_fest.day == 6:
                attrs["שבועות א׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            if hd_fest.month == 3 and hd_fest.day == 7 or hd_havdalah.month == 3 and hd_havdalah.day == 7:
                attrs["שבועות ב׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            if hd_fest.day == 8:
                attrs["אסרו חג שבועות"] = True

        # Rosh Chodesh (but not on Rosh Hashanah, Tishrei 1)
        if hd_fest.day in (1, 30) and not (hd_fest.month == 7 and hd_fest.day == 1):
            attrs["ראש חודש"] = True

        # Tzom Shiva Usor Betamuz
        if hd_py_fast.month == 4 and hd_py_fast.day == 17 and dawn <= now <= end_time:
            attrs["צום שבעה עשר בתמוז"] = True

        # Fixed: Erev Tisha B’Av with extension to sunset and deferred handling
        if (hd_sunset.month == 5 and hd_sunset.day == 8 and not is_tisha_on_shabbat) or \
           (hd_sunset.month == 5 and hd_sunset.day == 9 and is_tisha_on_shabbat):
            attrs["ערב תשעה באב"] = True

        # Fixed: Tisha B’Av proper - use hd_sunset to prevent early turn-on
        if (hd_sunset.month == 5 and hd_sunset.day == 9) or (hd_fest.month == 5 and hd_fest.day == 9):
            attrs["תשעה באב"] = True
            attrs["ערב תשעה באב"] = False  # Unset Erev after fast starts

        # Fixed: Deferred Tisha B’Av - use hd_sunset to prevent late turn-on
        if hd_sunset.month == 5 and hd_sunset.day == 10 and wd_sunset == 6 and start_time_fast <= now <= end_time:
            attrs["תשעה באב נדחה"] = True
            attrs["תשעה באב"] = False
            
        # ─── Yom Kippur Katan (only for Erev Rosh Chodesh Elul) ─────────────
        elul_year = hd_fest.year  # Hebrew year reference
        # First day of RC Elul = 30 Av
        av30 = PHebrewDate(elul_year, 5, 30)
        av30_wd = av30.to_pydate().weekday()  # Mon=0 .. Sun=6

        # Default = 29 Av
        if av30_wd == 5:        # If RC Elul starts Shabbos
            ykk_av_day = 28     # move back to Thu 28 Av
        elif av30_wd == 6:      # If RC Elul starts Sunday
            ykk_av_day = 27     # move back to Thu 27 Av
        else:
            ykk_av_day = 29     # normal case (also covers Fri start → Thu 29 Av)

        if hd_fest.month == 5 and hd_fest.day == ykk_av_day:
            attrs["יום כיפור קטן"] = True

        # ─── Countdown for fast starts in (6 hours before fast)
        is_fast_day = (
            (hd_py_fast.month == 7 and hd_py_fast.day == gedaliah_day) or  # Tzom Gedaliah
            (hd_py.month == 7 and hd_py.day == 10) or          # Yom Kippur
            (hd_py_fast.month == 10 and hd_py_fast.day == 10) or  # Tzom Tevet
            (hd_py_fast.month == 4 and hd_py_fast.day == 17) or  # 17 Tammuz
            (hd_py.month == 12 and hd_py.day == 13) or         # Ta'anit Esther
            (hd_fest.month == 5 and hd_fest.day == 8 and now < actual_sunset) or  # Erev Tisha B'Av
            (hd_fest.month == 5 and hd_fest.day == 9) or       # Tisha B'Av
            (hd_fest.month == 5 and hd_fest.day == 9 and wd_fest == 5) or  # Erev for Deferred Tisha B'Av
            (hd_fest.month == 5 and hd_fest.day == 10 and wd_fest == 6) or  # Deferred Tisha B'Av
            (hd_py.month == 7 and hd_py.day == 9)              # Erev Yom Kippur
        )
        if is_fast_day and now >= start_time_fast - timedelta(hours=6) and now < start_time_fast:
            remaining_sec = max(0, (start_time_fast - now).total_seconds())
            minutes_remaining = math.ceil(remaining_sec / 60)
            h = minutes_remaining // 60
            m = minutes_remaining % 60
            attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
        else:
            attrs["מען פאַסט אַן און"] = ""

        # Fix for pre-fast countdown on evening before minor dawn-start fasts (safe & halachic-day aligned)
        minor_fast_dates = [
            (7, gedaliah_day),        # Tzom Gedaliah (handles deferred to 4 Tishrei)
            (10, 10),                 # 10 Tevet
            (4, 17),                  # 17 Tammuz
            (13 if is_leap else 12, 13),  # Ta'anit Esther (Adar II in leap years)
        ]

        # Remember if a pre-fast countdown was already set (e.g., Erev YK / Tish'a)
        prefast_already = bool(attrs["מען פאַסט אַן און"])

        # Use the next morning's halachic date (already rolls at havdalah)
        next_halachic_date = festival_date
        hd_tomorrow = PHebrewDate.from_pydate(next_halachic_date)

        # Is the *next halachic day* a minor dawn-start fast?
        is_pre_minor_fast = any(hd_tomorrow.month == m and hd_tomorrow.day == d for m, d in minor_fast_dates)

        # Compute next dawn for the halachic next day (rounding consistent with earlier logic)
        tomorrow_cal = ZmanimCalendar(geo_location=geo, date=next_halachic_date)
        next_dawn = tomorrow_cal.sunrise().astimezone(tz) - timedelta(minutes=72)
        if next_dawn.second >= 30:
            next_dawn += timedelta(minutes=1)
        next_dawn = next_dawn.replace(second=0, microsecond=0)

        # Do not show a new "starts in" countdown if any fast flag is currently active.
        # This prevents the countdown from reappearing right after a fast ends.
        no_fast_active_now = not any(attrs.get(f) for f in self.FAST_FLAGS)

        # Only manage the minor-fast pre-window here; never clobber a countdown that was already set earlier
        if no_fast_active_now and is_pre_minor_fast and (next_dawn - timedelta(hours=6)) <= now < next_dawn:
            remaining_sec = max(0, (next_dawn - now).total_seconds())
            minutes_remaining = math.ceil(remaining_sec / 60)
            h = minutes_remaining // 60
            m = minutes_remaining % 60
            attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
        elif not prefast_already:
            # Clear only if we didn't already set a (non-minor) pre-fast countdown above (e.g., Erev YK)
            attrs["מען פאַסט אַן און"] = ""

        # Dynamic window overrides for the generic Chol HaMo'ed flags
        # Keep them continuous (havdalah→havdalah) on ordinary CH"M days,
        # but cut at candle time on the last CH"M day (Erev YT).
        def _dynamic_window(name: str, default_w: str | None) -> str | None:
            if name == "חול המועד סוכות":
                return "havdalah_havdalah"  # 17–20 Tishrei continuous
            if name == "חול המועד פסח":
                # last CH"M day is 20 Nisan → cut at candle that evening
                return "havdalah_candle" if (hd_fest.month == 1 and hd_fest.day == 20) else "havdalah_havdalah"
            return default_w

        # Filter attrs by windows
        for name, on in list(attrs.items()):
            if not on:
                continue
            w = _dynamic_window(name, self.WINDOW_TYPE.get(name))
            if w == "candle_havdalah" and not (candle_havdalah_start <= now <= candle_havdalah_end):
                attrs[name] = False
            elif w == "havdalah_havdalah" and not (havdalah_havdalah_start <= now <= havdalah_havdalah_end):
                attrs[name] = False
            elif w == "alos_havdalah" and not (alos_havdalah_start <= now <= alos_havdalah_end):
                attrs[name] = False
            elif w == "alos_candle" and not (alos_candle_start <= now <= alos_candle_end):
                attrs[name] = False
            elif w == "candle_alos" and not (candle_alos_start <= now <= candle_alos_end):
                attrs[name] = False
            elif w == "candle_both" and not (candle_both_start <= now <= candle_both_end):
                attrs[name] = False
            elif w == "havdalah_candle" and not (havdalah_candle_start <= now <= havdalah_candle_end):
                attrs[name] = False
            elif w == "candle_candle" and not (candle_candle_start <= now <= candle_candle_end):
                attrs[name] = False
            # others stay full day
            
        # ─── Aggregate flags & Shabbos Chol HaMoed (attributes only) ────────
        # "סוכות": 1st two days + entire Chol HaMoed through הושענא רבה
        attrs["סוכות"] = any(attrs.get(n) for n in [
            "סוכות א׳",
            "סוכות ב׳",
            "א׳ דחול המועד סוכות",
            "ב׳ דחול המועד סוכות",
            "ג׳ דחול המועד סוכות",
            "ד׳ דחול המועד סוכות",
            "הושענא רבה",
        ])

        # Single flag for both days: שמיני עצרת/שמחת תורה
        attrs["שמיני עצרת/שמחת תורה"] = bool(
            attrs.get("שמיני עצרת") or attrs.get("שמחת תורה")
        )

        # "פסח": 1st two days + entire Chol HaMoed + שביעי + אחרון
        attrs["פסח"] = any(attrs.get(n) for n in [
            "פסח א׳",
            "פסח ב׳",
            "א׳ דחול המועד פסח",
            "ב׳ דחול המועד פסח",
            "ג׳ דחול המועד פסח",
            "ד׳ דחול המועד פסח",
            "שביעי של פסח",
            "אחרון של פסח",
        ])

        # Single flag for both: שביעי/אחרון של פסח
        attrs["שביעי/אחרון של פסח"] = bool(
            attrs.get("שביעי של פסח") or attrs.get("אחרון של פסח")
        )

        # Shabbos Chol HaMoed (halachic Shabbos via wd_fest)
        attrs["שבת חול המועד סוכות"] = (
            wd_fest == 5 and hd_fest.month == 7 and hd_fest.day in (17, 18, 19, 20)
        )
        attrs["שבת חול המועד פסח"] = (
            wd_fest == 5 and hd_fest.month == 1 and hd_fest.day in (17, 18, 19, 20)
        )

        # Shabbos Rosh Chodesh – halachic Shabbos via wd_fest (Mon=0..Sun=6).
        # True when the halachic Shabbos day is also Rosh Chodesh
        # (RC days are 30 of the previous month or 1 of the current),
        # excluding 1 Tishrei (Rosh Hashanah) per your existing RC rule.
        attrs["שבת ראש חודש"] = (
            wd_fest == 5
            and (hd_fest.day in (1, 30))
            and not (hd_fest.month == 7 and hd_fest.day == 1)  # exclude RH
        )

        # ─── Countdown for fast ends in
        if any(attrs.get(f) for f in self.FAST_FLAGS):
            # during fast: show time until end
            if start_time_fast <= now < end_time:
                remaining_sec = max(0, (end_time - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אויס און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
                #_LOGGER.debug(f"Fast ends in set: now={now}, end_time={end_time}, "
                #              f"countdown={attrs['מען פאַסט אויס און']}")
            # before fast: show total duration
            elif now < start_time_fast:
                duration_sec = (end_time - start_time_fast).total_seconds()
                minutes_duration = math.ceil(duration_sec / 60)
                h = minutes_duration // 60
                m = minutes_duration % 60
                attrs["מען פאַסט אויס און"] = f"{h:02d}:{m:02d}"
                #_LOGGER.debug(f"Fast duration set: start_time_fast={start_time_fast}, "
                #              f"end_time={end_time}, countdown={attrs['מען פאַסט אויס און']}")
            # after fast: clear countdown
            else:
                attrs["מען פאַסט אויס און"] = ""
                #_LOGGER.debug(f"Fast ended, countdown cleared: now={now}, end_time={end_time}")
        else:
            attrs["מען פאַסט אויס און"] = ""
            #_LOGGER.debug(f"No fast flag active, countdown cleared")

        # Merge motzei
        from .motzi_holiday_sensor import (
            MotzeiYomKippurSensor,
            MotzeiPesachSensor,
            MotzeiSukkosSensor,
            MotzeiShavuosSensor,
            MotzeiRoshHashanaSensor,
            MotzeiShivaUsorBTammuzSensor,
            MotzeiTishaBavSensor,
        )
        for cls in [MotzeiYomKippurSensor, MotzeiPesachSensor, MotzeiSukkosSensor,
                    MotzeiShavuosSensor, MotzeiRoshHashanaSensor,
                    MotzeiShivaUsorBTammuzSensor, MotzeiTishaBavSensor]:
            motzi = cls(self.hass, self._candle_offset, self._havdalah_offset)
            await motzi.async_update(now)
            attrs[motzi._attr_name] = motzi.is_on
            attrs.update(getattr(motzi, "_attr_extra_state_attributes", {}))

        # ─── pick exactly one allowed holiday for .state ────────────────────
        combined = next((n for n, on in attrs.items() if n.endswith(" א׳ וב׳") and on), None)
        if combined:
            base = combined[:-len(" א׳ וב׳")]
            # Decide which day is active
            is_day1 = attrs.get(f"{base} א׳", False)
            # Build as "א׳ ד<base>" or "ב׳ ד<base>" but only for the two-day bases
            if base in self._TWO_DAY_BASES:
                picked = f"{'א' if is_day1 else 'ב'}׳ ד{base}"
            else:
                picked = f"{base} {'א׳' if is_day1 else 'ב׳'}"

        elif attrs.get("זאת חנוכה"):
            picked = "זאת חנוכה"

        elif any(attrs.get(name) for name in [
            "מוצאי ראש השנה",
            "מוצאי יום הכיפורים",
            "מוצאי פסח",
            "מוצאי שבועות",
            "מוצאי סוכות",
            "מוצאי צום שבעה עשר בתמוז",
            "מוצאי תשעה באב",
        ]):
            motzei_list = [
                "מוצאי ראש השנה",
                "מוצאי יום הכיפורים",
                "מוצאי פסח",
                "מוצאי שבועות",
                "מוצאי סוכות",
                "מוצאי צום שבעה עשר בתמוז",
                "מוצאי תשעה באב",
            ]
            picked = next(n for n in motzei_list if attrs.get(n))

        elif any(attrs.get(name) for name in [
            "אסרו חג פסח",
            "אסרו חג שבועות",
            "אסרו חג סוכות",
        ]):
            asru_list = [
                "אסרו חג פסח",
                "אסרו חג שבועות",
                "אסרו חג סוכות",
            ]
            picked = next(n for n in asru_list if attrs.get(n))

        else:
            picked = next((n for n in self.ALLOWED_HOLIDAYS if attrs.get(n)), "")
            # Flip single-day forms like "סוכות א׳" → "א׳ דסוכות" when applicable
            picked = self._flip_two_day_format(picked)

        self._attr_native_value = picked
        self._attr_extra_state_attributes = attrs
        attrs["Possible states"] = [self._flip_two_day_format(n) for n in self.ALLOWED_HOLIDAYS]
