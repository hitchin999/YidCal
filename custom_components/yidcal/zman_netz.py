from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator

_LABEL = "הנץ החמה"


class NetzSensor(YidCalZmanDevice, SensorEntity):
    """נץ החמה עפ\"י המג\"א (0°50′ sunrise).

    Single-source-of-truth migration: subscribes to the
    ZmanimCoordinator instead of computing its own astronomy.

    WHY NOT HA's CoordinatorEntity: see zman_shkia.py — the shared
    YidCalDevice base's bare super().__init__() collides with
    CoordinatorEntity's required ``coordinator`` arg. We replicate
    CoordinatorEntity's small, stable contract manually instead, and
    leave the shared device.py untouched.

    Behavioral contract preserved byte-for-byte:
      • Rollover at CIVIL MIDNIGHT (this sensor's original behavior):
        "today" is just now.date(); NO Alos adjustment.
      • State = today's Netz, half-up rounded (<30s floor / ≥30s
        ceil — matches zman_compute._half_up), as UTC.
      • Attributes, in this exact insertion order:
          Netz_With_Seconds  — unrounded sunrise (today), local-tz
                                ISO (from dt_raw_local)
          Netz_Simple        — today, honoring the 12/24 option
          Tomorrows_Simple
          Yesterdays_Simple
      • Timing: the coordinator refreshes at both its midnight and
        Alos anchors. This sensor's rollover key is the civil date,
        so the value changes only when the civil date advances —
        effectively at the midnight refresh. The Alos refresh re-runs
        the handler but yields the same civil date and value, so no
        spurious mid-night change. Identical user-visible timing to
        the old async_track_time_change(hour=0) behavior.

    RestoreEntity intentionally NOT used (see zman_shkia.py).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:weather-sunset-up"
    _attr_name         = "Netz HaChamah"
    _attr_unique_id    = "yidcal_netz"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_netz"
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

        # Civil-midnight rollover: "today" is just the civil date.
        today = dt_util.now().astimezone(self._tz).date()

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
            "Netz_With_Seconds": full_iso_today,
            "Netz_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
