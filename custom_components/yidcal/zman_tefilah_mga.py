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


class SofZmanTefilahMGASensor(YidCalDevice, RestoreEntity, SensorEntity):
    """סוף-זמן תפילה עפ\"י המג\"א (4 שעות זמניות)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:book-multiple"
    _attr_name          = "Sof Zman Tefilah (MGA)"
    _attr_unique_id     = "yidcal_sof_zman_tefilah_mga"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "sof_zman_tefilah_mga"
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
        sunset   = cal.sunset().astimezone(self._tz)

        # MGA “day” from dawn to nightfall
        dawn      = sunrise - timedelta(minutes=72)
        nightfall = sunset  + timedelta(minutes=72)

        # length of one sha’ah zmanit
        hour_td   = (nightfall - dawn) / 12

        # Sof Zman Tefilah = dawn + 4 * hour_td
        target    = dawn + hour_td * 4

        # expose for debugging
        self._attr_extra_state_attributes = {
            #"dawn":        dawn.isoformat(),
            #"sunrise":     sunrise.isoformat(),
            #"sunset":      sunset.isoformat(),
            #"nightfall":   nightfall.isoformat(),
            #"hour_len":    str(hour_td),
            "tefila_mga_with_seconds":  target.isoformat(),
        }

        # floor to the minute (any seconds 0–59)
        target = target.replace(second=0, microsecond=0)

        self._attr_native_value = target.astimezone(timezone.utc)
