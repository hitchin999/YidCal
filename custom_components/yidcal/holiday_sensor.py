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
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
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
        "ערב חנוכה",
        "חנוכה",
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
        "חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",
        "שבועות ב׳",
        "שבועות א׳ וב׳",
        "צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה",
        "ראש חודש",
    ]

    # ─── Only these may become the sensor.state ───
    ALLOWED_HOLIDAYS: set[str] = {
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",
        "ראש השנה ב׳",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
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
        "ערב חנוכה",
        "חנוכה",
        "צום עשרה בטבת",
        "ט\"ו בשבט",
        "תענית אסתר",
        "פורים",
        "שושן פורים",
        "ליל בדיקת חמץ",
        "ערב פסח",
        "פסח א׳",
        "פסח ב׳",
        "חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",
        "שבועות ב׳",
        "צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה",        
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

        # 1) Determine “today” in local tz, bump past sunset for candle‐lighting
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()

        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        s = sun(loc.observer, date=today, tzinfo=tz)
        if now >= s["sunset"] - timedelta(minutes=self._candle_offset):
            today += timedelta(days=1)

        # 2) Hebrew date info
        heb_info = HDateInfo(today, diaspora=True)
        hd_py    = PHebrewDate.from_pydate(today)

        # 3) Leap‐year flag for Shovavim
        year    = hd_py.year
        is_leap = ((year * 7 + 1) % 19) < 7
        
        # 4) Compute zmanim using a fixed 72 minutes before sunrise for alos hashachar
        z_t = sun(
            observer=loc.observer,
            date=today,
            tzinfo=tz,
        )
        sunrise         = z_t["sunrise"]
        dawn            = sunrise - timedelta(minutes=72)  # 72 min before sunrise
        today_sunset    = z_t["sunset"]

        z_y = sun(
            observer=loc.observer,
            date=today - timedelta(days=1),
            tzinfo=tz,
        )
        yesterday_sunset = z_y["sunset"]


        # 5) Holiday vs. fast logic (exactly as you had it)
        hol_name   = hd_py.holiday(hebrew=True, prefix_day=True)
        is_holiday = bool(hol_name and (heb_info.is_holiday or heb_info.is_yom_tov))
        is_fast    = hol_name in [
            "יום הכיפורים",
            "צום גדליה",
            "תענית אסתר",
            "צום עשרה בטבת",
            "צום שבעה עשר בתמוז",
            "תשעה באב",
            "תשעה באב נדחה",
        ]

        # 6) Compute start_time per‐fast (sunset–offset for YK & Tisha B’Av, else dawn)
        start_time = None
        if is_holiday:
            # Yomim Tovim all start at candle‐lighting
            start_time = yesterday_sunset - timedelta(minutes=self._candle_offset)
        if is_fast:
            if hol_name in ["יום הכיפורים", "תשעה באב", "תשעה באב נדחה"]:
                start_time = yesterday_sunset - timedelta(minutes=self._candle_offset)
            else:
                start_time = dawn

        # 7) Fast end is always havdalah‐offset after sunset
        end_time = None
        if is_holiday or is_fast:
            end_time = today_sunset + timedelta(minutes=self._havdalah_offset)

        # 8) Build your full attrs dict in order
        attrs: dict[str, bool | int] = {}
        for name in self.ALL_HOLIDAYS:
            attrs[name] = False
        attrs["מען פאַסט אויס און"] = None

        # Map holiday booleans
        # Rosh HaShanah: month 7 days 1-2
        if hd_py.month == 7:
            if hd_py.day == 1:
                attrs["ראש השנה א׳"] = True
                attrs["ראש השנה א׳ וב׳"] = True
            if hd_py.day == 2:
                attrs["ראש השנה ב׳"] = True
                attrs["ראש השנה א׳ וב׳"] = True
        # Erev Rosh HaShanah at dawn: 29 Elul (month 6)
        if hd_py.month == 6 and hd_py.day == 29 and now >= dawn:
            attrs["ערב ראש השנה"] = True

        # Yom Kippur: day 10 Tishrei (month 7) & Erev (day 9)
        if hd_py.month == 7 and hd_py.day == 9 and now >= dawn:
            attrs["ערב יום כיפור"] = True
        if hd_py.month == 7 and hd_py.day == 10:
            attrs["יום הכיפורים"] = True

        # Sukkot & related (month 7)
        if hd_py.month == 7:
            if hd_py.day == 14 and now >= dawn:
                attrs["ערב סוכות"] = True
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
                
        # 1. Decide which Hebrew day we search for chametz:
        #    Normally on 14 Nisan, except when 15 Nisan (first Seder) is Saturday night,
        #    in which case we move it two days earlier to 12 Nisan.
        tomorrow = today + timedelta(days=1)
        hd_tomorrow = PHebrewDate.from_pydate(tomorrow)
        # Python weekday: Monday=0 … Sunday=6
        # Seder on Saturday night means 15 Nisan falls on Sunday daytime:
        if hd_tomorrow.month == 1 and hd_tomorrow.day == 15 and tomorrow.weekday() == 6:
            bedikat_day = 12
        else:
            bedikat_day = 14

        # 2. If *today* is the bedikat day, set the boolean between sunset and dawn:
        if hd_py.month == 1 and hd_py.day == bedikat_day:
            # night begins at yesterday’s sunset, ends at today’s dawn
            if yesterday_sunset <= now < dawn:
                attrs["ליל בדיקת חמץ"] = True

        # Pesach & Erev at dawn (month 1)
        if hd_py.month == 1:
            if hd_py.day == 14 and now >= dawn:
                attrs["ערב פסח"] = True
            if hd_py.day == 15:
                attrs["פסח א׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if hd_py.day == 16:
                attrs["פסח ב׳"] = True
                attrs["פסח א׳ וב׳"] = True
            if 17 <= hd_py.day <= 20:
                attrs["חול המועד פסח"] = True
            if hd_py.day == 21:
                attrs["שביעי של פסח"] = True
            if hd_py.day == 22:
                attrs["אחרון של פסח"] = True

        # Shavuot & Erev at dawn (month 3)
        if hd_py.month == 3:
            if hd_py.day == 5 and now >= dawn:
                attrs["ערב שבועות"] = True
            if hd_py.day == 6:
                attrs["שבועות א׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            if hd_py.day == 7:
                attrs["שבועות ב׳"] = True
                attrs["שבועות א׳ וב׳"] = True

        # Purim & Shushan Purim & Ta'anit Esther (month 12 or 13)
        if hd_py.month in (12, 13):
            if hd_py.day == 13:
                attrs["תענית אסתר"] = True
            if hd_py.day == 14:
                attrs["פורים"] = True
            if hd_py.day == 15:
                attrs["שושן פורים"] = True

        # Chanukah & Erev at dawn (month 9)
        if hd_py.month == 9:
            if hd_py.day == 24 and now >= dawn:
                attrs["ערב חנוכה"] = True
            if (25 <= hd_py.day <= 30) or hd_py.day <= 2:
                attrs["חנוכה"] = True

        # Tu BiShvat (month 11)
        if hd_py.month == 11 and hd_py.day == 15:
            attrs["ט\"ו בשבט"] = True

        # Lag BaOmer (month 2)
        if hd_py.month == 2 and hd_py.day == 18:
            attrs["ל\"ג בעומר"] = True

        # Fast days
        if hd_py.month == 7 and hd_py.day == 3:
            attrs["צום גדליה"] = True
        if hd_py.month == 10 and hd_py.day == 10:
            attrs["צום עשרה בטבת"] = True
        if hd_py.month == 4 and hd_py.day == 17:
            attrs["צום שבעה עשר בתמוז"] = True
        if hd_py.month == 5 and hd_py.day == 9:
            attrs["תשעה באב"] = True

        # Rosh Chodesh
        if hd_py.day in (1, 30):
            attrs["ראש חודש"] = True

        # Custom periods
        # Thirteen Attributes of Mercy: 8 Tishrei Mon/Tue/Thu or 6 Tishrei Thu
        weekday = now.weekday()
        if (hd_py.month == 7 and ((hd_py.day == 8 and weekday in [0,1,3]) or (hd_py.day == 6 and weekday == 3))):
            attrs["שלוש עשרה מדות"] = True
        # Selichot: Sundays from 21–26 Elul (month 6)
        if hd_py.month == 6 and 21 <= hd_py.day <= 26 and weekday == 6:
            attrs["א׳ סליחות"] = True
        # תשעה באב נדחה: 10 Av on Sunday (month 5)
        if hd_py.month == 5 and hd_py.day == 10:
            # build a HebrewDate for 9 Av of this Hebrew year
            nine_av = PHebrewDate(hd_py.year, 5, 9)
            # convert to a Python date and check its weekday
            nine_av_greg = nine_av.to_pydate()  
            # Python: Monday=0 … Saturday=5, Sunday=6
            if nine_av_greg.weekday() == 5:
                attrs["תשעה באב נדחה"] = True
            
        # Base six-parsha Shovavim
        shov_base = ["SHEMOT","VAERA","BO","BESHALACH","YITRO","MISHPATIM"]
        shov_ext  = shov_base + ["TERUMAH","TETZAVEH"]

        parsha = (getparsha_string(hd_py) or "").upper()
        attrs["שובבים"]     = parsha in shov_base
        attrs["שובבים ת\"ת"] = is_leap and (parsha in shov_ext)

        # ── COUNTDOWN until havdalah for any fast day, formatted HH:MM ──
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
            end_time = today_sunset + timedelta(minutes=self._havdalah_offset)
            remaining = int((end_time - now).total_seconds())
            # never negative
            remaining = max(0, remaining)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            # format as "HH:MM"
            attrs["מען פאַסט אויס און"] = f"{hours:02d}:{minutes:02d}"
        else:
            attrs["מען פאַסט אויס און"] = None

        # ────────────────────────────────────────────────
        # 10) PICK exactly one allowed holiday for the visible state
        picked: str | None = None
        for name in self.ALLOWED_HOLIDAYS:
            if attrs.get(name) is True:
                picked = name
                break

        # 11) EXPOSE full attrs, but state is only the picked one
        self._attr_native_value = picked or ""
        self._attr_extra_state_attributes = attrs
