# /homeassistant/custom_components/yidcal/zman_sensors.py
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .device import YidCalDevice


class ZmanErevSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Next candle-lighting (“Zman Erev”) for Shabbos or Yom Tov eve."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:candelabra-fire"
    _attr_name = "Zman Erev"
    _attr_unique_id = "yidcal_zman_erev"
    _attr_entity_id = "sensor.yidcal_zman_erev"

    def __init__(self, hass: HomeAssistant, candle_offset: int):
        super().__init__()
        slug = "zman_erev"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()
        async_track_time_change(
            self.hass,
            self._weekly_update,
            weekday=6, hour=0, minute=0, second=0,
        )

    async def _weekly_update(self, now: datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime | None = None) -> None:
        src = self.hass.states.get("binary_sensor.yidcal_no_melucha")
        win_start = src and src.attributes.get("window_start")
        if not win_start:
            self._attr_native_value = None
            return
        # parse local time
        dt_local = datetime.fromisoformat(win_start).astimezone(dt_util.DEFAULT_TIME_ZONE)
        # round half up: if seconds >= 30, bump minute
        if dt_local.second >= 30:
            dt_local += timedelta(minutes=1)
        # drop seconds and microseconds
        dt_local = dt_local.replace(second=0, microsecond=0)
        # convert to UTC for timestamp
        dt_utc = dt_local.astimezone(timezone.utc)
        self._attr_native_value = dt_utc


class ZmanMotziSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Next havdalah (“Zman Motzi”) for Shabbos or Yom Tov close."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:liquor"
    _attr_name = "Zman Motzi"
    _attr_unique_id = "yidcal_zman_motzi"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int):
        super().__init__()
        slug = "zman_motzi"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._havdalah = havdalah_offset

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()
        async_track_time_change(
            self.hass,
            self._weekly_update,
            weekday=6, hour=0, minute=0, second=0,
        )

    async def _weekly_update(self, now: datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime | None = None) -> None:
        src = self.hass.states.get("binary_sensor.yidcal_no_melucha")
        win_end = src and src.attributes.get("window_end")
        if not win_end:
            self._attr_native_value = None
            return
        # parse local time
        dt_local = datetime.fromisoformat(win_end).astimezone(dt_util.DEFAULT_TIME_ZONE)
        # round half up: if seconds >= 30, bump minute
        if dt_local.second >= 30:
            dt_local += timedelta(minutes=1)
        # drop seconds and microseconds
        dt_local = dt_local.replace(second=0, microsecond=0)
        # convert to UTC for timestamp
        dt_utc = dt_local.astimezone(timezone.utc)
        self._attr_native_value = dt_utc


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    cfg = hass.data[DOMAIN][entry.entry_id]
    candle = cfg["candlelighting_offset"]
    havdalah = cfg["havdalah_offset"]

    entities = [
        ZmanErevSensor(hass, candle),
        ZmanMotziSensor(hass, havdalah),
    ]
    async_add_entities(entities, update_before_add=True)
