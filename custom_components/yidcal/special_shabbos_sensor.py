# custom_components/yidcal/special_shabbos_sensor.py

from datetime import datetime
import re
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
        "שבת ראש חודש",
        "פורים משולש",
    ]

    def __init__(self):
        super().__init__()
        slug = "special_shabbos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        # internal state string
        self._state: str = ""
        # expose one boolean attribute per possible event + mevorchim helpers
        self._attr_extra_state_attributes: dict[str, bool | str | None] = {}

    @property
    def state(self) -> str:
        return self._state

    async def async_update(self, now: datetime | None = None) -> None:
        """Recompute the sensor state and attributes."""
        try:
            raw = specials.get_special_shabbos_name()
        except Exception:
            raw = ""

        # Keep the state exactly as produced by specials
        self._state = raw

        # Split on ASCII hyphen '-' or Hebrew maqaf '־', tolerating spaces
        parts = [p.strip() for p in re.split(r"\s*[־-]\s*", raw) if p.strip()] if raw else []

        # Standard event booleans
        attrs = {ev: (ev in parts) for ev in self.POSSIBLE_EVENTS}

        # Add Mevorchim info as separate attributes
        is_mev = None
        mev_month = None
        for p in parts:
            if p.startswith("מברכים חודש"):
                is_mev = True
                # "מברכים חודש XXXXX"
                mev_month = p.replace("מברכים חודש", "", 1).strip() or None
                break
        if is_mev is None:
            is_mev = False

        attrs.update({
            "שבת מברכים": is_mev,
            "חודש_מברכים": mev_month,  # e.g. "כסלו", "שבט", etc. or None
        })

        self._attr_extra_state_attributes = attrs
