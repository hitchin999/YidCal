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


class NetzSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """נץ החמה עפ\"י המג\"א (0°50′ sunrise)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:weather-sunset-up"
    _attr_name          = "Netz HaChamah"
    _attr_unique_id     = "yidcal_netz"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "netz"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
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
        sunrise  = cal.sunrise().astimezone(self._tz)

        target   = sunrise

        # Debug attrs
        self._attr_extra_state_attributes = {
            "netz_with_seconds": sunrise.isoformat(),
        }

        # custom rounding: <30 s floor, ≥30 s ceil
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        self._attr_native_value = target.astimezone(timezone.utc)
