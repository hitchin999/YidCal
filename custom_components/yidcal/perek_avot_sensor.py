from __future__ import annotations
from datetime import date, timedelta
from .device import YidCalDevice

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

import pyluach.dates as pdates
from .yidcal_lib.helper import int_to_hebrew
import logging

_LOGGER = logging.getLogger(__name__)

class PerekAvotSensor(YidCalDevice, SensorEntity):
    """Which פרק of Pirkei Avot is read each week (from Pesach until Rosh Hashanah)."""

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

        # 2) DAILY at 00:00:05 → only update on Sunday
        async def _midnight_cb(now):
            if now.weekday() == 6:  # Sunday
                await self._update_state(now)

        unsub_midnight = async_track_time_change(
            self.hass, _midnight_cb, hour=0, minute=0, second=5
        )
        self._register_listener(unsub_midnight)

        # 3) DEBUG: every minute so you can observe flips in a simulator
        async def _minute_cb(now):
            await self._update_state(now)

        unsub_minute = async_track_time_change(
            self.hass, _minute_cb, second=0
        )
        self._register_listener(unsub_minute)

    def _should_skip_shabbat(self, shabbat_date: date, today_hd: pdates.HebrewDate) -> bool:
        """Check if Pirkei Avot should be skipped on this Shabbat."""
        
        shabbat_hd = pdates.HebrewDate.from_pydate(shabbat_date)
        
        # 1. Skip if Shavuot falls on Shabbat (6 Sivan)
        if shabbat_hd.month == 3 and shabbat_hd.day == 6:  # 6 Sivan
            return True
        
        # 2. Skip Shabbat Chazon (Shabbat on or immediately before Tisha B'Av)
        # Tisha B'Av is 9 Av (month 5, day 9)
        if shabbat_hd.month == 5:  # Av
            # If this Shabbat is 9 Av (Tisha B'Av on Shabbat)
            if shabbat_hd.day == 9:
                return True
            # If this Shabbat is between 3-8 Av (the Shabbat before Tisha B'Av)
            # (Tisha B'Av can't fall on Sunday, so if 9 Av is not Shabbat and 
            # we're between 3-8, this must be the Shabbat before)
            if 3 <= shabbat_hd.day <= 8:
                # Check if 9 Av falls after this Shabbat but before next Shabbat
                tisha_bav = pdates.HebrewDate(shabbat_hd.year, 5, 9)
                tisha_bav_py = tisha_bav.to_pydate()
                # If Tisha B'Av is between Sunday and Friday after this Shabbat
                if shabbat_date < tisha_bav_py <= shabbat_date + timedelta(days=6):
                    return True
        
        return False

    async def _update_state(self, now=None) -> None:
        """Compute which Pirkei Avot chapter will be read on the upcoming Shabbat."""
        today_py = date.today()

        # Anchor to the most recent Sunday (Mon=0 … Sun=6)
        days_since_sunday = (today_py.weekday() - 6) % 7
        week_start = today_py - timedelta(days=days_since_sunday)

        today_hd = pdates.HebrewDate.from_pydate(today_py)

        # 1) Pesach – last day (22 ניסן for diaspora) of this Hebrew year
        pesach_hd = pdates.HebrewDate(today_hd.year, 1, 22)
        pesach_py = pesach_hd.to_pydate()

        # 2) First Shabbat after Pesach
        offset = (5 - pesach_py.weekday()) % 7 or 7
        first_shabbat = pesach_py + timedelta(days=offset)

        # 3) Last Shabbat *before* Rosh Hashanah (1 תשרי) of next Hebrew year
        rh_hd = pdates.HebrewDate(today_hd.year + 1, 7, 1)
        rh_py = rh_hd.to_pydate()
        prev_day = rh_py - timedelta(days=1)
        days_to_sat = (prev_day.weekday() - 5) % 7    # Saturday = weekday 5
        last_shabbat = prev_day - timedelta(days=days_to_sat)

        # 4) Upcoming Shabbat = Sunday + 6 days
        shabbat_of_week = week_start + timedelta(days=6)

        if first_shabbat <= shabbat_of_week <= last_shabbat:
            # Check if this Shabbat should be skipped
            if self._should_skip_shabbat(shabbat_of_week, today_hd):
                state = "נישט אין די צייט פון פרקי אבות"
            else:
                # Count valid reading weeks (excluding skipped ones)
                valid_week_count = 0
                current_date = first_shabbat
                
                while current_date <= shabbat_of_week:
                    if not self._should_skip_shabbat(current_date, today_hd):
                        valid_week_count += 1
                    current_date += timedelta(days=7)
                
                # The current week's count is valid_week_count
                # (we already incremented for the current week if it's valid)
                
                # Count valid weeks from current to end
                valid_remaining = 0
                check_date = shabbat_of_week
                while check_date <= last_shabbat:
                    if not self._should_skip_shabbat(check_date, today_hd):
                        valid_remaining += 1
                    check_date += timedelta(days=7)
                
                # Check if we're in the final three valid reading weeks
                if valid_remaining <= 3:
                    # Final three Shabbats: show pairs 1‑2, 3‑4, 5‑6
                    pairs = [(1, 2), (3, 4), (5, 6)]
                    n1, n2 = pairs[3 - valid_remaining]
                    state = f"פרק {int_to_hebrew(n1)}‑{int_to_hebrew(n2)}"
                else:
                    # Normal single‑chapter cycle based on valid week count
                    chap = ((valid_week_count - 1) % 6) + 1
                    state = f"פרק {int_to_hebrew(chap)}"
        else:
            state = "נישט אין די צייט פון פרקי אבות"

        self._attr_native_value = state
        self.async_write_ha_state()
