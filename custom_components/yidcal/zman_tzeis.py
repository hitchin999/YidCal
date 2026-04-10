from __future__ import annotations
from datetime import datetime, timedelta, timezone, date as date_cls
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo


class ZmanTziesSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """Tzies Hakochavim (using Havdalah offset after sunset)."""

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:star"
    _attr_name          = "Tzies Hakochavim"
    _attr_unique_id     = "yidcal_tzies_hakochavim"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "tzies_hakochavim"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._havdalah = cfg.get("havdalah_offset", havdalah_offset)
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None
        self._unsub_alos = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # recompute each day at Alos HaShachar (instead of civil midnight)
        self._schedule_next_alos()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_alos is not None:
            self._unsub_alos()
            self._unsub_alos = None

    def _compute_alos_local(self, base_date: date_cls) -> datetime:
        """Alos HaShachar (MGA: sunrise - 72m) for base_date, in local tz."""
        assert self._geo is not None
        cal = ZmanimCalendar(geo_location=self._geo, date=base_date)
        return (cal.sunrise() - timedelta(minutes=72)).astimezone(self._tz)

    def _schedule_next_alos(self) -> None:
        """Schedule the next async_update to fire at the next Alos HaShachar."""
        if self._unsub_alos is not None:
            self._unsub_alos()
            self._unsub_alos = None
        if not self._geo:
            return
        now_local = dt_util.now().astimezone(self._tz)
        today = now_local.date()
        next_alos = self._compute_alos_local(today)
        if next_alos <= now_local:
            next_alos = self._compute_alos_local(today + timedelta(days=1))
        self._unsub_alos = async_track_point_in_time(
            self.hass, self._alos_update, next_alos
        )

    async def _alos_update(self, now: datetime) -> None:
        await self.async_update()
        self._schedule_next_alos()

    def _compute_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """
        Tzies for base_date = sunset(base_date) + havdalah_offset.
        Returns (rounded_local_dt, precise_local_iso_before_rounding).
        """
        assert self._geo is not None
        cal    = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunset = cal.sunset().astimezone(self._tz)

        target = sunset + timedelta(minutes=self._havdalah)
        full_iso_local = target.isoformat()  # precise (before rounding)

        # always ceil to the next minute
        target = (target + timedelta(minutes=1)).replace(second=0, microsecond=0)

        return target, full_iso_local

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        # local date
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today_civil = now_local.date()

        # Treat the day as not yet rolled over until Alos HaShachar.
        # This keeps last night's value stable for nighttime automations
        # that run after civil midnight (e.g. summer 6h-after-tzeis automations).
        today_alos = self._compute_alos_local(today_civil)
        if now_local < today_alos:
            today = today_civil - timedelta(days=1)
        else:
            today = today_civil

        # compute today / yesterday / tomorrow
        local_today_dt, full_iso_today = self._compute_for_date(today)
        local_yest_dt, _               = self._compute_for_date(today - timedelta(days=1))
        local_tom_dt, _                = self._compute_for_date(today + timedelta(days=1))

        # state in UTC
        self._attr_native_value = local_today_dt.astimezone(timezone.utc)

        # human strings
        human_today = self._format_simple_time(local_today_dt)
        human_tom   = self._format_simple_time(local_tom_dt)
        human_yest  = self._format_simple_time(local_yest_dt)

        # attributes (Tomorrow before Yesterday)
        self._attr_extra_state_attributes = {
            "Tzies_With_Seconds": full_iso_today,
            "Tzies_Simple": human_today,
            "Tomorrows_Simple": human_tom,
            "Yesterdays_Simple": human_yest,
        }
