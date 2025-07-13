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


class ChatzosHaLailaSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """חצות הלילה עפ\"י המג\"א (6 שעות זמניות מתחילת הלילה)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:weather-night"
    _attr_name          = "Chatzos HaLaila"
    _attr_unique_id     = "yidcal_chatzos_haleila"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "chatzos_haleila"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # load geo once
        self._geo = await get_geo(self.hass)
        # initial calc
        await self.async_update()
        # recompute each midnight local (so it always re-fires at 00:00)
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

        # 1) local “today” date
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        # 2) calculate tonight’s start and tomorrow’s dawn
        cal_today    = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset       = cal_today.sunset().astimezone(self._tz)
        night_start  = sunset + timedelta(minutes=72)

        # dawn of the next day
        cal_tomorrow = ZmanimCalendar(geo_location=self._geo, date=today + timedelta(days=1))
        sunrise_next = cal_tomorrow.sunrise().astimezone(self._tz)
        dawn_next    = sunrise_next - timedelta(minutes=72)

        # 3) one temporal hour = (dawn_next – night_start) / 12
        hour_td      = (dawn_next - night_start) / 12

        # 4) Chatzos Ha-Laila = night_start + 6 * hour_td
        target       = night_start + hour_td * 6

        # save full‐precision ISO timestamp
        full_iso = target.isoformat()

        # 6) custom rounding: <30 s floor, ≥30 s ceil
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # 7) set native UTC timestamp
        self._attr_native_value = target.astimezone(timezone.utc)
        
        # now build the human string in your configured tz
        local_target = target.astimezone(self._tz)
        # cross‐platform AM/PM formatting without %-I
        hour = local_target.hour % 12 or 12
        minute = local_target.minute
        ampm = "AM" if local_target.hour < 12 else "PM"
        human = f"{hour}:{minute:02d} {ampm}"
        
        # 5) expose debug attrs
        self._attr_extra_state_attributes = {
            #"night_start": night_start.isoformat(),
            #"dawn_next":   dawn_next.isoformat(),
            #"hour_len":    str(hour_td),
            "chatzos_haleila_with_seconds":  full_iso,
            "chatzos_haleila_simple":  human,
            
        }
