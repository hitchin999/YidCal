# custom_components/yidcal/parsha_sensor.py
from __future__ import annotations
from datetime import date, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change
from pyluach import dates, parshios

class ParshaYiddishSensor(SensorEntity):
    """Offline Parsha sensor using pyluach for weekly readings."""

    _attr_name = "YidCal Parsha"
    _attr_icon = "mdi:book-open-page-variant"
    _attr_unique_id = "yidcal_parsha"

    def __init__(self, hass) -> None:
        super().__init__()
        self.hass = hass
        self._state: str | None = None

    async def async_added_to_hass(self) -> None:
        # Initial update
        await self._update_state()

        # Schedule daily update at 12:00:05 AM
        async_track_time_change(
            self.hass,
            self._handle_time_change,
            hour=0,
            minute=0,
            second=5,
        )

    async def _handle_time_change(self, now) -> None:
        await self._update_state()

    @property
    def state(self) -> str:
        return self._state or "none"

    async def _update_state(self) -> None:
        # Calculate the upcoming Shabbat (Saturday)
        today = date.today()
        offset = (5 - today.weekday()) % 7
        shabbat = today + timedelta(days=offset)

        # Get Parsha from pyluach
        greg = dates.GregorianDate(shabbat.year, shabbat.month, shabbat.day)
        parsha_indices = parshios.getparsha(greg)

        if parsha_indices:
            heb = parshios.getparsha_string(greg, hebrew=True)
            combined = heb.replace(", ", "-")
            self._state = f"פרשת {combined}"
        else:
            self._state = "none"

        self.async_write_ha_state()
        
