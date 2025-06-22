# /config/custom_components/yidcal/zman_sensors.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

# Original hdate import
from hdate import HDateInfo

# New Zmanim imports
from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalDevice


def _create_geo(config) -> GeoLocation:
    """Helper to build GeoLocation from stored config."""
    return GeoLocation(
        name="YidCal",
        latitude=config["latitude"],
        longitude=config["longitude"],
        time_zone=config["tzname"],
        elevation=0,
    )

async def get_geo(hass: HomeAssistant) -> GeoLocation:
    """Fetch GeoLocation via executor to avoid blocking."""
    config = hass.data[DOMAIN]["config"]
    return await hass.async_add_executor_job(_create_geo, config)


class ZmanErevSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Next candle-lighting (“Zman Erev”) for Shabbos or Yom Tov eve."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:candelabra-fire"
    _attr_name = "Zman Erev"
    _attr_unique_id = "yidcal_zman_erev"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "zman_erev"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        # offsets and diaspora flag read from global config
        config = hass.data[DOMAIN]["config"]
        self._candle = config.get("candle", candle_offset)
        self._havdalah = config.get("havdala", havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # load geo info once
        self._geo = await get_geo(self.hass)
        # initial calculation
        await self.async_update()
        # schedule midnight weekly check
        async_track_time_change(
            self.hass,
            self._midnight_check,
            hour=0, minute=0, second=0,
        )
        # minute polling for new window
        async_track_time_interval(
            self.hass,
            self._minutely_update,
            timedelta(minutes=1),
        )

    async def _midnight_check(self, now: datetime) -> None:
        # only run weekly on Sunday (weekday=6)
        if now.weekday() == 6:
            await self.async_update()

    async def _minutely_update(self, now: datetime) -> None:
        await self.async_update(now)

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        # 1) current time in local tz
        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        # 2) compute sunset via ZmanimCalendar
        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset_today = cal_today.sunset().astimezone(self._tz)
        candle_time = sunset_today - timedelta(minutes=self._candle)

        # 3) choose check_date
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        # 4) festival span or Shabbos logic
        if is_yomtov:
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            eve_date = start - timedelta(days=1)
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                eve_date = today - timedelta(days=1)
            else:
                days_to_fri = (4 - wd) % 7
                eve_date = today + timedelta(days=days_to_fri)

        # 5) compute target candle-lighting time
        cal_eve = ZmanimCalendar(geo_location=self._geo, date=eve_date)
        s_eve = cal_eve.sunset().astimezone(self._tz)
        target = s_eve - timedelta(minutes=self._candle)

        # 6) extra attributes
        self._attr_extra_state_attributes = {
            "local_target_time": target.strftime("%-I:%M:%S %p"),
            "city": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "latitude": self._geo.latitude,
            "longitude": self._geo.longitude,
        }

        # 7) round half-up at 30s
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # 8) set native value in UTC
        self._attr_native_value = target.astimezone(timezone.utc)


class ZmanMotziSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Next havdalah (“Zman Motzi”) for Shabbos or Yom Tov close."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:liquor"
    _attr_name = "Zman Motzi"
    _attr_unique_id = "yidcal_zman_motzi"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "zman_motzi"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        config = hass.data[DOMAIN]["config"]
        self._candle = config.get("candle", candle_offset)
        self._havdalah = config.get("havdala", havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(
            self.hass,
            self._midnight_check,
            hour=0, minute=0, second=0,
        )
        async_track_time_interval(
            self.hass,
            self._minutely_update,
            timedelta(minutes=1),
        )

    async def _midnight_check(self, now: datetime) -> None:
        if now.weekday() == 6:
            await self.async_update()

    async def _minutely_update(self, now: datetime) -> None:
        await self.async_update(now)

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset_today = cal_today.sunset().astimezone(self._tz)
        candle_time = sunset_today - timedelta(minutes=self._candle)
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        if is_yomtov:
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            end_date = start + timedelta(days=hd.holidays[0].duration)
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                start = today - timedelta(days=1)
            else:
                days_to_fri = (4 - wd) % 7
                start = today + timedelta(days=days_to_fri)
            end_date = start + timedelta(days=1)

        cal_final = ZmanimCalendar(geo_location=self._geo, date=end_date)
        s_final = cal_final.sunset().astimezone(self._tz)
        target = s_final + timedelta(minutes=self._havdalah)

        self._attr_extra_state_attributes = {
            "local_target_time": target.strftime("%-I:%M:%S %p"),
            "city": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "latitude": self._geo.latitude,
            "longitude": self._geo.longitude,
        }

        if target.second >= 5:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)
        self._attr_native_value = target.astimezone(timezone.utc)

# no async_setup_entry here—these sensors are registered from sensor.py
