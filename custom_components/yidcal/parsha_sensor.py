# custom_components/yidcal/parsha_sensor.py
from __future__ import annotations
from datetime import date, timedelta
from .device import YidCalDevice
from .const import DOMAIN

from homeassistant.components.sensor import SensorEntity
from pyluach import dates, parshios

from datetime import timedelta as _timedelta  # to distinguish from pyluach.timedelta


class ParshaSensor(YidCalDevice, SensorEntity):
    """Offline Parsha sensor using pyluach for weekly readings."""

    _attr_name = "Parsha"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, hass) -> None:
        super().__init__()
        slug = "parsha"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._state: str | None = None
        self._last_calculated_date: date | None = None
        # Respect integration setting (default diaspora=True if missing)
        cfg = getattr(hass.data.get(DOMAIN, {}), "get", lambda *_: {})("config") if hasattr(hass.data.get(DOMAIN, {}), "get") else hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora: bool = (cfg.get("diaspora", True) if isinstance(cfg, dict) else True)
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Called when Home Assistant has fully started this entity."""
        await super().async_added_to_hass()
        # Do an initial state calculation immediately:
        await self._update_state()

        # Then schedule a callback every minute so that any manual time jump is detected
        self._register_interval(
            self.hass,
            self._handle_minute_tick,
            _timedelta(minutes=1),
        )

    async def _handle_minute_tick(self, now) -> None:
        """
        Every minute, check if the calendar date has changed from the last time
        we ran. If so, recalculate Parsha. This guarantees that if you manually
        jump the system clock, within 60 seconds the sensor will update.
        """
        today = date.today()
        # If we haven't calculated today yet, or if the date rolled over, update.
        if self._last_calculated_date != today:
            await self._update_state()

    @property
    def state(self) -> str:
        return self._state or ""

    async def _update_state(self) -> None:
        """Recompute which Parsha applies based on the upcoming Shabbat."""
        today = date.today()
        self._last_calculated_date = today

        # Find the next Saturday (weekday==5)
        offset = (5 - today.weekday()) % 7
        shabbat = today + timedelta(days=offset)

        # Use pyluach to get that week's Parsha
        greg = dates.GregorianDate(shabbat.year, shabbat.month, shabbat.day)
        # pyluach uses israel=True/False (inverse of diaspora)
        parsha_indices = parshios.getparsha(greg, israel=not self._diaspora)

        if parsha_indices:
            heb = parshios.getparsha_string(greg, israel=not self._diaspora, hebrew=True) or ""
            # Join double parshiyos with a hyphen for your card formatting
            combined = heb.replace(", ", "-").strip()
            self._state = f"פרשת {combined}" if combined else ""
        else:
            self._state = ""

        # A couple of helpful attributes
        self._attr_extra_state_attributes = {
            "Next_Shabbos_Date": shabbat.isoformat(),
            "Diaspora": self._diaspora,
        }

        # Write to Home Assistant
        self.async_write_ha_state()
