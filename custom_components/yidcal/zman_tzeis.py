from __future__ import annotations
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo
from .zmanim_coordinator import get_zmanim_coordinator
from .yidcal_lib.tzeis_options import TZEIS_OPTIONS
from .yidcal_lib.zman_compute import (
    all_tzeis_for_date,
    round_ceil as _round_ceil,
)

# Engine label this sensor reads from the coordinator window. Must match
# zman_compute.compute_zmanim_for_date exactly.
_LABEL = "צאת הכוכבים"


class ZmanTziesSensor(YidCalZmanDevice, SensorEntity):
    """Tzies Hakochavim — coordinator-migrated.

    Single source of truth: reads 'צאת הכוכבים' from ZmanimCoordinator's
    cached window instead of computing its own astronomy. Rollover
    camp: ALOS. Byte-identical output to the pre-coordinator sensor
    (state, attributes, attribute order) — verified by harness.

    No CoordinatorEntity inheritance (the shared YidCalDevice base's
    bare super().__init__() collides with CoordinatorEntity's required
    arg; see zman_shkia.py). The small contract is replicated manually.
    RestoreEntity intentionally dropped: coordinator.data is populated
    before platforms set up (async_start awaits first refresh).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon         = "mdi:star"
    _attr_name         = "Tzies Hakochavim"
    _attr_unique_id    = "yidcal_tzies_hakochavim"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_tzies_hakochavim"
        self.hass = hass
        self._coordinator = get_zmanim_coordinator(hass)
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        # Preserved for signature compatibility with sensor.py's
        # ZmanTziesSensor(hass, havdalah_offset) call. The actual
        # havdalah offset now lives in the coordinator's computation
        # (engine label uses the same cfg havdalah_offset), so this is
        # not re-applied here — kept only so construction doesn't break.
        self._havdalah = cfg.get("havdalah_offset", havdalah_offset)
        # Needed only for the every-opinion attribute table; the state
        # still comes from the coordinator.
        self._geo = None

    @property
    def available(self) -> bool:
        return (
            self._coordinator is not None
            and self._coordinator.last_update_success
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
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

        # Alos rollover: before today's Alos we still show the
        # previous civil day's value.
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

        attrs = {
            "Tzies_With_Seconds": full_iso_today,
            "Tzies_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
        attrs.update(self._all_opinions(today))
        self._attr_extra_state_attributes = attrs

    def _all_opinions(self, day) -> dict[str, str]:
        """Tonight's Tzeis under every opinion, as ISO timestamps.

        The evening counterpart of the Alos sensor's opinion table, and
        published on the same terms: whatever the configured havdalah
        offset is, so a dashboard can show a second opinion — or a
        household can compare them before choosing — without adding a
        sensor. All of them share one cached sun-event lookup, so the
        whole table is about as expensive as any single one.

        ``day`` is the sensor's ALREADY rollover-adjusted day, so the
        table always describes the same night as the state above.

        Rounded with ceil, not half-up: tzeis is an end-of-window time
        and the house chumra applies to every opinion here exactly as
        it does to the state. Degree opinions the sun never reaches
        tonight are absent rather than approximated (see
        ``all_tzeis_for_date``), so consumers must tolerate a missing
        key — at this latitude 26° drops out near the solstice.
        """
        if self._geo is None:
            return {}
        try:
            table = all_tzeis_for_date(geo=self._geo, tz=self._tz, base_date=day)
        except Exception:  # noqa: BLE001 - the state must not depend on this
            return {}
        return {
            opt.attr: _round_ceil(table[opt.key]).isoformat()
            for opt in TZEIS_OPTIONS
            if opt.key in table
        }
