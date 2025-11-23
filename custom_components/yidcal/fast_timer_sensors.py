# custom_components/yidcal/fast_timer_sensors.py
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from .device import YidCalDisplayDevice

# Attribute keys from HolidaySensor
ATTR_FAST_STARTS = "מען פאַסט אַן און"
ATTR_FAST_ENDS = "מען פאַסט אויס און"


class _FastCountdownBase(YidCalDisplayDevice, SensorEntity):
    """Base for countdown mirror sensors that read from HolidaySensor attributes."""

    def __init__(self, hass: HomeAssistant, slug: str, name: str, attr_key: str) -> None:
        super().__init__()
        self.hass = hass
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_name = name
        self._attr_native_value: str | None = ""
        self._attr_key = attr_key

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Initial value
        await self.async_update()

        # Stay in sync once per minute, same cadence as HolidaySensor
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    async def async_update(self, _now=None) -> None:
        holiday = self.hass.states.get("sensor.yidcal_holiday")
        if not holiday:
            # Keep invariant: "" means "no countdown"
            self._attr_native_value = ""
            return

        value = holiday.attributes.get(self._attr_key, "") or ""
        # Preserve the existing behavior: empty string when not active
        self._attr_native_value = value


class FastStartCountdownSensor(_FastCountdownBase):
    """Mirror of 'מען פאַסט אַן און' as its own display sensor."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass=hass,
            slug="fast_starts_in",
            name="Fast Starts In",
            attr_key=ATTR_FAST_STARTS,
        )

    @property
    def icon(self) -> str:
        return "mdi:timer-sand"


class FastEndCountdownSensor(_FastCountdownBase):
    """Mirror of 'מען פאַסט אויס און' as its own display sensor."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass=hass,
            slug="fast_ends_in",
            name="Fast Ends In",
            attr_key=ATTR_FAST_ENDS,
        )

    @property
    def icon(self) -> str:
        return "mdi:timer-sand-complete"
