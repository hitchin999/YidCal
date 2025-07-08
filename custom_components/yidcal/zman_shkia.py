from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


class ShkiaSensor(YidCalDevice, RestoreEntity, SensorEntity):
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

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        cal      = ZmanimCalendar(geo_location=self._geo, date=today)
        shkia    = cal.sunset().astimezone(self._tz)

        # expose for debugging
        self._attr_extra_state_attributes = {
            "shkia_with_seconds": shkia.isoformat(),
        }

        # ceil to next minute if there's any seconds, else keep the same minute
        if target.second >= 1:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        self._attr_native_value = shkia.astimezone(timezone.utc)
        
