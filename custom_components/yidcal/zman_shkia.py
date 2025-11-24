from __future__ import annotations
from datetime import datetime, timedelta, timezone, date as date_cls
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo


class ShkiaSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """שקיעת השמש עפ\"י המג\"א (0°50′ geometric sunset)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:weather-sunset-down"
    _attr_name          = "Shkias HaChamah"
    _attr_unique_id     = "yidcal_zman_shkia"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "shkia"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # load geo once
        self._geo = await get_geo(self.hass)
        # initial calculation
        await self.async_update()
        # recompute each midnight local
        async_track_time_change(
            self.hass,
            self._midnight_update,
            hour=0, minute=0, second=0,
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    def _compute_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """
        Shkiah for base_date = geometric sunset(base_date),
        state rounded by ceilling to the next minute.
        Returns (rounded_local_dt, precise_unrounded_local_iso_for_today_like_attr).
        """
        assert self._geo is not None
        cal   = ZmanimCalendar(geo_location=self._geo, date=base_date)
        shkia = cal.sunset().astimezone(self._tz)

        # keep unrounded ISO (matches existing 'Shkia_With_Seconds' style)
        full_iso_local = shkia.isoformat()

        # ceil to next minute
        target = (shkia + timedelta(minutes=1)).replace(second=0, microsecond=0)

        return target, full_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        # compute today / yesterday / tomorrow
        local_today_dt, full_iso_today = self._compute_for_date(today)
        local_yest_dt, _               = self._compute_for_date(today - timedelta(days=1))
        local_tom_dt, _                = self._compute_for_date(today + timedelta(days=1))

        # state (UTC)
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # human strings
        human_today = self._format_simple_time(local_today_dt)
        human_tom   = self._format_simple_time(local_tom_dt)
        human_yest  = self._format_simple_time(local_yest_dt)

        # attributes (Tomorrow before Yesterday)
        self._attr_extra_state_attributes = {
            "Shkia_With_Seconds": full_iso_today,  # unrounded geometric sunset (today)
            "Shkia_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
