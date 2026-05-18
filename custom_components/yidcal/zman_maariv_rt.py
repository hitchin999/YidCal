from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zmanim_coordinator import get_zmanim_coordinator

# Engine label added for this sensor: Maariv Rabbeinu Tam = sunset+72,
# ceil-rounded. See zman_compute.compute_zmanim_for_date.
_LABEL = "זמן מעריב ר״ת"


class ZmanMaarivRTSensor(YidCalZmanDevice, SensorEntity):
    """זמן ערבית (ר\"ת: 72 דקות אחרי שקיעה) — coordinator-migrated.

    Maariv R"T = sunset + 72 min, ceil-rounded. This zman did not
    exist in the engine before; it was added to
    zman_compute.compute_zmanim_for_date as זמן מעריב ר״ת (purely
    additive — verified zero regression to all pre-existing labels),
    so this sensor is now a single-source consumer like the rest.

    • Rollover at Alos HaShachar (original behavior): before today's
      Alos the displayed value stays on the previous civil day's
      Maariv R"T.
    • State = (rolled-over) today's value, ceil-rounded, as UTC.
    • Attributes (exact order):
        Maariv_RT_With_Seconds  (unrounded, from dt_raw_local)
        Maariv_RT_Simple
        Tomorrows_Simple
        Yesterdays_Simple

    No CoordinatorEntity inheritance / no RestoreEntity — see
    zman_shkia.py for the rationale.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:clock-check"
    _attr_name         = "Zman Maariv R\"T"
    _attr_unique_id    = "yidcal_zman_maariv_rt"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_zman_maariv_rt"
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

        # Alos rollover.
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
            "Maariv_RT_With_Seconds": full_iso_today,
            "Maariv_RT_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
