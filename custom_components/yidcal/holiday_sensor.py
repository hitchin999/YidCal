# holiday_sensor.py
"""
Separate HolidaySensor for YidCal integration.
Handles Jewish holidays, fast days, and custom periods with time-aware logic,
restores its last state across reboots, and filters the visible state
through a whitelist while still exposing all flags.
"""

from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging
from .device import YidCalDevice

from astral import LocationInfo
from astral.sun import sun
from hdate import HDateInfo
from hdate.translator import set_language
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from pyluach.parshios import getparsha_string

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

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
        "סוכות א׳",
        "סוכות ב׳",
        "סוכות א׳ וב׳",
        "א׳ דחול המועד סוכות",
        "ב׳ דחול המועד סוכות",
        "ג׳ דחול המועד סוכות",
        "ד׳ דחול המועד סוכות",
        "חול המועד סוכות",
        "הושענא רבה",
        "שמיני עצרת",
        "שמחת תורה",
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
        "ערב פסח",
        "פסח א׳",
        "פסח ב׳",
        "פסח א׳ וב׳",
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
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
        "ראש חודש",
    ]

    # ─── Only these may become the sensor.state ───
    ALLOWED_HOLIDAYS: list[str] = {
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",
        "ראש השנה ב׳",
        "מוצאי ראש השנה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות א׳",
        "סוכות ב׳",
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
        "פסח א׳",
        "פסח ב׳",
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
        "שבועות א׳",
        "שבועות ב׳",
        "מוצאי שבועות",
        "אסרו חג שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה", 
        "מוצאי תשעה באב",
    }    

    # ─── Window‐type map: holiday‑name → named window key ───────────
    WINDOW_TYPE: dict[str, str] = {
        # Festival days: sunset−candle_offset → sunset+havdalah_offset
        "א׳ סליחות":                     "havdalah_candle",
        "ערב ראש השנה":                  "alos_candle",
        "ראש השנה א׳":                   "candle_havdalah",
        "ראש השנה ב׳":                   "havdalah_havdalah",
        "ראש השנה א׳ וב׳":                "candle_havdalah",
        "צום גדליה":                      "alos_havdalah",
        "שלוש עשרה מדות":                 "alos_havdalah",
        "ערב יום כיפור":                   "alos_candle",
        "יום הכיפורים":                    "candle_havdalah",
        "ערב סוכות":                      "alos_candle",
        "סוכות א׳":                       "candle_havdalah",
        "סוכות ב׳":                       "havdalah_havdalah",
        "סוכות א׳ וב׳":                    "candle_havdalah",
        "א׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ב׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ג׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ד׳ דחול המועד סוכות":               "havdalah_havdalah",
        "חול המועד סוכות":                  "candle_havdalah",
        "הושענא רבה":                     "havdalah_havdalah",
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
        "ערב פסח":                       "alos_candle",
        "פסח א׳":                        "candle_havdalah",
        "פסח ב׳":                        "havdalah_havdalah",
        "פסח א׳ וב׳":                     "candle_havdalah",
        "א׳ דחול המועד פסח ":               "havdalah_havdalah",
        "ב׳ דחול המועד פסח":                "havdalah_havdalah",
        "ג׳ דחול המועד פסח":                "havdalah_havdalah",
        "ד׳ דחול המועד פסח":                "havdalah_havdalah",
        "חול המועד פסח":                  "havdalah_havdalah",
        "שביעי של פסח":                   "candle_havdalah",
        "אחרון של פסח":                   "havdalah_havdalah",
        "אסרו חג פסח":                    "havdalah_havdalah",
        "פסח שני":                       "havdalah_havdalah",
        "ל\"ג בעומר":                    "havdalah_havdalah",
        "ערב שבועות":                    "alos_candle",
        "שבועות א׳":                     "candle_havdalah",
        "שבועות ב׳":                     "havdalah_havdalah",
        "שבועות א׳ וב׳":                  "candle_havdalah",
        "אסרו חג שבועות":                "havdalah_havdalah",
        "צום שבעה עשר בתמוז":             "alos_havdalah",
        "ערב תשעה באב":                 "alos_candle",
        "תשעה באב":                    "candle_havdalah",
        "תשעה באב נדחה":                "candle_havdalah",
        "ראש חודש":                    "havdalah_havdalah",
    }

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "holiday"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        # initial state + full attrs
        self._attr_native_value: str = ""
        self._attr_extra_state_attributes: dict[str, bool | int] = {}

        # Hebrew names
        set_language("he")
        
    async def async_added_to_hass(self) -> None:

        # Restore last state/attributes on startup
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_native_value = last.state or ""
            self._attr_extra_state_attributes = dict(last.attributes)
            
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
    def extra_state_attributes(self) -> dict[str, bool | int]:
        return self._attr_extra_state_attributes

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None:
            return

        # 1) Base time, location, and holiday_date computation
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        actual_date = now.date()
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        # Civil sunset/dawn for today
        z_civil    = sun(loc.observer, date=actual_date, tzinfo=tz)
        dawn       = z_civil["sunrise"] - timedelta(minutes=72)
        candle_cut = z_civil["sunset"] - timedelta(minutes=self._candle_offset)
        # If after candle‑lighting, we consider the *next* Hebrew day
        if now >= candle_cut:
            holiday_date = actual_date + timedelta(days=1)
        else:
            holiday_date = actual_date
        # Fetch the day‑before and holiday_date sunsets
        z_prev        = sun(loc.observer, date=holiday_date - timedelta(days=1), tzinfo=tz)
        z_holoday     = sun(loc.observer, date=holiday_date,              tzinfo=tz)
        prev_sunset   = z_prev["sunset"]
        today_sunset  = z_holoday["sunset"]

        # 2) Pre-calc *all* named windows based on holiday_date
        candle_candle_start, candle_candle_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            today_sunset - timedelta(minutes=self._candle_offset),
        )

        candle_havdalah_start, candle_havdalah_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            today_sunset + timedelta(minutes=self._havdalah_offset),
        )
        alos_havdalah_start, alos_havdalah_end = (
            dawn,
            today_sunset + timedelta(minutes=self._havdalah_offset),
        )
        alos_candle_start, alos_candle_end = (
            dawn,
            today_sunset - timedelta(minutes=self._candle_offset),
        )
        candle_alos_start, candle_alos_end = (
            prev_sunset - timedelta(minutes=self._candle_offset),
            dawn,
        )
        havdalah_havdalah_start, havdalah_havdalah_end = (
            prev_sunset + timedelta(minutes=self._havdalah_offset),
            today_sunset + timedelta(minutes=self._havdalah_offset),
        )
        havdalah_candle_start, havdalah_candle_end = (
            prev_sunset + timedelta(minutes=self._havdalah_offset),
            today_sunset - timedelta(minutes=self._candle_offset),
        )
        
        # 3) Fast window uses the *same* prev_sunset & today_sunset
        #    (unify festival & fast windows)
        heb_info   = HDateInfo(holiday_date, diaspora=True)
        hd_py      = PHebrewDate.from_pydate(holiday_date)
        hd_py_fast = PHebrewDate.from_pydate(actual_date)

        # leap-year for Shovavim
        year    = hd_py.year
        is_leap = ((year * 7 + 1) % 19) < 7
        
        # ─── all fasts & yom‐tov end at *holiday_date* sunset + havdalah_offset
        end_time = today_sunset + timedelta(minutes=self._havdalah_offset)

        # ─── fast‐start: dawn for most, candle‐lighting for YK/Tisha B’Av
        #       (based on *holiday_date* rather than actual_date)
        if (hd_py.month == 7 and hd_py.day == 10) or \
           (hd_py.month == 5 and hd_py.day in (9,10)):
            # Yom Kippur & Tisha B’Av: start at candle‐lighting of the day before
            start_time_fast = prev_sunset - timedelta(minutes=self._candle_offset)
        else:
            # everyone else: start at dawn of holiday_date
            start_time_fast = dawn

        # 4) Build raw attrs (default no holiday + no countdown)
        attrs = {name: False for name in self.ALL_HOLIDAYS}
        attrs["מען פאַסט אויס און"] = None
        

        #Alef Slichos
        if hd_py.month == 6 and 21 <= hd_py.day <= 26 and wd == 6: 
            attrs["א׳ סליחות"] = True
        # Erev Rosh Hashanah
        if hd_py.month == 6 and hd_py.day == 29:
            attrs["ערב ראש השנה"] = True
            
        # Rosh Hashanah
        if hd_py.month == 7 and hd_py.day == 1:
            attrs["ראש השנה א׳"] = True
            attrs["ראש השנה א׳ וב׳"] = True
        if hd_py.month == 7 and hd_py.day == 2:
            attrs["ראש השנה ב׳"] = True
            attrs["ראש השנה א׳ וב׳"] = True
        # Tzom Gedalye
        if hd_py_fast.month == 7 and hd_py_fast.day == 3: 
            attrs["צום גדליה"] = True
            
        wd = now.weekday()
        if hd_py.month == 7 and ((hd_py.day == 8 and wd in [0,1,3]) or (hd_py.day == 6 and wd == 3)):
            attrs["שלוש עשרה מדות"] = True
            
        # Yom Kippur
        if hd_py.month == 7 and hd_py.day == 9:
            attrs["ערב יום כיפור"] = True
        if hd_py.month == 7 and hd_py.day == 10:
            attrs["יום הכיפורים"] = True

        # Sukkot
        if hd_py.month == 7:
            if hd_py.day == 14: attrs["ערב סוכות"] = True
            if hd_py.day == 15:
                attrs["סוכות א׳"] = True
                attrs["סוכות א׳ וב׳"] = True
            if hd_py.day == 16:
                attrs["סוכות ב׳"] = True
                attrs["סוכות א׳ וב׳"] = True
            if hd_py.day == 17:
                attrs["א׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_py.day == 18:
                attrs["ב׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_py.day == 19:
                attrs["ג׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_py.day == 20:
                attrs["ד׳ דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True
            if hd_py.day == 21: 
                attrs["הושענא רבה"] = True
            if hd_py.day == 22: 
                attrs["שמיני עצרת"] = True
            if hd_py.day == 23: 
                attrs["שמחת תורה"] = True
            if hd_py.day == 24: 
                attrs["אסרו חג סוכות"] = True
            
        # Chanukah & Erev at dawn (Kislev 9) and Zot Chanukah (Tevet 10)
        if hd_py.month == 9:
            # Erev Chanukah
            if hd_py.day == 24:
                attrs["ערב חנוכה"] = True
            # Days 1–6 of Chanukah
            if 25 <= hd_py.day <= 30:
                attrs["חנוכה"] = True

        elif hd_py.month == 10:
            # Days 7–8 of Chanukah
            if hd_py.day == 1:
                attrs["חנוכה"] = True
            if hd_py.day == 2:
                attrs["חנוכה"] = True
                attrs["זאת חנוכה"] = True
                
        # Shovavim
        parsha = (getparsha_string(hd_py) or "").upper()
        shov_base = ["SHEMOT","VAERA","BO","BESHALACH","YITRO","MISHPATIM"]
        shov_ext  = shov_base + ["TERUMAH","TETZAVEH"]
        attrs["שובבים"]     = parsha in shov_base
        attrs["שובבים ת\"ת"] = is_leap and parsha in shov_ext
        
        if hd_py_fast.month == 10 and hd_py_fast.day == 10: 
            attrs["צום עשרה בטבת"] = True
            
        # Tu BiShvat
        if hd_py.month == 11 and hd_py.day == 15:
            attrs["ט\"ו בשבט"] = True
            
        # Purim
        if hd_py.month in (12,13):
            if hd_py.day == 13: 
                attrs["תענית אסתר"] = True
            if hd_py.day == 14: 
                attrs["פורים"] = True
            if hd_py.day == 15: 
                attrs["שושן פורים"] = True

        # Bedikat Chametz
        tomorrow = actual_date + timedelta(days=1)
        hd_tomorrow = PHebrewDate.from_pydate(tomorrow)
        bedikat_day = 12 if (hd_tomorrow.month == 1 and hd_tomorrow.day == 15 and tomorrow.weekday() == 6) else 14
        if hd_py.month == 1 and hd_py.day == bedikat_day:
            if yesterday_sunset <= now < dawn:
                attrs["ליל בדיקת חמץ"] = True

        # Pesach & Erev
        if hd_py.month == 1:
            if hd_py.day == 14:
                attrs["ערב פסח"] = True
            if hd_py.day == 15:
                attrs["פסח א׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if hd_py.day == 16:
                attrs["פסח ב׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if hd_py.day == 17:
                attrs["א׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_py.day == 18:
                attrs["ב׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_py.day == 19:
                attrs["ג׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_py.day == 20:
                attrs["ד׳ דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True
            if hd_py.day == 21:
                attrs["שביעי של פסח"] = True
            if hd_py.day == 22:
                attrs["אחרון של פסח"] = True
            if hd_py.day == 23:
                attrs["אסרו חג פסח"] = True
                
        # Pesach Sheini & Lag BaOmer
        if hd_py.month == 2:
            if hd_py.day == 14: 
                attrs["פסח שני"] = True
            if hd_py.day == 18: 
                attrs["ל\"ג בעומר"] = True

        # Shavuot & Erev (month 3)
        if hd_py.month == 3:
            if hd_py.day == 5:
                attrs["ערב שבועות"] = True
            if hd_py.day == 6:
                attrs["שבועות א׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            if hd_py.day == 7:
                attrs["שבועות ב׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            if hd_py.day == 8:
                attrs["אסרו חג שבועות"] = True

        # Rosh Chodesh
        if hd_py.day in (1, 30): attrs["ראש חודש"] = True



        # ─── Tzom Shiva Usor Betamuz (17 Tammuz) ──────────────
        # dawn → sunset+havdalah
        if hd_py_fast.month == 4 and hd_py_fast.day == 17 and dawn <= now <= end_time:
            attrs["צום שבעה עשר בתמוז"] = True

        # ─── Tisha B’av proper (9 Av) ──────────────────────────
        # candle‑lighting → sunset+havdalah (use holiday_date)
        if hd_py.month == 5 and hd_py.day == 9 \
            and start_time_fast <= now <= end_time:
            attrs["תשעה באב"] = True

        # ─── Deferred Tisha B’av (10 Av if 9 Av was Shabbat) ────
        # same window (use holiday_date)
        if hd_py.month == 5 and hd_py.day == 10 \
            and start_time_fast <= now <= end_time:
            attrs["תשעה באב נדחה"] = True

        # ─── Countdown for any fast in progress ─────────────────
        FAST_FLAGS = [
            "יום הכיפורים",
            "צום גדליה",
            "תענית אסתר",
            "צום עשרה בטבת",
            "צום שבעה עשר בתמוז",
            "תשעה באב",
            "תשעה באב נדחה",
        ]
        if any(attrs.get(f) for f in FAST_FLAGS):
            # before start → total duration, else remaining until end
            if now < start_time_fast:
                seconds = (end_time - start_time_fast).total_seconds()
            else:
                seconds = (end_time - now).total_seconds()
            seconds = max(0, int(seconds))
            h, m = divmod(seconds, 3600)
            m = m // 60
            attrs["מען פאַסט אויס און"] = f"{h:02d}:{m:02d}"

        # Filter attrs
        for name, on in list(attrs.items()):
            if not on:
                continue
            w = self.WINDOW_TYPE.get(name)
            if w == "candle_havdalah":
                if not(candle_havdalah_start <= now <= candle_havdalah_end): attrs[name] = False
            elif w == "havdalah_havdalah":
                if not(havdalah_havdalah_start <= now <= havdalah_havdalah_end): attrs[name] = False
            elif w == "havdalah_candle":
                if not(havdalah_candle_start <= now <= havdalah_candle_end): attrs[name] = False
            elif w == "alos_havdalah":
                if not(alos_havdalah_start <= now <= alos_havdalah_end): attrs[name] = False
            elif w == "alos_candle":
                if not (alos_candle_start <= now <= alos_candle_end): attrs[name] = False
            elif w == "candle_alos":
                if not (candle_alos_start <= now <= candle_alos_end): attrs[name] = False
            # others stay full day

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
        # 1) Zot Chanukah
        if attrs.get("זאת חנוכה"):
            picked = "זאת חנוכה"

        # 2) motzei flags (all “מוצאי …”) — they’re only True until 2 AM
        elif any(attrs.get(name) for name in [
            "מוצאי ראש השנה",
            "מוצאי יום הכיפורים",
            "מוצאי פסח",
            "מוצאי שבועות",
            "מוצאי סוכות",
            "מוצאי צום שבעה עשר בתמוז",
            "מוצאי תשעה באב",
        ]):
            # pick the first motzei that’s True
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

        # 3) אסרו חג flags (they only become True after motzei ends)
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

        # 4) everything else in your normal ALLOWED_HOLIDAYS order
        else:
            picked = next((n for n in self.ALLOWED_HOLIDAYS if attrs.get(n)), "")

        attrs["possible_states"] = self.ALL_HOLIDAYS
        self._attr_native_value = picked
        self._attr_extra_state_attributes = attrs
