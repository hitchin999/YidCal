from __future__ import annotations
from datetime import date, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_time_change,
)

import pyluach.dates as pdates
from .yidcal_lib.helper import int_to_hebrew


class PerekAvotSensor(SensorEntity):
    """Which פרק of Pirkei Avot is read each week (from Pesach until Sukkot)."""

    _attr_name = "Perek Avos"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "perek_avot"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._attr_native_value = "נישט אין די צייט פון פרקי אבות"

    async def async_added_to_hass(self) -> None:
        # 1) Do an immediate population
        await self._update_state()

        # 2) Define a coroutine listener for midnight updates
        async def _midnight_update(now):
            await self._update_state()

        # 3) Define a coroutine listener for periodic backups
        async def _periodic_update(now):
            await self._update_state()

        # 4) Schedule the midnight update at 00:00:05 every day
        async_track_time_change(
            self.hass,
            _midnight_update,
            hour=0,
            minute=0,
            second=5,
        )

        # 5) Schedule the periodic update every hour
        async_track_time_interval(
            self.hass,
            _periodic_update,
            timedelta(hours=1),
        )

    async def _update_state(self) -> None:
        """Compute which Pirkei Avot chapter should be the sensor state today."""
        today_py = date.today()
        today_hd = pdates.HebrewDate.from_pydate(today_py)

        # 1) Pesach – 15 ניסן of this Hebrew year
        pesach_hd = pdates.HebrewDate(today_hd.year, 1, 15)
        pesach_py = pesach_hd.to_pydate()

        # 2) First Shabbos after Pesach
        offset = (5 - pesach_py.weekday()) % 7 or 7
        first_shabbat = pesach_py + timedelta(days=offset)

        # 3) Sukkos – 15 תשרי of next Hebrew year
        sukkot_hd = pdates.HebrewDate(today_hd.year + 1, 7, 15)
        sukkot_py = sukkot_hd.to_pydate()

        # 4) If today is between those two, cycle chapters 1–6
        if first_shabbat <= today_py <= sukkot_py:
            weeks_since = ((today_py - first_shabbat).days // 7) + 1
            chap = ((weeks_since - 1) % 6) + 1
            state = f"פרק {int_to_hebrew(chap)}"
        else:
            state = "נישט אין די צייט פון פרקי אבות"

        self._attr_native_value = state
        # Write the updated state back to HA
        self.async_write_ha_state()
