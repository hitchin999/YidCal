from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class NoMeluchaShabbosSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    ON every week from Friday sunset - candle_offset
    until Saturday sunset + havdalah_offset (ignores Yom Tov completely).
    """
    _attr_name = "No Melucha – Shabbos"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_shabbos"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_no_melucha_shabbos"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return
        now = dt_util.now().astimezone(self._tz)
        today = now.date()

        def week_window(base_date):
            wd = base_date.weekday()  # Mon=0..Sat=5..Sun=6
            friday = base_date - timedelta(days=(wd - 4) % 7)
            saturday = friday + timedelta(days=1)
            s = ZmanimCalendar(geo_location=self._geo, date=friday).sunset().astimezone(self._tz) - timedelta(minutes=self._candle)
            e = ZmanimCalendar(geo_location=self._geo, date=saturday).sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)
            return s, e

        start_dt, end_dt = week_window(today)
        if now >= end_dt:
            start_dt, end_dt = week_window(today + timedelta(days=7))

        window_start = _round_half_up(start_dt)
        window_end = _round_ceil(end_dt)
        self._attr_is_on = window_start <= now < window_end
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
        }
