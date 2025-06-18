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

from astral import LocationInfo
from astral.sun import sun
from hdate import HDateInfo

from .const import DOMAIN
from .device import YidCalDevice


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
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._diaspora = True
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )


    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # initial calculation
        await self.async_update()
        # schedule a midnight check every day
        async_track_time_change(
            self.hass,
            self._midnight_check,
            hour=0, minute=0, second=0,
        )
        
        # poll every minute (so we pick up window_start as soon as it's set)
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
        # 1) now + today
        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        # 2) today’s sunset and candle-lighting threshold
        s_today = sun(self._loc.observer, date=today, tzinfo=self._tz)
        sunset_today = s_today["sunset"]
        candle_time = sunset_today - timedelta(minutes=self._candle)

        # 3) decide which date to check (tomorrow if already past candle-time)
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        # 4) determine festival span
        if is_yomtov:
            # multi-day Yom Tov
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            eve_date = start - timedelta(days=1)
        else:
            # Shabbos Fri→Sat
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah_offset)):
                # still Saturday before havdalah → Friday
                eve_date = today - timedelta(days=1)
            else:
                # upcoming Friday
                days_to_fri = (4 - wd) % 7
                eve_date = today + timedelta(days=days_to_fri)

        # 5) compute candle-lighting datetime
        s_eve = sun(self._loc.observer, date=eve_date, tzinfo=self._tz)["sunset"]
        target = s_eve - timedelta(minutes=self._candle)

        # 6) round half-up at 30s
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # 7) convert to UTC for timestamp device_class
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
        # now store both offsets
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._diaspora = True
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # initial calculation
        await self.async_update()
        async_track_time_change(
            self.hass,
            self._midnight_check,
            hour=0, minute=0, second=0,
        )
        
        # poll every minute as well
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
        # 1) now + today
        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        # 2) today’s sunset
        s_today = sun(self._loc.observer, date=today, tzinfo=self._tz)
        sunset_today = s_today["sunset"]
        candle_time = sunset_today - timedelta(minutes=self._candle)
        # decide check_date (same as erev)
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        # 3) festival span
        if is_yomtov:
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            end_date = start + timedelta(days=hd.holidays[0].duration)  # multi-day length
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                start = today - timedelta(days=1)
            else:
                days_to_fri = (4 - wd) % 7
                start = today + timedelta(days=days_to_fri)
            end_date = start + timedelta(days=1)

        # 4) compute havdalah datetime on final day
        s_final = sun(self._loc.observer, date=end_date, tzinfo=self._tz)["sunset"]
        target = s_final + timedelta(minutes=self._havdalah)

        # 5) round half-up at 30s
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        # 6) convert to UTC
        self._attr_native_value = target.astimezone(timezone.utc)


# no async_setup_entry here—these are registered from sensor.py
