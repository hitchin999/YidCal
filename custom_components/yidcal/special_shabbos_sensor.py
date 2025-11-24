# custom_components/yidcal/special_shabbos_sensor.py

from datetime import datetime
import re
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.core import callback   # <-- add this

from .yidcal_lib import specials
from .device import YidCalDisplayDevice
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SpecialShabbosSensor()], update_before_add=True)


class SpecialShabbosSensor(YidCalDisplayDevice, SensorEntity):
    _attr_name = "Special Shabbos"
    _attr_icon = "mdi:calendar-star"

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
        self._attr_native_value = ""
        self._attr_extra_state_attributes: dict[str, bool | str | None] = {}

    @callback
    def _handle_midnight(self, now) -> None:
        # thread-safe update trigger
        self.schedule_update_ha_state(True)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()

        unsub = async_track_time_change(
            self.hass,
            self._handle_midnight,
            hour=0, minute=0, second=5
        )
        self._register_listener(unsub)

    async def async_update(self, now: datetime | None = None) -> None:
        cfg = self.hass.data.get(DOMAIN, {}).get("config", {}) or {}
        diaspora = cfg.get("diaspora", True)
        is_in_israel = not diaspora

        try:
            raw = specials.get_special_shabbos_name(is_in_israel=is_in_israel)
        except TypeError:
            raw = specials.get_special_shabbos_name()

        self._attr_native_value = raw or ""

        parts = [p.strip() for p in re.split(r"\s*[־-]\s*", raw) if p.strip()] if raw else []
        attrs = {ev: (ev in parts) for ev in self.POSSIBLE_EVENTS}

        is_mev = False
        mev_month = None
        for p in parts:
            if p.startswith("מברכים חודש"):
                is_mev = True
                mev_month = p.replace("מברכים חודש", "", 1).strip() or None
                break

        attrs.update({
            "שבת מברכים": is_mev,
            "חודש_מברכים": mev_month,
        })

        self._attr_extra_state_attributes = attrs
