# custom_components/yidcal/fast_timer_sensors.py
"""The fast countdown sensors — classic ticking state, silent logs.

sensor.yidcal_fast_starts_in / sensor.yidcal_fast_ends_in show the
same state they always did: the ticking countdown text ("02:13" →
"02:12" → …) mirrored from the holiday sensor's מען פאַסט אַן און /
מען פאַסט אויס און attributes, empty when no fast window is active.

What's new is that the ticking no longer floods anything:

  • RECORDER / History: quiet_recorder.py drops these two entity_ids
    inside the recorder's own event processing, so no database rows
    are ever written — no user configuration.yaml editing needed.

  • LOGBOOK (live stream): the sensors carry a ``unit_of_measurement``
    attribute (empty string), which Home Assistant's logbook treats as
    a "continuous sensor" and filters out — verified against current
    core (logbook/helpers.py::is_sensor_continuous). The DB-backed
    logbook/history pages are already covered by the recorder drop.

  • The ``target`` attribute holds the absolute fast boundary (ISO),
    handy for cards/automations (e.g. triggering exactly at the
    fast's start/end without template gymnastics).

If the recorder guard ever fails on a future HA version, everything
still works — the sensors just record like they did before 0.7.8.
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from .device import YidCalDisplayDevice

# Holiday-sensor attribute keys
ATTR_STARTS_AT = "fast_starts_at"            # stable ISO ts
ATTR_ENDS_AT = "fast_ends_at"                # stable ISO ts
ATTR_TEXT_STARTS = "מען פאַסט אַן און"        # ticking text
ATTR_TEXT_ENDS = "מען פאַסט אויס און"         # ticking text

# Entity ids for quiet_recorder (imported by __init__.py)
SILENCED_ENTITY_IDS = {
    "sensor.yidcal_fast_starts_in",
    "sensor.yidcal_fast_ends_in",
}


class _FastCountdownBase(YidCalDisplayDevice, SensorEntity):
    """Base for the fast countdown mirror sensors."""

    def __init__(
        self,
        hass: HomeAssistant,
        slug: str,
        name: str,
        ts_key: str,
        text_key: str,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_name = name
        self._attr_native_value: str | None = ""
        self._ts_key = ts_key
        self._text_key = text_key
        # NOTE: the empty-string unit_of_measurement is deliberate —
        # its mere PRESENCE makes the logbook classify this sensor as
        # continuous and skip it (live mode checks the attribute).
        # It is a plain extra attribute, NOT native_unit_of_measurement,
        # so the sensor platform's numeric-state validation never runs.
        self._attr_extra_state_attributes: dict = {
            "unit_of_measurement": "",
            "target": "",
        }

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
        text = (holiday.attributes.get(self._text_key) or "") if holiday else ""
        target = (holiday.attributes.get(self._ts_key) or "") if holiday else ""

        # Preserve the classic behavior: ticking HH:MM, "" when idle
        self._attr_native_value = text
        self._attr_extra_state_attributes = {
            "unit_of_measurement": "",
            "target": target,
        }


class FastStartCountdownSensor(_FastCountdownBase):
    """Mirror of 'מען פאַסט אַן און' as its own display sensor."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass=hass,
            slug="fast_starts_in",
            name="Fast Starts In",
            ts_key=ATTR_STARTS_AT,
            text_key=ATTR_TEXT_STARTS,
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
            ts_key=ATTR_ENDS_AT,
            text_key=ATTR_TEXT_ENDS,
        )

    @property
    def icon(self) -> str:
        return "mdi:timer-sand-complete"
