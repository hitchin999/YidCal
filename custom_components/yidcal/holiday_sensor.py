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
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import logging
from .device import YidCalDevice
from .const import DOMAIN

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation
from hdate import HDateInfo
from hdate.translator import set_language
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from pyluach.parshios import getparsha_string

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from .erev_motzei_extra import compute_erev_motzei_flags, EXTRA_ATTRS
from .zman_sensors import get_geo

def _round_half_up(dt: datetime.datetime) -> datetime.datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)

def _round_ceil(dt: datetime.datetime) -> datetime.datetime:
    """Always bump up to the next minute."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
def _compute_chatzos_hayom(geo: GeoLocation, base_date: datetime.date, tz: ZoneInfo) -> datetime.datetime:
    """Compute Chatzos Hayom (halachic midday) for a given date using MGA day."""
    cal = ZmanimCalendar(geo_location=geo, date=base_date)
    sunrise = cal.sunrise().astimezone(tz)
    sunset = cal.sunset().astimezone(tz)

    # MGA day: 72 minutes before sunrise to 72 minutes after sunset
    dawn = sunrise - timedelta(minutes=72)
    nightfall = sunset + timedelta(minutes=72)

    hour_td = (nightfall - dawn) / 12
    chatzos = dawn + hour_td * 6

    # Round: <30s floor, >=30s ceil
    if chatzos.second >= 30:
        chatzos += timedelta(minutes=1)
    return chatzos.replace(second=0, microsecond=0)

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
    _attr_device_class = SensorDeviceClass.ENUM
    
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
        "עשרת ימי תשובה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות (כל חג)",
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
        "ערב שבת חנוכה",
        "שבת חנוכה",
        "שבת חנוכה ראש חודש",
        "א׳ דחנוכה",
        "ב׳ דחנוכה",
        "ג׳ דחנוכה",
        "ד׳ דחנוכה",
        "ה׳ דחנוכה",
        "ו׳ דחנוכה",
        "ז׳ דחנוכה",
        "זאת חנוכה",
        "מוצאי חנוכה",
        "שובבים",
        "שובבים ת\"ת",
        "צום עשרה בטבת",
        "חמשה עשר בשבט",
        "תענית אסתר",
        "תענית אסתר מוקדם",
        "פורים",
        "שושן פורים",
        "מוצאי שושן פורים",
        "ליל בדיקת חמץ",
        "ערב פסח מוקדם",
        "שבת ערב פסח",
        "ערב פסח",
        "פסח (כל חג)",
        "פסח א׳",
        "פסח ב׳",
        "פסח א׳ וב׳",
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "ה׳ דחול המועד פסח",
        "חול המועד פסח",
        "שבת חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "שביעי/אחרון של פסח",
        "מוצאי פסח",
        "אסרו חג פסח",
        "פסח שני",
        "ל\"ג בעומר",
        "מוצאי ל\"ג בעומר",
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
        "ט\"ו באב",
        "יום כיפור קטן",
        "ראש חודש",
        "שבת ראש חודש",
        "ערב שבת",
        "ערב יום טוב",
        "מוצאי שבת",
        "מוצאי יום טוב",
        "ערב שבת שחל ביום טוב",
        "ערב יום טוב שחל בשבת",
        "מוצאי שבת שחל ביום טוב",
        "מוצאי יום טוב שחל בשבת",
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
        "א׳ דחנוכה",
        "ב׳ דחנוכה",
        "ג׳ דחנוכה",
        "ד׳ דחנוכה",
        "ה׳ דחנוכה",
        "ו׳ דחנוכה",
        "ז׳ דחנוכה",
        "חנוכה",
        "זאת חנוכה",
        "צום עשרה בטבת",
        "חמשה עשר בשבט",
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
        "ה׳ דחול המועד פסח",
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
        "מוצאי תשעה באב",
        "ט\"ו באב",
        "יום כיפור קטן",
    ]

    # ─── Window‐type map: holiday‑name → named window key ───────────
    WINDOW_TYPE: dict[str, str] = {
        "א׳ סליחות":                     "havdalah_candle",
        "ערב ראש השנה":                  "havdalah_candle",
        "ראש השנה א׳":                   "candle_havdalah",
        "ראש השנה ב׳":                   "havdalah_havdalah",
        "ראש השנה א׳ וב׳":                "candle_both",
        "צום גדליה":                      "alos_havdalah",
        "שלוש עשרה מדות":                 "alos_candle",
        "ערב יום כיפור":                   "candle_candle",
        "יום הכיפורים":                    "candle_havdalah",
        "ערב סוכות":                      "havdalah_candle",
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
        "ערב שבת חנוכה":                  "alos_candle",
        "שבת חנוכה":                      "candle_havdalah", 
        "שבת חנוכה ראש חודש":              "candle_havdalah",
        "א׳ דחנוכה":                      "havdalah_havdalah",
        "ב׳ דחנוכה":                      "havdalah_havdalah",
        "ג׳ דחנוכה":                      "havdalah_havdalah",
        "ד׳ דחנוכה":                      "havdalah_havdalah",
        "ה׳ דחנוכה":                      "havdalah_havdalah",
        "ו׳ דחנוכה":                      "havdalah_havdalah",
        "ז׳ דחנוכה":                      "havdalah_havdalah",
        "זאת חנוכה":                      "havdalah_havdalah",
        "שובבים":                        "havdalah_havdalah",
        "שובבים ת\"ת":                   "havdalah_havdalah",
        "צום עשרה בטבת":                 "alos_havdalah",
        "חמשה עשר בשבט":                "havdalah_havdalah",
        "תענית אסתר":                     "alos_havdalah",
        "תענית אסתר מוקדם":                "alos_havdalah",
        "פורים":                         "havdalah_havdalah",
        "שושן פורים":                     "havdalah_havdalah",
        "ליל בדיקת חמץ":                   "candle_alos",
        "ערב פסח מוקדם":                  "havdalah_candle",
        "שבת ערב פסח":                   "candle_candle",
        "ערב פסח":                       "havdalah_candle",
        "פסח א׳":                        "candle_havdalah",
        "פסח ב׳":                        "havdalah_havdalah",
        "פסח א׳ וב׳":                     "candle_both",
        "א׳ דחול המועד פסח":                "havdalah_havdalah",
        "ב׳ דחול המועד פסח":                "havdalah_havdalah",
        "ג׳ דחול המועד פסח":                "havdalah_havdalah",
        "ד׳ דחול המועד פסח":                "havdalah_havdalah",
        "ה׳ דחול המועד פסח":                "havdalah_havdalah",
        "חול המועד פסח":                  "havdalah_candle",
        "שביעי של פסח":                   "candle_havdalah",
        "אחרון של פסח":                   "havdalah_havdalah",
        "אסרו חג פסח":                    "havdalah_havdalah",
        "פסח שני":                       "havdalah_havdalah",
        "ל\"ג בעומר":                    "havdalah_havdalah",
        "ערב שבועות":                    "havdalah_candle",
        "שבועות א׳":                     "candle_havdalah",
        "שבועות ב׳":                     "havdalah_havdalah",
        "שבועות א׳ וב׳":                  "candle_both",
        "אסרו חג שבועות":                "havdalah_havdalah",
        "צום שבעה עשר בתמוז":             "alos_havdalah",
        "ערב תשעה באב":                 "alos_havdalah",
        "תשעה באב":                    "candle_havdalah",
        "תשעה באב נדחה":                "candle_havdalah",
        "ט\"ו באב":                     "alos_havdalah",
        "יום כיפור קטן":                  "alos_havdalah",
        "ראש חודש":                    "havdalah_havdalah",
    }
    
    # Attributes that should only exist in one mode to avoid confusion
    EY_ONLY_ATTRS = {
        "ה׳ דחול המועד פסח",
    }
    DIASPORA_ONLY_ATTRS = {
        "סוכות ב׳",
        "פסח ב׳",
        "שבועות ב׳",
        "סוכות א׳ וב׳",
        "פסח א׳ וב׳",
        "שבועות א׳ וב׳",
        "אחרון של פסח",
        "שביעי/אחרון של פסח",
        "שמיני עצרת",
        "שמחת תורה",
    }

    def _possible_states_for_mode(self) -> list[str]:
        """
        Build the UI options in chronological order.
        - Base order = ALLOWED_HOLIDAYS (already chronological), with 2-day forms flipped.
        - In Israel mode:
            • Replace separate שמיני עצרת/שמחת תורה with a single combined label,
              inserted immediately after הושענא רבה.
            • Insert base names סוכות/פסח/שבועות immediately after א׳ ד<חג>.
        """
        seq = [
            self._flip_two_day_format(n)
            for n in self.ALLOWED_HOLIDAYS
            if self._attr_allowed_in_mode(n)
        ]
        if self._diaspora:
            return seq

        ey_seq: list[str] = []
        seen = set()
        inserted_combined = False
        for label in seq:
            # Skip the split שמיני/שמחת in EY; we'll insert the combined one once.
            if label in ("שמיני עצרת", "שמחת תורה"):
                continue
            ey_seq.append(label); seen.add(label)

            # Right after הושענא רבה, insert the combined label.
            if label == "הושענא רבה" and not inserted_combined:
                ey_seq.append("שמיני עצרת/שמחת תורה")
                seen.add("שמיני עצרת/שמחת תורה")
                inserted_combined = True

            # Insert base names right after Day-1 labels.
            if label == "א׳ דסוכות" and "סוכות" not in seen:
                ey_seq.append("סוכות"); seen.add("סוכות")
            if label == "א׳ דפסח" and "פסח" not in seen:
                ey_seq.append("פסח"); seen.add("פסח")
            if label == "א׳ דשבועות" and "שבועות" not in seen:
                ey_seq.append("שבועות"); seen.add("שבועות")

        return ey_seq

    def _attr_allowed_in_mode(self, name: str) -> bool:
        if self._diaspora:
            return name not in self.EY_ONLY_ATTRS
        return name not in self.DIASPORA_ONLY_ATTRS

    def _empty_attrs_for_mode(self) -> dict[str, bool | str | list[str]]:
        names = [n for n in self.ALL_HOLIDAYS if self._attr_allowed_in_mode(n)]
        attrs = {name: False for name in names}
        attrs["מען פאַסט אויס און"] = ""
        attrs["מען פאַסט אַן און"] = ""
        return attrs

    def _prune_attrs_for_mode(self, attrs: dict[str, bool | str]) -> dict[str, bool | str]:
        allowed = set(self._empty_attrs_for_mode().keys())
        # keep special meta keys
        keep_always = {"Possible states", "מען פאַסט אויס און", "מען פאַסט אַן און"}
        return {k: v for k, v in attrs.items() if (k in allowed or k in keep_always)}
    
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

    # For Israel-only display: collapse day-1 labels to base name
    _EY_DAY1_ALIAS = {
        "א׳ דסוכות": "סוכות",
        "א׳ דפסח": "פסח",
        "א׳ דשבועות": "שבועות",
        # deliberately NOT collapsing R"H:
        # "א׳ דראש השנה": "ראש השנה",
    }

    @staticmethod
    def _ey_collapse_day1_label(name: str) -> str:
        return HolidaySensor._EY_DAY1_ALIAS.get(name, name)

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
        cfg = hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora = cfg.get("diaspora", True)

        # Hebrew names
        set_language("he")
        # cache tz/geo after add; placeholders now
        self._tz: ZoneInfo | None = ZoneInfo(hass.config.time_zone)
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        # Restore last state/attributes on startup
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
    
        # Always start from a clean base that includes "Possible states"
        attrs = self._empty_attrs_for_mode()
    
        if last:
            for k, v in (last.attributes or {}).items():
                if k in attrs or k in ("מען פאַסט אויס און", "מען פאַסט אַן און"):
                    attrs[k] = v
            self._attr_native_value = last.state or ""
        else:
            self._attr_native_value = ""
            
        attrs["Possible states"] = self._possible_states_for_mode()
        self._attr_extra_state_attributes = attrs
    
        # cache geo once
        self._geo = await get_geo(self.hass)

        # immediate first calculation so UI isn’t stale after restore
        await self.async_update()

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
        """Values visible in the HA UI select; filtered by mode."""
        return self._possible_states_for_mode() + [""]

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None:
            return

        # require cached geo; if missing (very early), bail gracefully
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz or ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        actual_date = now.date()
        wd = now.weekday()
        cal = ZmanimCalendar(geo_location=self._geo, date=actual_date)
        actual_sunset = cal.sunset().astimezone(tz)

        # Compute roll‐points using rounded candle/havdalah (aligned with other sensors)
        raw_candle_cut = actual_sunset - timedelta(minutes=self._candle_offset)
        raw_havdalah_cut = actual_sunset + timedelta(minutes=self._havdalah_offset)

        candle_cut = _round_half_up(raw_candle_cut)
        havdalah_cut = _round_ceil(raw_havdalah_cut)

        # festival_date rolls at havdalah
        if now >= havdalah_cut:
            festival_date = actual_date + timedelta(days=1)
        else:
            festival_date = actual_date

        wd_fest = wd if festival_date == actual_date else (wd + 1) % 7
        # Eve of the festival day is Shabbos?
        eve_is_shabbos = ((festival_date - timedelta(days=1)).weekday() == 5)

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
        cal_fest = ZmanimCalendar(geo_location=self._geo, date=festival_date)
        prev_cal = ZmanimCalendar(geo_location=self._geo, date=festival_date - timedelta(days=1))
        next_cal = ZmanimCalendar(geo_location=self._geo, date=festival_date + timedelta(days=1))

        prev_sunset_raw = prev_cal.sunset().astimezone(tz)
        festival_sunset_raw = cal_fest.sunset().astimezone(tz)
        next_sunset_raw = next_cal.sunset().astimezone(tz)

        tomorrow_cal = ZmanimCalendar(geo_location=self._geo, date=actual_date + timedelta(days=1))
        tomorrow_sunset_raw = tomorrow_cal.sunset().astimezone(tz)
        
        # Convenience aliases (raw sunsets)
        prev_sunset = prev_sunset_raw
        festival_sunset = festival_sunset_raw
        next_sunset = next_sunset_raw
        tomorrow_sunset = tomorrow_sunset_raw

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
        
        # Engage only when TODAY's halachic date (hd_fest) is the day *before* a chag
        # and that eve is actually Shabbos. We hold off until havdalah.
        gate_motzaei_shabbos = (
            (wd_fest == 5) and (now < havdalah_cut) and (
                (hd_fest.month == 1 and hd_fest.day == 14) or  # Erev Pesach on Shabbos
                (hd_fest.month == 3 and hd_fest.day == 5)      # Erev Shavuos on Shabbos
            )
        )

        # Debug Hebrew date
        #_LOGGER.debug(f"Current time: {now}, Hebrew date (hd_py): {hd_py.month}/{hd_py.day}, "
        #              f"hd_fest: {hd_fest.month}/{hd_fest.day}, hd_py_fast: {hd_py_fast.month}/{hd_py_fast.day}")

        # Build windows (rounded to match Erev/Motzei / DayType behavior)
        candle_havdalah_start = _round_half_up(
            prev_sunset_raw - timedelta(minutes=self._candle_offset)
        )
        candle_havdalah_end = _round_ceil(
            festival_sunset_raw + timedelta(minutes=self._havdalah_offset)
        )

        candle_both_start = _round_half_up(
            prev_sunset_raw - timedelta(minutes=self._candle_offset)
        )
        candle_both_end = _round_ceil(
            next_sunset_raw + timedelta(minutes=self._havdalah_offset)
        )

        alos_havdalah_start = dawn  # already rounded above
        alos_havdalah_end = _round_ceil(
            festival_sunset_raw + timedelta(minutes=self._havdalah_offset)
        )

        alos_candle_start = dawn
        alos_candle_end = _round_half_up(
            festival_sunset_raw - timedelta(minutes=self._candle_offset)
        )

        candle_alos_start = _round_half_up(
            prev_sunset_raw - timedelta(minutes=self._candle_offset)
        )
        candle_alos_end = dawn

        shabbat_second = festival_date.weekday() == 5
        if shabbat_second:
            # Second day is Shabbos → start at Friday candles
            havdalah_havdalah_start = _round_half_up(
                prev_sunset_raw - timedelta(minutes=self._candle_offset)
            )
        else:
            # Normal case → start at Motzei previous day
            havdalah_havdalah_start = _round_ceil(
                prev_sunset_raw + timedelta(minutes=self._havdalah_offset)
            )
        havdalah_havdalah_end = _round_ceil(
            festival_sunset_raw + timedelta(minutes=self._havdalah_offset)
        )

        havdalah_candle_start = _round_ceil(
            prev_sunset_raw + timedelta(minutes=self._havdalah_offset)
        )
        havdalah_candle_end = _round_half_up(
            festival_sunset_raw - timedelta(minutes=self._candle_offset)
        )

        candle_candle_start = _round_half_up(
            prev_sunset_raw - timedelta(minutes=self._candle_offset)
        )
        candle_candle_end = _round_half_up(
            tomorrow_sunset_raw - timedelta(minutes=self._candle_offset)
        )

        # leap-year for Shovavim
        year = hd_py.year
        is_leap = ((year * 7 + 1) % 19) < 7
        

        # --- Purim-on-Friday detection (used for window overrides) ---
        adar_month = 13 if is_leap else 12
        purim_friday = PHebrewDate(hd_fest.year, adar_month, 14).to_pydate().weekday() == 4  # Fri

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
        attrs = self._empty_attrs_for_mode()
        attrs["Possible states"] = self._possible_states_for_mode()

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
            # Sukkos day 2 (16 Tishrei)
            if self._diaspora and ((hd_fest.month == 7 and hd_fest.day == 16) or (hd_havdalah.month == 7 and hd_havdalah.day == 16)):
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
            # Sukkos Asru-Chag: 24 Tishrei (galus) vs 23 Tishrei (Israel)
            if (self._diaspora and hd_fest.day == 24) or (not self._diaspora and hd_fest.day == 23):
                attrs["אסרו חג סוכות"] = True

        # ─── Chanukah (8-day span from 25 Kislev) ─────────────────────────
        # Do NOT hardcode Kislev/Tevet day numbers; Kislev can be 29 or 30.
        chan_first_py = PHebrewDate(hd_fest.year, 9, 25).to_pydate()
        days_into_chan = (festival_date - chan_first_py).days
        in_chanukah = 0 <= days_into_chan <= 7

        # Erev Chanukah = 24 Kislev
        if hd_fest.month == 9 and hd_fest.day == 24:
            attrs["ערב חנוכה"] = True

        if in_chanukah:
            attrs["חנוכה"] = True

            # Day labels 1–7
            chan_day_letters = ["א׳", "ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ז׳"]
            if 0 <= days_into_chan <= 6:
                attrs[f"{chan_day_letters[days_into_chan]} דחנוכה"] = True

            # Zot Chanukah = day 8
            if days_into_chan == 7:
                attrs["זאת חנוכה"] = True
        
        # Friday daytime of Chanukah: alos → candle
        if in_chanukah and wd_py == 4:
            attrs["ערב שבת חנוכה"] = True

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
            attrs["חמשה עשר בשבט"] = True

        # Purim — Taanit Esther (pushed to 11 Adar when 13 Adar is Shabbat)
        if hd_fest.month in (12, 13):
            thirteen_adar_py = PHebrewDate(hd_fest.year, adar_month, 13).to_pydate()
            taanit_pushed = thirteen_adar_py.weekday() == 5  # 13 Adar is Shabbat

            if taanit_pushed:
                if hd_fest.day == 11 and dawn <= now <= end_time:
                    attrs["תענית אסתר"] = True
                    attrs["תענית אסתר מוקדם"] = True
            else:
                if hd_fest.day == 13 and dawn <= now <= end_time:
                    attrs["תענית אסתר"] = True
            if hd_fest.day == 14:
                attrs["פורים"] = True
            if hd_fest.day == 15:
                attrs["שושן פורים"] = True
        # --- Purim-on-Friday: defer Shushan Purim to Motzaei Shabbos → Motzaei Sunday ---
        if purim_friday:
            fifteen_py = PHebrewDate(hd_fest.year, adar_month, 15).to_pydate()
            if fifteen_py.weekday() == 5:  # 15 Adar is Shabbos
                sat_sunset = ZmanimCalendar(geo_location=self._geo, date=fifteen_py).sunset().astimezone(tz)
                sun_sunset = ZmanimCalendar(geo_location=self._geo, date=fifteen_py + timedelta(days=1)).sunset().astimezone(tz)

                shushan_start = _round_ceil(
                    sat_sunset + timedelta(minutes=self._havdalah_offset)
                )  # Motzaei Shabbos
                shushan_end = _round_ceil(
                    sun_sunset + timedelta(minutes=self._havdalah_offset)
                )  # Motzaei Sunday
        
                # Only show "שושן פורים" in that deferred window; otherwise suppress it
                attrs["שושן פורים"] = (shushan_start <= now <= shushan_end)

        # Bedikat Chametz
        if is_bedikat_day:
            if prev_sunset <= now < dawn:
                attrs["ליל בדיקת חמץ"] = True

        # Pesach & Erev
        if hd_py.month == 1:
            # 1) Friday before when Erev Pesach falls on Shabbos
            if is_erev_pesach_on_shabbos and (hd_py.month == 1 and hd_py.day == 13):
                attrs["ערב פסח מוקדם"] = True
            
            # 2) The Shabbos that *is* Erev Pesach – cover all three rollovers:
            #   • candle-rolled (right after candle time Fri),
            #   • sunset-rolled (Fri night),
            #   • havdalah-rolled (Shabbos day until havdalah).
            if is_erev_pesach_on_shabbos and (
                (wd_py     == 5 and hd_py.month     == 1 and hd_py.day     == 14) or  # from candle time Fri
                (wd_sunset == 5 and hd_sunset.month == 1 and hd_sunset.day == 14) or  # Fri night (after sunset)
                (wd_fest   == 5 and hd_fest.month   == 1 and hd_fest.day   == 14)     # Shabbos day (until havdalah)
            ):
                attrs["שבת ערב פסח"] = True

            # Turn on Erev Pesach on 14 Nisan (normal) OR on 13 Nisan when 14 falls on Shabbos (מוקדם)
            if (hd_py.day == 14) or (is_erev_pesach_on_shabbos and hd_py.day == 13):
                attrs["ערב פסח"] = True
            # PESACH day 1
            if (hd_py.day == 15) or (hd_fest.month == 1 and hd_fest.day == 15):
                if not gate_motzaei_shabbos:
                    attrs["פסח א׳"] = True
                    attrs["פסח א׳ וב׳"] = True
            # Pesach day 2 (16 Nisan)
            if self._diaspora and ((hd_fest.month == 1 and hd_fest.day == 16) or (hd_havdalah.month == 1 and hd_havdalah.day == 16)):
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
            if self._diaspora and ((hd_py.month == 1 and hd_py.day == 22) or (hd_fest.month == 1 and hd_fest.day == 22)):
                attrs["אחרון של פסח"] = True
            # Pesach Asru-Chag: 23 Nisan (galus) vs 22 Nisan (Israel)
            if (self._diaspora and hd_fest.month == 1 and hd_fest.day == 23) or (not self._diaspora and hd_fest.month == 1 and hd_fest.day == 22):
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
            # SHAVUOS day 1
            if (hd_py.day == 6) or (hd_fest.month == 3 and hd_fest.day == 6):
                if not gate_motzaei_shabbos:
                    attrs["שבועות א׳"] = True
                    attrs["שבועות א׳ וב׳"] = True
            # Shavuos day 2 (7 Sivan)
            if self._diaspora and ((hd_fest.month == 3 and hd_fest.day == 7) or (hd_havdalah.month == 3 and hd_havdalah.day == 7)):
                attrs["שבועות ב׳"] = True
                attrs["שבועות א׳ וב׳"] = True
            # Shavuos Asru-Chag: 8 Sivan (galus) vs 7 Sivan (Israel)
            if (self._diaspora and hd_fest.month == 3 and hd_fest.day == 8) or (not self._diaspora and hd_fest.month == 3 and hd_fest.day == 7):
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
        if (
            hd_sunset.month == 5
            and hd_sunset.day == 10
            and wd_sunset == 6
            and start_time_fast <= now <= end_time
        ):
            attrs["תשעה באב נדחה"] = True
            attrs["תשעה באב"] = True  # <- keep the generic flag on too
            
         # Tu BiShvat
        if hd_fest.month == 5 and hd_fest.day == 15:
            attrs["ט\"ו באב"] = True
            
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

        # ─── Countdown for fast starts in ───────────────────────────────────
        # Minor fasts: timer starts at tzeis (havdalah) the evening before
        # Major fasts (YK, Tisha B'Av): timer starts at Chatzos HaYom of Erev
        
        minor_fast_dates = [
            (7, gedaliah_day),                    # Tzom Gedaliah
            (10, 10),                             # 10 Tevet
            (4, 17),                              # 17 Tammuz
            (13 if is_leap else 12, 13),          # Ta'anit Esther
        ]
        
        # Check if tomorrow (by halachic day) is a minor fast
        hd_tomorrow = PHebrewDate.from_pydate(festival_date)
        is_tomorrow_minor_fast = any(
            hd_tomorrow.month == m and hd_tomorrow.day == d 
            for m, d in minor_fast_dates
        )
        
        # Check if today (actual date) is a minor fast day (for during-fast display)
        is_today_minor_fast = any(
            hd_py_fast.month == m and hd_py_fast.day == d 
            for m, d in minor_fast_dates
        )
        
        # Compute tomorrow's dawn for minor fasts
        tomorrow_cal_for_dawn = ZmanimCalendar(geo_location=self._geo, date=festival_date)
        next_dawn = tomorrow_cal_for_dawn.sunrise().astimezone(tz) - timedelta(minutes=72)
        if next_dawn.second >= 30:
            next_dawn += timedelta(minutes=1)
        next_dawn = next_dawn.replace(second=0, microsecond=0)
        
        # ─── MINOR FASTS: Pre-fast countdown (tzeis evening before → alos) ───
        no_fast_active_now = not any(attrs.get(f) for f in self.FAST_FLAGS)
        
        # For the window check: handle midnight rollover
        # If we're past midnight but before dawn, we're definitely past tzeis (which was hours ago)
        # so we just need to check now < next_dawn
        past_midnight_before_dawn = (now.hour < 12 and now < next_dawn)
        
        # Normal evening check (same calendar day): after tonight's tzeis
        tonight_tzeis = _round_ceil(actual_sunset + timedelta(minutes=self._havdalah_offset))
        evening_window = (tonight_tzeis <= now)
        
        # Combined: either past midnight before dawn, OR evening after tzeis but before dawn
        in_minor_fast_prewindow = (past_midnight_before_dawn or evening_window) and now < next_dawn
        
        if no_fast_active_now and is_tomorrow_minor_fast and in_minor_fast_prewindow:
            remaining_sec = max(0, (next_dawn - now).total_seconds())
            minutes_remaining = math.ceil(remaining_sec / 60)
            h = minutes_remaining // 60
            m = minutes_remaining % 60
            attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
        
        # ─── MAJOR FASTS: Pre-fast countdown (Chatzos HaYom of Erev → fast start) ───
        # Erev Yom Kippur: countdown from Chatzos until candle lighting
        elif hd_py.month == 7 and hd_py.day == 9:
            chatzos_erev_yk = _compute_chatzos_hayom(self._geo, actual_date, tz)
            if chatzos_erev_yk <= now < candle_cut:
                remaining_sec = max(0, (candle_cut - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # Erev Tisha B'Av: countdown from Chatzos until sunset
        elif hd_fest.month == 5 and hd_fest.day == 8 and now < actual_sunset:
            chatzos_erev_9av = _compute_chatzos_hayom(self._geo, actual_date, tz)
            if chatzos_erev_9av <= now < actual_sunset:
                remaining_sec = max(0, (actual_sunset - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # Erev Tisha B'Av Nidche (when 9 Av is Shabbos, fast is Sunday): 
        # countdown from Chatzos Shabbos until Motzei Shabbos
        elif hd_fest.month == 5 and hd_fest.day == 9 and wd_fest == 5:
            chatzos_shabbos = _compute_chatzos_hayom(self._geo, actual_date, tz)
            motzei_shabbos = _round_ceil(actual_sunset + timedelta(minutes=self._havdalah_offset))
            if chatzos_shabbos <= now < motzei_shabbos:
                remaining_sec = max(0, (motzei_shabbos - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # ─── DURING FAST: countdown to end ───
        # Minor fasts during the fast day itself
        elif is_today_minor_fast and dawn <= now < end_time:
            # Fast is active - no "starts in" countdown needed
            attrs["מען פאַסט אַן און"] = ""
        
        # Yom Kippur during the fast
        elif (hd_py.month == 7 and hd_py.day == 10) or (hd_fest.month == 7 and hd_fest.day == 10):
            attrs["מען פאַסט אַן און"] = ""
        
        # Tisha B'Av during the fast
        elif (hd_fest.month == 5 and hd_fest.day == 9) or \
             (hd_fest.month == 5 and hd_fest.day == 10 and wd_fest == 6):
            attrs["מען פאַסט אַן און"] = ""
        
        else:
            attrs["מען פאַסט אַן און"] = ""
            
        # helper: are we inside עשי"ת right now?
        def _in_ayt_window(now, tz, geo, candle_offset, havdalah_offset) -> bool:
            today = now.date()
            cal = ZmanimCalendar(geo_location=geo, date=today)
            sunrise = cal.sunrise().astimezone(tz)
            sunset  = cal.sunset().astimezone(tz)
            havdala = sunset + timedelta(minutes=havdalah_offset)
            candle  = sunset - timedelta(minutes=candle_offset)
        
            # Hebrew date by *sunset* rollover (for spanning days)
            hd_sun = PHebrewDate.from_pydate(today)
            if now >= sunset:
                hd_sun = hd_sun + 1
        
            # Only 3–9 Tishrei
            if hd_sun.month != 7 or not (3 <= hd_sun.day <= 9):
                return False
        
            # Start only *after* havdalah on Motzaei R"H (the early part of 3 Tishrei is still R"H-night)
            if hd_sun.day == 3 and now < havdala:
                return False
        
            # End at candle-lighting on Erev YK (9 Tishrei)
            if hd_sun.day == 9 and now >= candle:
                return False
        
            return True
            
        if _in_ayt_window(now, tz, self._geo, self._candle_offset, self._havdalah_offset):
            attrs["עשרת ימי תשובה"] = True

        # Dynamic window overrides for the generic Chol HaMo'ed flags
        # Keep them continuous (havdalah→havdalah) on ordinary CH"M days,
        # but cut at candle time on the last CH"M day (Erev YT).
        def _dynamic_window(name: str, default_w: str | None) -> str | None:
            
            # If the first Yom Tov begins Motzaei Shabbos, do NOT start at candles;
            # start at havdalah (Shabbos end) instead.
            if eve_is_shabbos and name in ("פסח א׳", "פסח א׳ וב׳", "שבועות א׳", "שבועות א׳ וב׳"):
                return "havdalah_havdalah"
            # Diaspora: 8th day Pesach can also begin Motzaei Shabbos.
            if eve_is_shabbos and self._diaspora and name == "אחרון של פסח":
                return "havdalah_havdalah"
        
            if name == "חול המועד סוכות":
                return "havdalah_havdalah"  # 17–20 Tishrei continuous
            if name == "חול המועד פסח":
                # last CH"M day is 20 Nisan → cut at candle that evening
                return "havdalah_candle" if (hd_fest.month == 1 and hd_fest.day == 20) else "havdalah_havdalah"
            # --- Purim on Friday: Motzaei Thu → Candle Fri ---
            if name == "פורים" and purim_friday and (hd_fest.month == adar_month) and (hd_fest.day == 14):
                return "havdalah_candle"
                
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
        # "סוכות (כל חג)": 1st two days + entire Chol HaMoed through הושענא רבה
        attrs["סוכות (כל חג)"] = any(attrs.get(n) for n in [
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

        # "פסח (כל חג)": 1st two days + entire Chol HaMoed + שביעי + (אחרון בגלות)
        attrs["פסח (כל חג)"] = any(attrs.get(n) for n in [
            "פסח א׳",
            "פסח ב׳",
            "א׳ דחול המועד פסח",
            "ב׳ דחול המועד פסח",
            "ג׳ דחול המועד פסח",
            "ד׳ דחול המועד פסח",
            "שביעי של פסח",
            "אחרון של פסח",
        ])

        # Single flag for both: שביעי/אחרון של פסח (diaspora only)
        if self._diaspora:
            attrs["שביעי/אחרון של פסח"] = bool(
                attrs.get("שביעי של פסח") or attrs.get("אחרון של פסח")
            )

        # ─── Shabbos-based flags: use Fri candle → Sat havdalah window ─────────
        shabbos_pydate: datetime.date | None = None

        # Friday after candle-lighting counts as "in Shabbos" already
        if wd == 4 and now >= candle_cut:
            shabbos_pydate = actual_date + timedelta(days=1)  # Shabbos day (Saturday)

        # Saturday until havdalah is still Shabbos
        elif wd == 5 and now < havdalah_cut:
            shabbos_pydate = actual_date

        hd_shabbos = PHebrewDate.from_pydate(shabbos_pydate) if shabbos_pydate else None
        
        # Shabbos Chanukah flags (Fri candle → Sat havdalah)
        if hd_shabbos:
            chan_first_py_shabbos = PHebrewDate(hd_shabbos.year, 9, 25).to_pydate()
            days_into_chan_shabbos = (shabbos_pydate - chan_first_py_shabbos).days
            if 0 <= days_into_chan_shabbos <= 7:
                attrs["שבת חנוכה"] = True

                # Shabbos Chanukah that is also Rosh Chodesh (exclude RH 1 Tishrei)
                if (hd_shabbos.day in (1, 30)) and not (hd_shabbos.month == 7 and hd_shabbos.day == 1):
                    attrs["שבת חנוכה ראש חודש"] = True

        # CH"M day ranges differ by mode (EY includes day 16; diaspora starts at 17)
        chm_days = (17, 18, 19, 20) if self._diaspora else (16, 17, 18, 19, 20)

        attrs["שבת חול המועד סוכות"] = bool(
            hd_shabbos and hd_shabbos.month == 7 and hd_shabbos.day in chm_days
        )
        attrs["שבת חול המועד פסח"] = bool(
            hd_shabbos and hd_shabbos.month == 1 and hd_shabbos.day in chm_days
        )

        # Shabbos Rosh Chodesh (same Shabbos window logic)
        attrs["שבת ראש חודש"] = bool(
            hd_shabbos
            and (hd_shabbos.day in (1, 30))
            and not (hd_shabbos.month == 7 and hd_shabbos.day == 1)  # exclude RH
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
            MotzeiChanukahSensor,
            MotzeiLagBaOmerSensor,
            MotzeiShushanPurimSensor,
        )
        for cls in [MotzeiYomKippurSensor, MotzeiPesachSensor, MotzeiSukkosSensor,
                    MotzeiShavuosSensor, MotzeiRoshHashanaSensor,
                    MotzeiShivaUsorBTammuzSensor, MotzeiTishaBavSensor,
                    MotzeiChanukahSensor, MotzeiLagBaOmerSensor, MotzeiShushanPurimSensor]:
            motzi = cls(self.hass, self._candle_offset, self._havdalah_offset)
            await motzi.async_update(now)
            attrs[motzi._attr_name] = motzi.is_on
            attrs.update(getattr(motzi, "_attr_extra_state_attributes", {}))

        # --- Extra Erev/Motzei windows (8 flags) ---
        extra_flags = compute_erev_motzei_flags(
            now=now,
            tz=tz,
            geo=self._geo,
            diaspora=self._diaspora,
            candle_offset=self._candle_offset,
            havdalah_offset=self._havdalah_offset,
        )
        for name, val in extra_flags.items():
            # Only attach if this name is known for the current mode
            if name in attrs:
                attrs[name] = val
                
        # --- Motzei should DISPLAY only until 2:00 AM after havdalah ---
        motzei_cutoff_2am = datetime.datetime.combine(
            havdalah_cut.date(), time(2, 0), tz
        )
        # if 2am is earlier than havdalah (normal case), push cutoff to next day
        if motzei_cutoff_2am <= havdalah_cut:
            motzei_cutoff_2am += timedelta(days=1)

        motzei_display_allowed = now < motzei_cutoff_2am

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

        elif motzei_display_allowed and any(attrs.get(name) for name in [
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

        # ---------- Israel post-filter (no logic rewrites, only outcome tweaks) ----------
        if not self._diaspora:
            for second in ("סוכות ב׳", "פסח ב׳", "שבועות ב׳"):
                attrs[second] = False
            attrs["סוכות א׳ וב׳"] = False
            attrs["פסח א׳ וב׳"] = False
            attrs["שבועות א׳ וב׳"] = False

            # CH"M Israel labels
            if hd_fest.month == 7 and 16 <= hd_fest.day <= 19:
                for name in ("א׳ דחול המועד סוכות","ב׳ דחול המועד סוכות","ג׳ דחול המועד סוכות","ד׳ דחול המועד סוכות"):
                    attrs[name] = False
                idx = hd_fest.day - 15  # 1..4
                mapping = {1:"א׳",2:"ב׳",3:"ג׳",4:"ד׳"}
                attrs[f"{mapping[idx]} דחול המועד סוכות"] = True
                attrs["חול המועד סוכות"] = True

            if hd_fest.month == 1 and 16 <= hd_fest.day <= 20:
                for name in ("א׳ דחול המועד פסח","ב׳ דחול המועד פסח","ג׳ דחול המועד פסח","ד׳ דחול המועד פסח","ה׳ דחול המועד פסח"):
                    attrs[name] = False
                idx = hd_fest.day - 15
                mapping = {1:"א׳",2:"ב׳",3:"ג׳",4:"ד׳",5:"ה׳"}
                attrs[f"{mapping[idx]} דחול המועד פסח"] = True
                attrs["חול המועד פסח"] = True

            attrs["אסרו חג פסח"] = attrs["אסרו חג פסח"] or (hd_fest.month == 1 and hd_fest.day == 22)
            attrs["אסרו חג שבועות"] = (hd_fest.month == 3 and hd_fest.day == 7)
            attrs["אסרו חג סוכות"] = (hd_fest.month == 7 and hd_fest.day == 23)

            if hd_fest.month == 7 and hd_fest.day == 22:
                attrs["שמחת תורה"] = True

            if picked in ("שמיני עצרת", "שמחת תורה"):
                picked = "שמיני עצרת/שמחת תורה"
            picked = self._ey_collapse_day1_label(picked)

        # Prune to mode after all flags are computed
        attrs = self._prune_attrs_for_mode(attrs)

        self._attr_native_value = picked
        self._attr_extra_state_attributes = attrs
