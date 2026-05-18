from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator

# Hebrew labels read out of the coordinator window. Must match the
# labels produced by zman_compute.compute_zmanim_for_date.
_LABEL = "שקיעת החמה"
_ALOS_LABEL = "עלות השחר"


class ShkiaSensor(YidCalZmanDevice, SensorEntity):
    """שקיעת השמש עפ\"י המג\"א (0°50′ geometric sunset).

    Single-source-of-truth migration: this sensor no longer computes
    its own astronomy. It subscribes to the ZmanimCoordinator (which
    runs zman_compute.compute_zmanim_for_date once per location for a
    civil today-2 … today+1 window) and applies its OWN, unchanged
    rollover rule on read.

    WHY NOT HA's CoordinatorEntity:
    The shared YidCalDevice base (device.py) calls a bare
    ``super().__init__()``. Mixing in ``CoordinatorEntity`` puts it in
    that cooperative __init__ chain, and the bare call re-enters
    ``CoordinatorEntity.__init__`` with no ``coordinator`` arg →
    TypeError at platform setup. device.py is the base for 50+
    sensors and is off-limits for risk, so instead of inheriting
    ``CoordinatorEntity`` we replicate its tiny, stable contract
    manually: keep the coordinator, subscribe on add (auto-removed
    via async_on_remove), mirror ``available`` to the coordinator's
    last refresh success. Behaviorally identical for our use.

    Behavioral contract preserved byte-for-byte vs the
    pre-coordinator sensor:
      • Rollover at Alos HaShachar, NOT civil midnight: between civil
        midnight and that day's Alos the displayed value stays on the
        previous civil day's shkia.
      • State = (rolled-over) today's shkia, ceil-rounded, as UTC.
      • Attributes, in this exact insertion order:
          Shkia_With_Seconds  — unrounded geometric sunset (today),
                                 local-tz ISO (from dt_raw_local)
          Shkia_Simple        — today, honoring the 12/24 option
          Tomorrows_Simple
          Yesterdays_Simple
      • Value flips at Alos: the coordinator's dual-anchor schedule
        fires a refresh at Alos → _handle_coordinator_update → the
        rollover test moves "today" forward. The midnight refresh
        also fires the handler but the rollover test keeps the value
        on the previous day until Alos — identical user-visible
        timing to the old self-scheduled Alos behavior.

    RestoreEntity intentionally NOT used: __init__.py awaits the
    coordinator's first refresh before platforms set up, so
    coordinator.data is always populated here — no restart gap.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:weather-sunset-down"
    _attr_name         = "Shkias HaChamah"
    _attr_unique_id    = "yidcal_zman_shkia"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_shkia"
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
        today_civil = now_local.date()

        alos_today = win.alos_for(today_civil)
        if alos_today is not None and now_local < alos_today:
            today = today_civil - timedelta(days=1)
        else:
            today = today_civil

        e_today = win.entry(_LABEL, today)
        e_yest  = win.entry(_LABEL, today - timedelta(days=1))
        e_tom   = win.entry(_LABEL, today + timedelta(days=1))
        if e_today is None:
            return

        self._attr_native_value = e_today.dt_local.astimezone(timezone.utc)

        full_iso_today = (
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

        self._attr_extra_state_attributes = {
            "Shkia_With_Seconds": full_iso_today,
            "Shkia_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
