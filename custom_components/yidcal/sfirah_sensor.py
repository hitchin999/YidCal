# custom_components/yidcal/sfirah_sensor.py
import logging
import unicodedata
from datetime import timedelta
from typing import Optional

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_sunset,
)

from .device import YidCalDisplayDevice
from .yidcal_lib.sfirah_helper import SfirahHelper

_LOGGER = logging.getLogger(__name__)


class BaseSefirahSensor(YidCalDisplayDevice, SensorEntity):
    """Base class for Sefirah (Omer) sensors."""
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        helper: SfirahHelper,
        name: str,
        unique_id: str,
        strip_nikud: bool,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._helper = helper
        self._strip = strip_nikud
        self._havdalah_offset = havdalah_offset

        self._state: Optional[str] = None
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._attr_icon = "mdi:counter"

        self._added = False

    @property
    def native_value(self):
        return self._state

    @property
    def icon(self) -> str:
        return self._attr_icon

    async def async_added_to_hass(self) -> None:
        """Register timers and do an initial update."""
        await super().async_added_to_hass()
        self._added = True

        # Immediate state
        await self.async_update()

        # Minute tick – SAFE callback
        @callback
        def _on_minute(_now) -> None:
            # Ask HA to call async_update() on the event loop
            self.async_schedule_update_ha_state(True)

        unsub_min = async_track_time_interval(self.hass, _on_minute, timedelta(minutes=1))
        self._register_listener(unsub_min)

        # Update each day at tzeis = sunset + havdalah_offset – SAFE callback
        @callback
        def _on_tzeis(_now) -> None:
            self.async_schedule_update_ha_state(True)

        unsub_tzeis = async_track_sunset(
            self.hass, _on_tzeis, offset=timedelta(minutes=self._havdalah_offset)
        )
        self._register_listener(unsub_tzeis)

    async def async_update(self) -> None:
        """Fetch new text from the helper and write state."""
        try:
            text = self._get_text()
            if self._strip:
                # Strip nikud (marks) while preserving normal letters
                text = unicodedata.normalize("NFKC", text)
                text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "M")
            self._state = text
        except Exception as e:
            _LOGGER.exception("Failed to compute Sefirah text: %s", e)
            # keep previous state if any
            return

        if self._added:
            self.async_write_ha_state()

    # subclasses must implement
    def _get_text(self) -> str:
        raise NotImplementedError


class SefirahCounter(BaseSefirahSensor):
    """Sensor for the Sefirah count in text."""

    def __init__(
        self,
        hass: HomeAssistant,
        helper: SfirahHelper,
        strip_nikud: bool,
        havdalah_offset: int,
    ) -> None:
        slug = "sefirah_counter"
        super().__init__(
            hass,
            helper,
            "Sefirah Counter",
            f"yidcal_{slug}",
            strip_nikud,
            havdalah_offset,
        )
        # Correct domain for a SensorEntity
        self.entity_id = f"sensor.yidcal_{slug}"

    def _get_text(self) -> str:
        return self._helper.get_sefirah_text()


class SefirahCounterMiddos(BaseSefirahSensor):
    """Sensor for the Sefirah middos (attributes) text."""

    def __init__(
        self,
        hass: HomeAssistant,
        helper: SfirahHelper,
        strip_nikud: bool,
        havdalah_offset: int,
    ) -> None:
        slug = "sefirah_counter_middos"
        super().__init__(
            hass,
            helper,
            "Sefirah Counter Middos",
            f"yidcal_{slug}",
            strip_nikud,
            havdalah_offset,
        )
        # Correct domain for a SensorEntity
        self.entity_id = f"sensor.yidcal_{slug}"

    def _get_text(self) -> str:
        return self._helper.get_middos_text()
