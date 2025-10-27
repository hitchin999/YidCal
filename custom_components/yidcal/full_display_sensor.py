from __future__ import annotations
import datetime
from datetime import time
from datetime import timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDisplayDevice

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from . import DEFAULT_DAY_LABEL_LANGUAGE
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation
from .zman_sensors import get_geo


class FullDisplaySensor(YidCalDisplayDevice, SensorEntity):
    """
    Combines day label Yiddish, parsha, holiday (from YOUR list via yidcal_holiday attrs),
    R"Chodesh, special Shabbos, and—if any other motzei sensor is ON—adds that as well.
    It will *not* show motzei for 17 Tammuz (Shi’vah Usor b’Tammuz) or Tisha B’av.
    """
    _attr_name = "Full Display"

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
        self._geo: GeoLocation | None = None
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

    async def async_added_to_hass(self) -> None:
        """Register initial update and start once-per-minute polling."""
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
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
        tz = self._tz
        now = now or datetime.datetime.now(tz)

        def _ok(state: str | None) -> bool:
            if state is None:
                return False
            s = str(state).strip()
            return s not in ("", STATE_UNKNOWN, STATE_UNAVAILABLE, "unknown", "unavailable")

        # 1) Day label (Yiddish or Hebrew per user choice)
        label_entity = f"sensor.yidcal_day_label_{self._day_label_language}"
        day = self.hass.states.get(label_entity)
        text = day.state.strip() if day and _ok(day.state) else ""

        # 2) Parsha (suppress sentinel "None")
        parsha = self.hass.states.get("sensor.yidcal_parsha")
        if parsha and _ok(parsha.state):
            ps = str(parsha.state).strip()
            if ps.lower() not in ("none", "פרשת none"):
                text += f" {ps}"

        # 3) Holiday — single state from sensor.yidcal_holiday
        hol = self.hass.states.get("sensor.yidcal_holiday")
        if hol and _ok(hol.state):
            text += f" - {hol.state.strip()}"
        
        # 3b) Shabbos Erev Pesach: if this Shabbos is Erev Pesach (מוקדם year),
        # surface "ערב פסח" throughout Shabbos.
        if hol and getattr(hol, "attributes", None):
            if hol.attributes.get("שבת ערב פסח", False):
                # avoid duplicating if it somehow already appears
                if "ערב פסח" not in text:
                    text += " ~ ערב פסח"

        # 5) Special Shabbos (after Fri-13:00 or any Sat)
        special = self.hass.states.get("sensor.yidcal_special_shabbos")
        show_special = False
        if special and _ok(special.state):
            sstate = str(special.state).strip()
            if sstate.lower() not in ("no data",):
                wd, hr = now.weekday(), now.hour
                if wd == 4 and hr >= 13:
                    show_special = True
                elif wd == 5 and self._geo:
                    today = now.date()
                    cal = ZmanimCalendar(geo_location=self._geo, date=today)
                    sunset = cal.sunset()
                    if sunset:
                        sunset = sunset.astimezone(tz)
                        havdalah = sunset + timedelta(minutes=72)
                        if now < havdalah:
                            show_special = True
                if show_special:
                    text += f" ~ {sstate}"

        # 4) Rosh Chodesh
        rosh = self.hass.states.get("sensor.yidcal_rosh_chodesh_today")
        if rosh and _ok(rosh.state) and rosh.state != "Not Rosh Chodesh Today":
            # only add if not already covered by "שבת ראש חודש"
            if not (show_special and special and _ok(special.state) and "שבת ראש חודש" in str(special.state)):
                text += f" ~ {rosh.state.strip()}"

        # 6) Optional “today’s date”
        if self._include_date:
            date_ent = self.hass.states.get("sensor.yidcal_date")
            if date_ent and _ok(date_ent.state):
                text += f" - {date_ent.state.strip()}"

        self._state = text
