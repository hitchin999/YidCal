#/homeassistant/custom_components/yidcal/ishpizin_sensor.py
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

# All possible states for the sensor (enum options)
ISHPIZIN_STATES = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]


def _attrs_for_state(state: str) -> dict:
    """Flags first, then 'Possible states' last (for nice UI ordering)."""
    flags = {f"אושפיזא ד{name}": (state == f"אושפיזא ד{name}") for name in ISHPIZIN_NAMES}
    return {
        **flags,
        "Possible states": ISHPIZIN_STATES,  # durable; HA preserves order
    }


class IshpizinSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Sensor that shows the current Ushpizin during Sukkot nights.
    State updates at Havdalah (sunset + offset) and shows 'אושפיזא ד<Name>'.
    Attributes include a flag for each possible Ushpizin + 'Possible states' list.
    """

    _attr_name = "Ishpizin"
    _attr_icon = "mdi:account-group"
    _attr_device_class = "enum"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._havdalah_offset = havdalah_offset
        self._attr_unique_id = "yidcal_ishpizin"
        self.entity_id = "sensor.yidcal_ishpizin"

        # Initial state + attributes (with 'Possible states' last)
        self._attr_native_value: str = ""
        self._attr_extra_state_attributes = _attrs_for_state(self._attr_native_value)

    @property
    def options(self) -> list[str]:
        """Return list of possible values for Home Assistant automation UI."""
        return ISHPIZIN_STATES

    @property
    def native_value(self) -> str:
        """Return the current state value."""
        return self._attr_native_value

    def _set_state(self, state: str) -> None:
        """Atomic state+attributes setter to avoid dropping keys."""
        self._attr_native_value = state
        self._attr_extra_state_attributes = _attrs_for_state(state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore last valid state; always regenerate attrs to include 'Possible states'
        last = await self.async_get_last_state()
        if last and last.state in ISHPIZIN_STATES:
            self._set_state(last.state)
        else:
            self._set_state("")

        # Initial update and 1-minute polling
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)

        # Determine Hebrew year for Sukkot mapping (Tishrei = month 7)
        today = now.date()
        heb_year = PHebrewDate.from_pydate(today).year

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Default: outside the Sukkot nights window → empty state, flags all False
        chosen_state: str = ""

        # Iterate through the 7 Sukkot nights (15–21 Tishrei)
        for day_num, name in enumerate(ISHPIZIN_NAMES, start=15):
            # Gregorian date for that Hebrew day in this Hebrew year
            gdate = PHebrewDate(heb_year, 7, day_num).to_pydate()

            # Havdalah start for that night: sunset + offset
            s = sun(loc.observer, date=gdate, tzinfo=tz)
            start = s["sunset"] + timedelta(minutes=self._havdalah_offset)

            # End at next night's havdalah (sunset+offset of next day)
            gdate_next = gdate + timedelta(days=1)
            s_next = sun(loc.observer, date=gdate_next, tzinfo=tz)
            end = s_next["sunset"] + timedelta(minutes=self._havdalah_offset)

            if start <= now < end:
                chosen_state = f"אושפיזא ד{name}"
                break

        self._set_state(chosen_state)
