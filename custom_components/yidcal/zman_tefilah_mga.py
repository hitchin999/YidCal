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


class SofZmanTefilahMGASensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
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

    def _format_human(self, dt_local: datetime) -> str:
        hour = dt_local.hour % 12 or 12
        minute = dt_local.minute
        ampm = "AM" if dt_local.hour < 12 else "PM"
        return f"{hour}:{minute:02d} {ampm}"

    def _compute_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """
        MGA day (dawn→nightfall); SZT = dawn + 4 sha'ot zmaniot.
        Returns (rounded_local_dt, precise_local_iso_before_flooring).
        """
        assert self._geo is not None
        cal      = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunrise  = cal.sunrise().astimezone(self._tz)
        sunset   = cal.sunset().astimezone(self._tz)

        dawn      = sunrise - timedelta(minutes=72)
        nightfall = sunset  + timedelta(minutes=72)

        hour_td = (nightfall - dawn) / 12
        target  = dawn + hour_td * 4

        # keep precise (local) ISO pre-floor
        full_iso_local = target.isoformat()

        # floor to the minute
        target = target.replace(second=0, microsecond=0)

        return target, full_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today     = now_local.date()

        # today / yesterday / tomorrow
        local_today_dt, full_iso_today = self._compute_for_date(today)
        local_yest_dt, _               = self._compute_for_date(today - timedelta(days=1))
        local_tom_dt, _                = self._compute_for_date(today + timedelta(days=1))

        # state = today's time in UTC
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # human strings
        human_today = self._format_human(local_today_dt)
        human_tom   = self._format_human(local_tom_dt)
        human_yest  = self._format_human(local_yest_dt)

        # attributes (Tomorrow before Yesterday), keep existing keys
        self._attr_extra_state_attributes = {
            "Tefila_MGA_With_Seconds": full_iso_today,
            "Tefila_MGA_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }

