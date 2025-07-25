# /config/custom_components/yidcal/tehilim_daily_sensor.py

from __future__ import annotations
from datetime import date, timedelta
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change

from .device import YidCalDevice
from .yidcal_lib.helper import int_to_hebrew
from pyluach.hebrewcal import HebrewDate as PHebrewDate

REFERENCE_DATE  = date(2025, 6, 25)
REFERENCE_INDEX = 16
CHAPTER_COUNT   = 150
BLOCK_SIZE      = 5
CYCLE_LENGTH    = (CHAPTER_COUNT + BLOCK_SIZE - 1) // BLOCK_SIZE  # = 30


class TehilimDailySensor(YidCalDevice, SensorEntity):
    """Daily-rotating Tehilim block, skipping Shabbos/Yomtov/Hoshana Rabah."""

    _attr_icon               = "mdi:book-open-variant"
    _attr_name               = "Tehilim Daily"
    _attr_unique_id          = "yidcal_tehilim_daily"
    _attr_extra_state_attributes: dict[str, Any] = {}

    def __init__(self, hass: HomeAssistant, device) -> None:
        super().__init__(hass, device)
        slug = "tehilim_daily"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        self._state = None
        self.update()

        async_track_time_change(
            hass, self._handle_midnight, hour=0, minute=0, second=1
        )

    async def _handle_midnight(self, now):
        self.async_schedule_update_ha_state(True)

    @property
    def state(self) -> str | None:
        return self._state

    def update(self):
        """Recompute today's block and export every block as a boolean attr."""

        today = dt_util.now().date()

        # — skip today if Shabbos/Yomtov
        if self.hass.states.is_state("binary_sensor.yidcal_no_melucha", "on"):
            return

        # — skip if today is Hoshana Rabah
        hol = self.hass.states.get("sensor.yidcal_holiday")
        if hol and hol.attributes.get("hoshana_raba", False):
            return

        # 1) count valid days since reference (skip historical Shabbos & H.R.)
        delta_days = (today - REFERENCE_DATE).days
        valid_days = 0
        for d in range(delta_days + 1):
            check = REFERENCE_DATE + timedelta(days=d)

            # skip past Shabbos
            if check.weekday() == 5:
                continue
            # skip past Hoshana Rabah (21 Tishrei)
            hd = PHebrewDate.from_pydate(check)
            if hd.month == 7 and hd.day == 21:
                continue
            # skip past Yom Tov
            if hd.festival(include_working_days=False) is not None:
                continue

            valid_days += 1

        # 2) figure out which 5-chapter block is “today”
        idx      = (REFERENCE_INDEX + valid_days - 1) % CYCLE_LENGTH
        start_ch = idx * BLOCK_SIZE + 1
        end_ch   = min(start_ch + BLOCK_SIZE - 1, CHAPTER_COUNT)

        # helper to strip any geresh/gershayim
        def clean(s: str) -> str:
            return s.replace("׳", "").replace("״", "")

        # our state string, e.g. "א - ה"
        today_label = f"{clean(int_to_hebrew(start_ch))} - {clean(int_to_hebrew(end_ch))}"
        self._state = today_label

        # 3) build every possible block label, and mark it true only if it matches today
        attrs: dict[str, bool] = {}
        for i in range(CYCLE_LENGTH):
            s = i * BLOCK_SIZE + 1
            e = min(s + BLOCK_SIZE - 1, CHAPTER_COUNT)
            lbl = f"{clean(int_to_hebrew(s))} - {clean(int_to_hebrew(e))}"
            attrs[lbl] = (lbl == today_label)

        # 4) set _attr_extra_state_attributes to our new boolean map
        self._attr_extra_state_attributes = attrs
        
