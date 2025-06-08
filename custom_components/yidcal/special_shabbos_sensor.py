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
    # fixed list of all possible special-Shabbos events
    POSSIBLE_EVENTS = [
        "שבת שקלים",
        "שבת זכור",
        "שבת החודש",
        "שבת פרה",
        "שבת הגדול",
        "שבת שובה",
        "שבת חזון",
        "שבת נחמו",
        "שבת חזק",
        "פורים משולש",
    ]

    def __init__(self):
        super().__init__()
        slug = "special_shabbos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        # internal state string
        self._state: str = ""
        # expose one boolean attribute per possible event
        self._attr_extra_state_attributes: dict[str, bool] = {}

    @property
    def state(self) -> str:
        return self._state

    async def async_update(self, now: datetime | None = None) -> None:
        """Recompute the sensor state and attributes."""
        try:
            raw = specials.get_special_shabbos_name()
        except Exception:
            raw = ""

        # split the raw hyphen-joined string into individual events
        events = raw.split("־") if raw else []
        # update the sensor state
        self._state = raw
        # build attributes: True if the event is in the upcoming list
        self._attr_extra_state_attributes = {
            ev: (ev in events) for ev in self.POSSIBLE_EVENTS
        }
