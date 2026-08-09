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
from .yidcal_lib.alos_options import (
    ALOS_OPTIONS,
    DEFAULT_ALOS_OPTION,
    get_option,
)
from .yidcal_lib.zman_compute import (
    alos_for_date,
    all_alos_for_date,
    round_half_up as _round_half_up,
)

# Engine label this sensor reads from the coordinator window. Must match
# zman_compute.compute_zmanim_for_date exactly.
_LABEL = "עלות השחר"


class AlosSensor(YidCalZmanDevice, SensorEntity):
    """Alos HaShachar — coordinator-migrated.

    Single source of truth: reads 'עלות השחר' from ZmanimCoordinator's
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
    _attr_icon         = "mdi:weather-sunset-up"
    _attr_name         = "Alos HaShachar"
    _attr_unique_id    = "yidcal_alos"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_alos"
        self.hass = hass
        self._coordinator = get_zmanim_coordinator(hass)
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._option = get_option(cfg.get("alos_method", DEFAULT_ALOS_OPTION))
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

        attrs = {
            "Alos_With_Seconds": full_iso_today,
            "Alos_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
            # Which opinion the STATE above follows.
            "Method": self._option.english,
            "Method_Key": self._option.key,
        }
        attrs.update(self._all_opinions(today))
        self._attr_extra_state_attributes = attrs

    def _all_opinions(self, day) -> dict[str, str]:
        """Today's Alos under every opinion, as ISO timestamps.

        Published whatever the configured method is, so a dashboard can
        show a second opinion — or a household can compare them before
        choosing — without adding a sensor. All fourteen share one cached
        sun-event lookup, so the whole table is about as expensive as any
        single one of them.
        """
        if self._geo is None:
            return {}
        try:
            table = all_alos_for_date(geo=self._geo, tz=self._tz, base_date=day)
        except Exception:  # noqa: BLE001 - the state must not depend on this
            return {}
        return {
            opt.attr: _round_half_up(table[opt.key]).isoformat()
            for opt in ALOS_OPTIONS
            if opt.key in table
        }


class AlosVariantSensor(YidCalZmanDevice, SensorEntity):
    """One extra Alos sensor for a second opinion.

    Created per entry in the ``alos_extra_sensors`` option. Unlike the
    main Alos sensor these do not go through the coordinator — the
    coordinator caches one Alos row (the configured one), and adding a
    row per opinion to a shared four-day window to serve an optional
    sensor would cost every install for the benefit of a few. The
    underlying sun events are cached anyway, so computing here is
    effectively free and keeps the coordinator's contract unchanged.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, hass: HomeAssistant, option_key: str) -> None:
        super().__init__()
        opt = get_option(option_key)
        self._option = opt
        self._attr_unique_id = f"yidcal_alos_{opt.slug}"
        self.entity_id = f"sensor.yidcal_alos_{opt.slug}"
        self._attr_name = f"Alos HaShachar ({opt.english})"

        self.hass = hass
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # Civil-midnight rollover, same as the main Alos sensor, on the
        # shared aligned :00 tick.
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now=None) -> None:
        if self._geo is None:
            return
        today = dt_util.now().astimezone(self._tz).date()

        def raw(day):
            return alos_for_date(
                geo=self._geo, tz=self._tz, base_date=day, option=self._option.key
            )

        try:
            today_raw = raw(today)
            tom_raw = raw(today + timedelta(days=1))
            yest_raw = raw(today - timedelta(days=1))
        except Exception:  # noqa: BLE001
            return

        self._attr_native_value = _round_half_up(today_raw).astimezone(timezone.utc)
        self._attr_extra_state_attributes = {
            "Alos_With_Seconds": today_raw.isoformat(),
            "Alos_Simple": self._format_simple_time(_round_half_up(today_raw)),
            "Tomorrows_Simple": self._format_simple_time(_round_half_up(tom_raw)),
            "Yesterdays_Simple": self._format_simple_time(_round_half_up(yest_raw)),
            "Method": self._option.english,
            "Method_Key": self._option.key,
        }
