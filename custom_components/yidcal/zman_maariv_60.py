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
from .device import YidCalDevice
from .zman_sensors import get_geo


class ZmanMaariv60Sensor(YidCalDevice, RestoreEntity, SensorEntity):
    """זמן ערבית (60 דקות אחרי שקיעה)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:clock-alert"
    _attr_name          = "Zman Maariv 60"
    _attr_unique_id     = "yidcal_zman_maariv_60"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "zman_maariv_60"
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
            self.hass, self._midnight_update, hour=0, minute=0, second=0
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    def _format_human(self, dt_local: datetime) -> str:
        hour = dt_local.hour % 12 or 12
        minute = dt_local.minute
        ampm = "AM" if dt_local.hour < 12 else "PM"
        return f"{hour}:{minute:02d} {ampm}"

    def _compute_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """Maariv 60 for base_date = sunset(base_date) + 60m; ceil to minute."""
        assert self._geo is not None
        cal    = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunset = cal.sunset().astimezone(self._tz)

        target = sunset + timedelta(minutes=60)

        # precise local ISO before rounding
        full_iso_local = target.isoformat()

        # ceil to minute regardless of current seconds
        target = (target + timedelta(minutes=1)).replace(second=0, microsecond=0)

        return target, full_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        # local date
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        # compute today / yesterday / tomorrow
        local_today_dt, full_iso_today = self._compute_for_date(today)
        local_yest_dt, _               = self._compute_for_date(today - timedelta(days=1))
        local_tom_dt, _                = self._compute_for_date(today + timedelta(days=1))

        # state = today's value in UTC
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # human strings
        human_today = self._format_human(local_today_dt)
        human_tom   = self._format_human(local_tom_dt)
        human_yest  = self._format_human(local_yest_dt)

        # attributes (Tomorrow before Yesterday)
        self._attr_extra_state_attributes = {
            "Maariv_60_With_Seconds": full_iso_today,
            "Maariv_60_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
