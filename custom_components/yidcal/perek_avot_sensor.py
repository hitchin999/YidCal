from __future__ import annotations
from datetime import date, timedelta
from .device import YidCalDevice

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

import pyluach.dates as pdates
from .yidcal_lib.helper import int_to_hebrew


class PerekAvotSensor(YidCalDevice, SensorEntity):
    """Which פרק of Pirkei Avot is read each week (from Pesach until Sukkot)."""

    _attr_name = "Perek Avos"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "perek_avot"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._attr_native_value = "נישט אין די צייט פון פרקי אבות"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # 1) Immediate population
        await self._update_state()

        # 2) DAILY at 00:00:05 → guard inside callback so it only updates on Sunday
        async def _midnight_cb(now):
            # Python: Monday=0 … Sunday=6
            if now.weekday() == 6:
                await self._update_state(now)

        unsub_midnight = async_track_time_change(
            self.hass,
            _midnight_cb,
            hour=0, minute=0, second=5,
        )
        self._register_listener(unsub_midnight)

        # 3) DEBUG: every minute at HH:MM:00 so you can observe the flip in your simulator
        async def _minute_cb(now):
            await self._update_state(now)

        unsub_minute = async_track_time_change(
            self.hass,
            _minute_cb,
            second=0,
        )
        self._register_listener(unsub_minute)

    async def _update_state(self, now=None) -> None:
        """Compute which Pirkei Avot chapter should be the sensor state today."""
        today_py = date.today()

        # Anchor to the most recent Sunday (Mon=0 … Sun=6)
        days_since_sunday = (today_py.weekday() - 6) % 7
        week_start = today_py - timedelta(days=days_since_sunday)

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

        # 4) If this week’s Sunday is in the Pesach–Sukkot window, compute chapter
        if first_shabbat <= week_start <= sukkot_py:
            weeks_since = ((week_start - first_shabbat).days // 7) + 1
            chap = ((weeks_since - 1) % 6) + 1
            state = f"פרק {int_to_hebrew(chap)}"
        else:
            state = "נישט אין די צייט פון פרקי אבות"

        self._attr_native_value = state
        self.async_write_ha_state()
