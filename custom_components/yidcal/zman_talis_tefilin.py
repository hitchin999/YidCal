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
from . import DEFAULT_TALLIS_TEFILIN_OFFSET

# default Alos “MGA” offset (0°50′ = 72 minutes)
DEFAULT_ALOS_OFFSET = 72


class ZmanTalisTefilinSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """זמן נטילת תפילין ותלית ראשונה עפ״י מג״א (Misheyakir)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:watch"
    _attr_name = "Zman Talis & Tefilin"
    _attr_unique_id = "yidcal_zman_tallis_tefilin"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.entity_id = "sensor.yidcal_zman_tallis_tefilin"
        self.hass = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None
        # grab user-defined offset (minutes after Alos)
        self._offset = cfg.get(
            "tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(
            self.hass, self._midnight_update, hour=0, minute=0, second=0
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        # local now & date
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # compute sunrise
        cal = ZmanimCalendar(geo_location=self._geo, date=today)
        sunrise = cal.sunrise().astimezone(self._tz)

        # 1) Alos HaShachar = sunrise - 72 minutes
        alos_time = sunrise - timedelta(minutes=DEFAULT_ALOS_OFFSET)
        # 2) Zman Talis & Tefilin = Alos + user offset
        target = alos_time + timedelta(minutes=self._offset)

        # expose extra attributes for debugging
        self._attr_extra_state_attributes = {
            #"sunrise": sunrise.isoformat(),
            "alos_with_seconds": alos_time.isoformat(),
            "tallis_with_seconds": target.isoformat(),
            "offset_minutes": self._offset,
        }

        # round seconds: floor if <56, else ceil
        if target.second >= 56:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # set the UTC timestamp value
        self._attr_native_value = target.astimezone(timezone.utc)
