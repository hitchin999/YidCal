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
from . import DEFAULT_TALLIS_TEFILIN_OFFSET

# default Alos “MGA” offset (0°50′ = 72 minutes)
DEFAULT_ALOS_OFFSET = 72


class ZmanTalisTefilinSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
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
        # user-defined minutes after Alos
        self._offset = cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(
            self.hass, self._midnight_update, hour=0, minute=0, second=0
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    def _format_human(self, dt_local: datetime) -> str:
        hour = dt_local.hour % 12 or 12
        minute = dt_local.minute
        ampm = "AM" if dt_local.hour < 12 else "PM"
        return f"{hour}:{minute:02d} {ampm}"

    def _compute_for_date(self, base_date: date_cls) -> tuple[datetime, str, str]:
        """
        For base_date:
          Alos = sunrise(base_date) - DEFAULT_ALOS_OFFSET
          Target = Alos + self._offset
        Returns (rounded_local_target, precise_local_alos_iso, precise_local_target_iso).
        """
        assert self._geo is not None
        cal = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunrise = cal.sunrise().astimezone(self._tz)

        alos_time = sunrise - timedelta(minutes=DEFAULT_ALOS_OFFSET)
        target = alos_time + timedelta(minutes=self._offset)

        alos_iso_local = alos_time.isoformat()
        target_iso_local = target.isoformat()

        # rounding: <30s floor, >=30s ceil
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        return target, alos_iso_local, target_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        # local now & date
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # today / yesterday / tomorrow
        local_today_dt, alos_iso_today, target_iso_today = self._compute_for_date(today)
        local_yest_dt, _, _ = self._compute_for_date(today - timedelta(days=1))
        local_tom_dt,  _, _ = self._compute_for_date(today + timedelta(days=1))

        # state = today's value in UTC
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # human strings
        human_today = self._format_human(local_today_dt)
        human_tom   = self._format_human(local_tom_dt)
        human_yest  = self._format_human(local_yest_dt)

        # attributes (Tomorrow before Yesterday) + keep your existing keys
        self._attr_extra_state_attributes = {
            # "sunrise": sunrise.isoformat(),  # if needed later
            "Alos_With_Seconds": alos_iso_today,
            "Tallis_With_Seconds": target_iso_today,
            "Tallis_Simple": human_today,
            "Offset_Minutes": self._offset,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }

