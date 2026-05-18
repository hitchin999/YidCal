from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator
from . import DEFAULT_TALLIS_TEFILIN_OFFSET

# Two engine labels: the Alos this is measured from, and the
# Talis & Tefilin target (Alos + configured offset).
_LABEL = "זמן טלית ותפילין"
_ALOS_LABEL = "עלות השחר"


class ZmanTalisTefilinSensor(YidCalZmanDevice, SensorEntity):
    """זמן נטילת תפילין ותלית ראשונה עפ״י מג״א (Misheyakir) —
    coordinator-migrated.

    SPECIAL (not a standard single-zman sensor):
      • Exposes SIX attributes, including TWO *_With_Seconds values
        (the unrounded Alos AND the unrounded Talis target) and an
        Offset_Minutes attribute — preserved in this exact order:
          Alos_With_Seconds, Tallis_With_Seconds, Tallis_Simple,
          Offset_Minutes, Tomorrows_Simple, Yesterdays_Simple
      • State = today's Talis target, half-up rounded, as UTC.
      • Rollover at CIVIL MIDNIGHT (original behavior; no Alos test).

    The coordinator computes זמן טלית ותפילין with the same configured
    tallis_tefilin_offset this sensor reads, so the target matches;
    עלות השחר supplies the Alos_With_Seconds raw value. Verified
    byte-identical to the pre-coordinator sensor.

    No CoordinatorEntity inheritance / no RestoreEntity — see
    zman_shkia.py for the rationale.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:watch"
    _attr_name         = "Zman Talis & Tefilin"
    _attr_unique_id    = "yidcal_zman_tallis_tefilin"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_zman_tallis_tefilin"
        self.hass = hass
        self._coordinator = get_zmanim_coordinator(hass)
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        # Same key the old sensor used; surfaced as the Offset_Minutes
        # attribute. The coordinator uses this same value to compute
        # the זמן טלית ותפילין target, so they stay consistent.
        self._offset = cfg.get(
            "tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET
        )

    @property
    def available(self) -> bool:
        return (
            self._coordinator is not None
            and self._coordinator.last_update_success
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._coordinator is not None:
            self.async_on_remove(
                self._coordinator.async_add_listener(
                    self._handle_coordinator_update
                )
            )
        self._recompute_from_coordinator()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._recompute_from_coordinator()
        self.async_write_ha_state()

    def _recompute_from_coordinator(self) -> None:
        if self._coordinator is None:
            return
        win = self._coordinator.data
        if win is None:
            return

        # Civil-midnight rollover.
        today = dt_util.now().astimezone(self._tz).date()

        e_today = win.entry(_LABEL, today)
        e_yest  = win.entry(_LABEL, today - timedelta(days=1))
        e_tom   = win.entry(_LABEL, today + timedelta(days=1))
        a_today = win.entry(_ALOS_LABEL, today)
        if e_today is None:
            return

        self._attr_native_value = e_today.dt_local.astimezone(timezone.utc)

        # Two raw values: unrounded Alos, unrounded Talis target.
        alos_iso_today = (
            a_today.dt_raw_local.isoformat()
            if (a_today is not None and a_today.dt_raw_local is not None)
            else (a_today.dt_local.isoformat() if a_today is not None else "")
        )
        target_iso_today = (
            e_today.dt_raw_local.isoformat()
            if e_today.dt_raw_local is not None
            else e_today.dt_local.isoformat()
        )

        human_today = self._format_simple_time(e_today.dt_local)
        human_tom = (
            self._format_simple_time(e_tom.dt_local)
            if e_tom is not None else ""
        )
        human_yest = (
            self._format_simple_time(e_yest.dt_local)
            if e_yest is not None else ""
        )

        # Exact original attribute set and order.
        self._attr_extra_state_attributes = {
            "Alos_With_Seconds": alos_iso_today,
            "Tallis_With_Seconds": target_iso_today,
            "Tallis_Simple": human_today,
            "Offset_Minutes": self._offset,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
