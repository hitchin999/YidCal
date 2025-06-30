from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDevice

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_interval

from .device import YidCalDevice
from .const import DOMAIN
from . import DEFAULT_DAY_LABEL_LANGUAGE
from homeassistant.const import STATE_UNKNOWN


class FullDisplaySensor(YidCalDevice, SensorEntity):
    """
    Combines day label Yiddish, parsha, holiday (from YOUR list via yidcal_holiday attrs),
    R"Chodesh, special Shabbos, and—if any other motzei sensor is ON—adds that as well.
    It will *not* show motzei for 17 Tammuz (Shi’vah Usor b’Tammuz) or Tisha B’av.
    """
    _attr_name = "Full Display"

    # ONLY show these holidays
    ALLOWED_HOLIDAYS: set[str] = {
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
        "מוצאי פסח",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",
        "שבועות ב׳",
        "מוצאי שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה",
        "מוצאי תשעה באב",
    }

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "full_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._state = ""

        # Read user choice for day-label language
        cfg = hass.data[DOMAIN]["config"]
        self._day_label_language = cfg.get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE)
        self._include_date      = cfg.get("include_date", False)

    async def async_added_to_hass(self) -> None:
        """Register initial update and start once-per-minute polling."""
        await super().async_added_to_hass()
        await self.async_update()
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    @property
    def native_value(self) -> str:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)

        # 1) Day label (Yiddish or Hebrew per user choice)
        label_entity = f"sensor.yidcal_day_label_{self._day_label_language}"
        day = self.hass.states.get(label_entity)
        text = day.state if day and day.state else ""

        # 2) Parsha
        parsha = self.hass.states.get("sensor.yidcal_parsha")
        if parsha:
            st = parsha.state.strip().lower()
            if st and st != "none":
                text += f" {parsha.state}"

        # 3) Holiday via yidcal_holiday attrs
        hol = self.hass.states.get("sensor.yidcal_holiday")
        picked = None
        if hol:
            for name, val in hol.attributes.items():
                if val is True and name in self.ALLOWED_HOLIDAYS:
                    picked = name
                    break
        if picked:
            text += f" - {picked}"
            
        # 4) Rosh Chodesh
        rosh = self.hass.states.get("sensor.yidcal_rosh_chodesh_today")
        if rosh and rosh.state != "Not Rosh Chodesh Today":
            text += f" ~ {rosh.state}"

        # 5) Special Shabbos (after Fri-13:00 or any Sat)
        special = self.hass.states.get("sensor.yidcal_special_shabbos")
        if special and special.state not in ("No data", ""):
            wd, hr = now.weekday(), now.hour
            if (wd == 4 and hr >= 13) or wd == 5:
                text += f" ~ {special.state}"
        # 6) Optional “today’s date”
        if self._include_date:
            date_ent = self.hass.states.get("sensor.yidcal_date")
            if date_ent and date_ent.state not in (None, "", STATE_UNKNOWN):
                text += f" - {date_ent.state}"
                
        self._state = text
