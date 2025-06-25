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


class ChatzosHayomSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """חצות היום (6 שעות זמניות)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:clock-time-twelve-outline"
    _attr_name          = "Chatzos Hayom"
    _attr_unique_id     = "yidcal_chatzos_hayom"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "chatzos_hayom"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz       = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # load geo once
        self._geo = await get_geo(self.hass)
        # initial calc
        await self.async_update()
        # recalc each midnight local
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

        # 1) current date in local tz
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        # 2) build calendar at 0°50′ zenith
        cal      = ZmanimCalendar(geo_location=self._geo, date=today)
        sunrise  = cal.sunrise().astimezone(self._tz)
        sunset   = cal.sunset().astimezone(self._tz)

        # 3) MGA “day” from dawn to nightfall
        dawn      = sunrise - timedelta(minutes=72)
        nightfall = sunset  + timedelta(minutes=72)

        # 4) length of one sha’ah zmanit
        hour_td   = (nightfall - dawn) / 12

        # 5) chatzos = dawn + 6 temporal hours
        target    = dawn + hour_td * 6

        # 6) optional debug attrs
        self._attr_extra_state_attributes = {
            #"dawn":       dawn.isoformat(),
            #"sunrise":    sunrise.isoformat(),
            #"sunset":     sunset.isoformat(),
            #"nightfall":  nightfall.isoformat(),
            #"hour_len":   str(hour_td),
            "chatzos_hayom_with_Seconds": target.isoformat(),
        }

        # 7) custom rounding: under 58 s → floor, 58 s or above → ceil
        if target.second >= 58:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # 8) set native UTC value
        self._attr_native_value = target.astimezone(timezone.utc)
