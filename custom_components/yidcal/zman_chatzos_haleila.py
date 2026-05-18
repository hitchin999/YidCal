from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator

_LABEL = "חצות הלילה"
_ALOS_LABEL = "עלות השחר"


class ChatzosHaLailaSensor(YidCalZmanDevice, SensorEntity):
    """Chatzos HaLaila — coordinator-migrated.

    SPECIAL (which-night rollover):
      • The displayed value is the chatzos of the night that BEGAN at
        a given civil date's sunset. The "halachic base date" is:
        if now < that civil day's Alos → the night is still last
        night → base = yesterday; otherwise base = today. So 11:56 PM
        Mon and 12:01 AM Tue both resolve to Monday's night (same
        chatzos for the same halachic night). This is the original
        _halachic_base_date logic, preserved exactly.
      • The engine's חצות הלילה is computed for the night beginning at
        `base`'s sunset (night_start = sunset+72 → next dawn,
        midpoint), which is exactly the old formula — verified
        byte-identical.
      • State = base-night chatzos, half-up rounded, as UTC.
      • Attributes (exact order):
          Chatzos_Haleila_With_Seconds  (unrounded, from dt_raw_local)
          Chatzos_Haleila_Simple
          Tomorrows_Simple   (next night)
          Yesterdays_Simple  (previous night)

    The coordinator's 4-day window (civil today-2 … today+1) always
    covers base-1, base, base+1 for any base the rollover picks.

    No CoordinatorEntity inheritance / no RestoreEntity — see
    zman_shkia.py for the rationale.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:weather-night"
    _attr_name         = "Chatzos HaLaila"
    _attr_unique_id    = "yidcal_chatzos_haleila"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_chatzos_haleila"
        self.hass = hass
        self._coordinator = get_zmanim_coordinator(hass)
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

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

        now_local = dt_util.now().astimezone(self._tz)
        today = now_local.date()

        # Halachic base date: before today's Alos we are still in
        # last night, so the base is yesterday; else today.
        alos_today = win.alos_for(today)
        if alos_today is not None and now_local < alos_today:
            base = today - timedelta(days=1)
        else:
            base = today

        e_base = win.entry(_LABEL, base)
        e_last = win.entry(_LABEL, base - timedelta(days=1))
        e_tom  = win.entry(_LABEL, base + timedelta(days=1))
        if e_base is None:
            return

        self._attr_native_value = e_base.dt_local.astimezone(timezone.utc)

        tonight_iso = (
            e_base.dt_raw_local.isoformat()
            if e_base.dt_raw_local is not None
            else e_base.dt_local.isoformat()
        )
        human_tonight = self._format_simple_time(e_base.dt_local)
        human_tomorrow = (
            self._format_simple_time(e_tom.dt_local)
            if e_tom is not None else ""
        )
        human_last = (
            self._format_simple_time(e_last.dt_local)
            if e_last is not None else ""
        )

        self._attr_extra_state_attributes = {
            "Chatzos_Haleila_With_Seconds": tonight_iso,
            "Chatzos_Haleila_Simple":       human_tonight,
            "Tomorrows_Simple":             human_tomorrow,
            "Yesterdays_Simple":            human_last,
        }
