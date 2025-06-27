from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

# The seven Ushpizin for Sukkot
ISHPIZIN_NAMES = [
    "אברהם",  # 15 Tishrei
    "יצחק",   # 16 Tishrei
    "יעקב",   # 17 Tishrei
    "משה",    # 18 Tishrei
    "אהרן",   # 19 Tishrei
    "יוסף",   # 20 Tishrei
    "דוד",    # 21 Tishrei
]

class IshpizinSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Sensor that shows the current Ushpizin during Sukkot nights.
    State updates at Havdalah (sunset + offset) and shows 'אושפיזא ד<Name>'.
    Attributes include a flag for each possible Ushpizin and possible_states list."""

    _attr_name = "Ishpizin"
    _attr_icon = "mdi:account-group"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._havdalah_offset = havdalah_offset
        self._attr_unique_id = "yidcal_ishpizin"
        self.entity_id = "sensor.yidcal_ishpizin"

        # Initial state
        self._attr_native_value: str = ""
        # Prepare attributes: one boolean per Ushpizin and possible_states
        flags: dict[str, bool] = {f"אושפיזא ד{name}": False for name in ISHPIZIN_NAMES}
        flags["possible_states"] = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES]
        self._attr_extra_state_attributes = flags

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore last state
        last = await self.async_get_last_state()
        if last:
            self._attr_native_value = last.state or ""
            self._attr_extra_state_attributes = dict(last.attributes)
        # Initial update and 1-minute polling
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)

        # Determine Hebrew year
        today = now.date()
        heb_year = PHebrewDate.from_pydate(today).year

        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )

        state: str = ""
        # Reset attributes
        attrs: dict[str, bool | list[str]] = {key: False for key in self._attr_extra_state_attributes}
        attrs["possible_states"] = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES]

        # Iterate through days 15–21 Tishrei
        for offset, name in enumerate(ISHPIZIN_NAMES, start=15):
            # Convert Hebrew date to Gregorian
            date_day = PHebrewDate(heb_year, 7, offset).to_pydate()
            # Havdalah start for that day: sunset + offset
            s = sun(loc.observer, date=date_day, tzinfo=tz)
            off_time = s["sunset"] + timedelta(minutes=self._havdalah_offset)
            # Havdalah start of next day
            next_day = date_day + timedelta(days=1)
            s2 = sun(loc.observer, date=next_day, tzinfo=tz)
            next_off = s2["sunset"] + timedelta(minutes=self._havdalah_offset)

            if now >= off_time and now < next_off:
                state = f"אושפיזא ד{name}"
                attrs[state] = True
                break

        self._attr_native_value = state
        self._attr_extra_state_attributes = attrs
      
