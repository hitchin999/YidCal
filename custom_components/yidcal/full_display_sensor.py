from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_interval


class FullDisplaySensor(SensorEntity):
    """
    Combines day label Yiddish, parsha, holiday (from YOUR list via yidcal_holiday attrs),
    R"Chodesh, and special Shabbos into one filtered string matching the original card formatting.
    """
    _attr_name = "Full Display"

    # ONLY show these holidays
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

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "full_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"
    
        self.hass = hass
        self._state = ""
        async_track_time_interval(hass, self.async_update, timedelta(minutes=1))

    @property
    def native_value(self) -> str:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)

        # 1) Day label
        day = self.hass.states.get("sensor.yidcal_day_label_yiddish")
        text = day.state if day and day.state else ""

        # 2) Parsha (skip if “none”/empty)
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

        # 5) Special Shabbos after Fri-13:00 or any Sat
        special = self.hass.states.get("sensor.yidcal_special_shabbos")
        if special and special.state not in ("No data", ""):
            wd, hr = now.weekday(), now.hour
            if (wd == 4 and hr >= 13) or wd == 5:
                text += f" ~ {special.state}"

        self._state = text
