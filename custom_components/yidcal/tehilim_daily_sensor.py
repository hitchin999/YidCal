# /config/custom_components/yidcal/tehilim_daily_sensor.py

from __future__ import annotations
from datetime import date, timedelta

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change

from .device import YidCalDevice
from .yidcal_lib.helper import int_to_hebrew
from pyluach.hebrewcal import HebrewDate as PHebrewDate

REFERENCE_DATE = date(2025, 6, 25)
REFERENCE_INDEX = 17
CHAPTER_COUNT = 150
BLOCK_SIZE = 5
CYCLE_LENGTH = (CHAPTER_COUNT + BLOCK_SIZE - 1) // BLOCK_SIZE  # = 30


class TehilimDailySensor(YidCalDevice, SensorEntity):
    """Daily‐rotating Tehilim block, skipping Shabbos/Yomtov/Hoshana Rabah."""

    _attr_icon       = "mdi:book-open-variant"
    _attr_name       = "Tehilim Daily"
    _attr_unique_id  = "yidcal_tehilim_daily"
    _state: str | None = None

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(hass, device)
        slug = "tehilim_daily"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        # midnight rollover
        async_track_time_change(
            hass, self._handle_midnight, hour=0, minute=0, second=1
        )

    async def _handle_midnight(self, now):
        self.async_schedule_update_ha_state(True)

    @property
    def state(self) -> str | None:
        return self._state

    def update(self):
        today = dt_util.now().date()

        # — skip if *today* is Shabbos/Yomtov
        if self.hass.states.is_state("binary_sensor.yidcal_no_melucha", "on"):
            return

        # — skip if *today* is Hoshana Rabah
        hol = self.hass.states.get("sensor.yidcal_holiday")
        if hol and hol.attributes.get("hoshana_raba", False):
            return

        # count valid days since REFERENCE_DATE, skipping historical Shabbos & Hosh. Rabah
        delta_days = (today - REFERENCE_DATE).days
        valid_days = 0

        for d in range(delta_days + 1):
            check = REFERENCE_DATE + timedelta(days=d)

            # historical Shabbos
            if check.weekday() == 5:
                continue

            # historical Hoshana Rabah = 21 Tishrei
            hd = PHebrewDate.from_pydate(check)
            if hd.month == 7 and hd.day == 21:
                continue

            valid_days += 1

        # compute which 5-chapter block to show
        idx      = (REFERENCE_INDEX + valid_days - 1) % CYCLE_LENGTH
        start_ch = idx * BLOCK_SIZE + 1
        end_ch   = min(start_ch + BLOCK_SIZE - 1, CHAPTER_COUNT)

        # build strings
        start_str = int_to_hebrew(start_ch)
        end_str   = int_to_hebrew(end_ch).rstrip("׳״")

        # e.g. "פ״ו-צ"
        self._state = f"{start_str} - {end_str}"
