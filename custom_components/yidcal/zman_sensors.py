# /config/custom_components/yidcal/zman_sensors.py

from __future__ import annotations

import datetime
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalDevice

# ─── Helper: compute holiday duration via pyluach ───────────────────────────

def get_holiday_duration(pydate: datetime.date) -> int:
    """
    Return the number of consecutive Yom Tov days starting at `pydate`,
    using pyluach to detect the festival name.
    """
    hd0 = HDateInfo(pydate, diaspora=True)
    if not hd0.is_yom_tov:
        return 0  # not a festival

    # Base holiday name without prefix day ("פסח א׳" → "פסח")
    base_name = PHebrewDate.from_pydate(pydate).holiday(
        hebrew=True, prefix_day=False
    )
    length = 1

    while True:
        next_date = pydate + timedelta(days=length)
        name2 = PHebrewDate.from_pydate(next_date).holiday(
            hebrew=True, prefix_day=False
        )
        if name2 == base_name:
            length += 1
        else:
            break

    return length

# ─── Geo helpers ──────────────────────────────────────────────────────────────

def _create_geo(config) -> GeoLocation:
    return GeoLocation(
        name="YidCal",
        latitude=config["latitude"],
        longitude=config["longitude"],
        time_zone=config["tzname"],
        elevation=0,
    )

async def get_geo(hass: HomeAssistant) -> GeoLocation:
    config = hass.data[DOMAIN]["config"]
    return await hass.async_add_executor_job(_create_geo, config)

# ─── Zman Erev Sensor ─────────────────────────────────────────────────────────

class ZmanErevSensor(YidCalDevice, RestoreEntity, SensorEntity):
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

        config = hass.data[DOMAIN]["config"]
        self._candle  = config.get("candlelighting_offset", candle_offset)
        self._havdalah = config.get("havdalah_offset",     havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(self.hass, self._midnight_update, hour=0, minute=0, second=0)

    async def _midnight_update(self, now: datetime.datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        z_civil    = {"sunrise": None, "sunset": None}
        cal_today  = ZmanimCalendar(geo_location=self._geo, date=today)
        sunrise    = cal_today.sunrise().astimezone(self._tz)
        sunset     = cal_today.sunset().astimezone(self._tz)
        dawn       = sunrise - timedelta(minutes=72)
        candle_cut = sunset - timedelta(minutes=self._candle)

        # Determine eve date
        if now >= candle_cut:
            check_date = today + timedelta(days=1)
        else:
            check_date = today

        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        if is_yomtov:
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            eve_date = start - timedelta(days=1)
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset + timedelta(minutes=self._havdalah)):
                eve_date = today - timedelta(days=1)
            else:
                days_to_fri = (4 - wd) % 7
                eve_date = today + timedelta(days=days_to_fri)

        cal_eve = ZmanimCalendar(geo_location=self._geo, date=eve_date)
        s_eve   = cal_eve.sunset().astimezone(self._tz)
        target  = s_eve - timedelta(minutes=self._candle)
        full_iso = target.isoformat()
        # half‑up rounding
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        self._attr_native_value = target.astimezone(timezone.utc)

        lt = target.astimezone(self._tz)
        hour, minute = lt.hour % 12 or 12, lt.minute
        ampm = "AM" if lt.hour < 12 else "PM"
        human = f"{hour}:{minute:02d} {ampm}"

        self._attr_extra_state_attributes = {
            "Zman_Erev_With_Seconds": full_iso,
            "Zman_Erev_Simple":       human,
            "City":                   self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude":               self._geo.latitude,
            "Longitude":              self._geo.longitude,
        }

# ─── Zman Motzi Sensor ────────────────────────────────────────────────────────

class ZmanMotziSensor(YidCalDevice, RestoreEntity, SensorEntity):
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
        self._candle  = config.get("candlelighting_offset", candle_offset)
        self._havdalah = config.get("havdalah_offset",    havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(self.hass, self._midnight_update, hour=0, minute=0, second=0)

    async def _midnight_update(self, now: datetime.datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        cal_today    = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset_today = cal_today.sunset().astimezone(self._tz)
        candle_cut   = sunset_today - timedelta(minutes=self._candle)

        # decide which Hebrew day (today or tomorrow) to use
        check_date = today + timedelta(days=1) if now >= candle_cut else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        if is_yomtov:
            # find the first day of the festival span
            start = check_date
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            # compute duration via pyluach fallback
            duration = get_holiday_duration(start)
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                start = today - timedelta(days=1)
            else:
                days_to_fri = (4 - wd) % 7
                start = today + timedelta(days=days_to_fri)
            duration = 1

        # figure out which date’s sunset to use for the final havdalah
        if is_yomtov:
            # festival: the last *civil* festival day is start + (duration - 1)
            end_date = start + timedelta(days=duration - 1)
        else:
            # weekly Motzi (Shabbos): start + duration lands on Saturday
            end_date = start + timedelta(days=duration)

        # sunset on the last day of the festival
        cal_final = ZmanimCalendar(geo_location=self._geo, date=end_date)
        s_final   = cal_final.sunset().astimezone(self._tz)
        target    = s_final + timedelta(minutes=self._havdalah)

        full_iso = target.isoformat()
        # always ceil to next minute
        target = (target + timedelta(minutes=1)).replace(second=0, microsecond=0)

        self._attr_native_value = target.astimezone(timezone.utc)

        lt = target.astimezone(self._tz)
        hour, minute = lt.hour % 12 or 12, lt.minute
        ampm = "AM" if lt.hour < 12 else "PM"
        human = f"{hour}:{minute:02d} {ampm}"

        self._attr_extra_state_attributes = {
            "Zman_Motzi_With_Seconds": full_iso,
            "Zman_Motzi_Simple":       human,
            "City":                    self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude":                self._geo.latitude,
            "Longitude":               self._geo.longitude,
        }
