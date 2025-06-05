#custom_components/yidcal/special_shabbos_sensor.py
from homeassistant.components.sensor import SensorEntity
from .yidcal_lib import specials
from .device import YidCalDevice

async def async_setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Special Shabbos sensor."""
    add_entities([SpecialShabbosSensor()], update_before_add=True)

class SpecialShabbosSensor(YidCalDevice, SensorEntity):
    """Sensor that provides the upcoming special Shabbatot."""
    _attr_name = "Special Shabbos"
    _attr_icon = "mdi:calendar-star"  # icon for a special event

    def __init__(self):
        super().__init__()
        slug = "special_shabbos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"sensor.yidcal_{slug}"
        self._state = None

    @property
    def state(self):
        """Return the state of the sensor (Hebrew string of special Shabbatot)."""
        return self._state

    def update(self):
        """Update the sensor state by computing the upcoming Shabbat specials."""
        try:
            # Call the rule engine to get the special Shabbat name (or empty string)
            self._state = specials.get_special_shabbos_name()
        except Exception as e:
            # In case of any calculation errors, set state to empty
            self._state = ""
