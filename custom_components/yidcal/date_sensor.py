from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun

from homeassistant.const import STATE_UNKNOWN
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_sunset
from homeassistant.helpers.restore_state import RestoreEntity

from pyluach.hebrewcal import Year, HebrewDate as PHebrewDate
from .yidcal_lib.helper import int_to_hebrew

_LOGGER = logging.getLogger(__name__)


def get_hebrew_month_name(month: int, year: int) -> str:
    """
    Map Pyluach month-numbers to Hebrew month names, handling leap years.
    """
    if month == 12:
        return "אדר א׳" if Year(year).leap else "אדר"
    if month == 13:
        return "אדר ב׳"
    return {
        1:  "ניסן", 2:  "אייר", 3:  "סיון", 4:  "תמוז",
        5:  "אב",   6:  "אלול",7:  "תשרי", 8:  "חשון",
        9:  "כסלו",10: "טבת",  11: "שבט",
    }.get(month, "")


class DateSensor(RestoreEntity, SensorEntity):
    """Today’s Hebrew date. flips at sunset+havdalah only."""

    _attr_name = "Date"
    _attr_icon = "mdi:calendar-range"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "date"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"sensor.yidcal_{slug}"
    
        self.hass = hass
        self._havdalah_offset = timedelta(minutes=havdalah_offset)

        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )

        self._state: str | None = None

    def _schedule_update(self, *_args) -> None:
        """Thread-safe scheduling of _update_state on the event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        # 1) restore previous state
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._state = last.state

        # 2) immediate first calculation
        await self._update_state()

        # 3) schedule daily sunset+offset update
        async_track_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )

    @property
    def state(self) -> str:
        return self._state or STATE_UNKNOWN

    async def _update_state(self) -> None:
        """Recompute date based on sunset+offset boundary."""
        now = datetime.now(self._tz)
        s = sun(self._loc.observer, date=now.date(), tzinfo=self._tz)
        switch_time = s["sunset"] + self._havdalah_offset

        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()

        heb = PHebrewDate.from_pydate(py_date)
        day_heb = int_to_hebrew(heb.day)
        month_heb = get_hebrew_month_name(heb.month, heb.year)
        year_num = heb.year % 1000
        year_heb = int_to_hebrew(year_num)

        state = f"{day_heb} {month_heb} {year_heb}"
        state = state.replace("\u05F4", '"').replace("\u05F3", "'")

        self._state = state
        self.async_write_ha_state()
