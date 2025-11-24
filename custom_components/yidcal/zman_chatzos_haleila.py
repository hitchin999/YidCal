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


class ChatzosHaLailaSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
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

    def _compute_chatzos_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """Compute Chatzos HaLaila for the 'night' that begins at base_date's sunset.

        Returns (rounded_local_dt, precise_iso_string_in_local_tz).
        """
        assert self._geo is not None

        # Start of halachic night for base_date
        cal_today = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunset = cal_today.sunset().astimezone(self._tz)
        night_start = sunset + timedelta(minutes=72)

        # Dawn (alot) of the next morning (base_date + 1)
        cal_next = ZmanimCalendar(geo_location=self._geo, date=base_date + timedelta(days=1))
        sunrise_next = cal_next.sunrise().astimezone(self._tz)
        dawn_next = sunrise_next - timedelta(minutes=72)

        # Temporal hour and midpoint
        hour_td = (dawn_next - night_start) / 12
        target = night_start + hour_td * 6

        # full precision (local tz) for debug
        full_iso_local = target.isoformat()

        # rounding rule: <30s floor, >=30s ceil; then zero out seconds/us
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # Return local rounded time and precise (local) ISO
        return target, full_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # Today / Yesterday / Tomorrow computations (based on local date)
        local_today_dt, full_iso_today = self._compute_chatzos_for_date(today)
        local_yest_dt, _               = self._compute_chatzos_for_date(today - timedelta(days=1))
        local_tom_dt, _                = self._compute_chatzos_for_date(today + timedelta(days=1))

        # Native UTC state = today's chatzos (rounded) in UTC
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # Human strings for attributes
        human_today = self._format_simple_time(local_today_dt)
        human_yest  = self._format_simple_time(local_yest_dt)
        human_tom   = self._format_simple_time(local_tom_dt)

        # Attributes
        self._attr_extra_state_attributes = {
            "Chatzos_Haleila_With_Seconds": full_iso_today,
            "Chatzos_Haleila_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }

