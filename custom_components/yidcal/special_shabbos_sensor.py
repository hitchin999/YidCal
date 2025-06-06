# custom_components/yidcal/special_shabbos_sensor.py

from datetime import datetime
from homeassistant.components.sensor import SensorEntity
from .yidcal_lib import specials
from .device import YidCalDevice

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up SpecialShabbosSensor via config entry."""
    async_add_entities([SpecialShabbosSensor()], update_before_add=True)


class SpecialShabbosSensor(YidCalDevice, SensorEntity):
    """Sensor that provides the upcoming special Shabbatot."""

    _attr_name = "Special Shabbos"
    _attr_icon = "mdi:calendar-star"

    def __init__(self):
        super().__init__()
        slug = "special_shabbos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._state = None

    @property
    def state(self):
        """Return the state of the sensor (Hebrew string of special Shabbatot)."""
        return self._state

    async def async_update(self, now: datetime | None = None) -> None:
        """Recompute the sensor state once per update call."""
        try:
            self._state = specials.get_special_shabbos_name()
        except Exception:
            self._state = ""
