from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator

# Engine label this sensor reads from the coordinator window. Must match
# zman_compute.compute_zmanim_for_date exactly.
_LABEL = "סוף זמן קריאת שמע גר״א"


class SofZmanKriasShmaGRASensor(YidCalZmanDevice, SensorEntity):
    """Sof Zman Krias Shma (GRA) — coordinator-migrated.

    Single source of truth: reads 'סוף זמן קריאת שמע גר״א' from ZmanimCoordinator's
    cached window instead of computing its own astronomy. Rollover
    camp: MIDNT. Byte-identical output to the pre-coordinator sensor
    (state, attributes, attribute order) — verified by harness.

    No CoordinatorEntity inheritance (the shared YidCalDevice base's
    bare super().__init__() collides with CoordinatorEntity's required
    arg; see zman_shkia.py). The small contract is replicated manually.
    RestoreEntity intentionally dropped: coordinator.data is populated
    before platforms set up (async_start awaits first refresh).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:book-open-variant-outline"
    _attr_name         = "Sof Zman Krias Shma (GRA)"
    _attr_unique_id    = "yidcal_sof_zman_krias_shma_gra"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_sof_zman_krias_shma_gra"
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

        # Civil-midnight rollover: "today" is just the civil date.
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
            "Krias_Shma_GRA_With_Seconds": full_iso_today,
            "krias_Shma_GRA_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
