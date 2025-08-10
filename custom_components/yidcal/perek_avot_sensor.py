from __future__ import annotations
from datetime import date, timedelta
from typing import Optional, Tuple, Union

from .device import YidCalDevice
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

import pyluach.dates as pdates
from .yidcal_lib.helper import int_to_hebrew
import logging

_LOGGER = logging.getLogger(__name__)

ChapterType = Union[int, Tuple[int, int]]

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
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._update_state()  # immediate

        async def _midnight_cb(now):
            if now.weekday() == 6:  # Sunday
                await self._update_state(now)

        unsub_midnight = async_track_time_change(
            self.hass, _midnight_cb, hour=0, minute=0, second=5
        )
        self._register_listener(unsub_midnight)

        async def _minute_cb(now):
            await self._update_state(now)

        unsub_minute = async_track_time_change(
            self.hass, _minute_cb, second=0
        )
        self._register_listener(unsub_minute)

    # ───────────────── helpers ─────────────────

    def _skip_reason(self, shabbat_date: date) -> Optional[str]:
        """Return the skip reason string if Avos is skipped this Shabbos, else None."""
        sh_hd = pdates.HebrewDate.from_pydate(shabbat_date)

        # Shavuos on Shabbos → skip (6 or 7 Sivan in chutz la'aretz)
        if sh_hd.month == 3 and sh_hd.day in (6, 7):
            # Format day as Hebrew numeral (e.g., ו׳ / ז׳)
            day_lbl = int_to_hebrew(sh_hd.day)
            return f"הדלגה — שבועות ({day_lbl} סיון)"

        # Shabbos Chazon (Shabbos on/just before 9 Av) → skip
        if sh_hd.month == 5:  # Av
            if sh_hd.day == 9:
                return "הדלגה — שבת חזון"
            if 3 <= sh_hd.day <= 8:
                # Is 9 Av during the following week?
                tisha_bav_py = pdates.HebrewDate(sh_hd.year, 5, 9).to_pydate()
                if shabbat_date < tisha_bav_py <= shabbat_date + timedelta(days=6):
                    return "הדלגה — שבת חזון"

        return None

    @staticmethod
    def _fmt_date(d: date) -> str:
        return d.isoformat()

    # ───────────────── core update ─────────────────

    async def _update_state(self, now=None) -> None:
        """Compute which Pirkei Avos chapter will be read on the upcoming Shabbos."""
        today_py = date.today()

        # Most recent Sunday (Mon=0 … Sun=6)
        days_since_sunday = (today_py.weekday() - 6) % 7
        week_start = today_py - timedelta(days=days_since_sunday)
        shabbat_of_week = week_start + timedelta(days=6)

        today_hd = pdates.HebrewDate.from_pydate(today_py)

        # Last day of Pesach (chutz la'aretz): 22 Nisan -> first Shabbos after
        pesach_py = pdates.HebrewDate(today_hd.year, 1, 22).to_pydate()
        offset = (5 - pesach_py.weekday()) % 7 or 7  # Saturday=5; ensure after
        first_shabbos = pesach_py + timedelta(days=offset)

        # Last Shabbos before Rosh Hashanah
        rh_py = pdates.HebrewDate(today_hd.year + 1, 7, 1).to_pydate()
        prev_day = rh_py - timedelta(days=1)
        days_to_sat = (prev_day.weekday() - 5) % 7
        last_shabbos = prev_day - timedelta(days=days_to_sat)

        attrs = {
            "first_shabbos": self._fmt_date(first_shabbos),
            "last_shabbos": self._fmt_date(last_shabbos),
            "shabbos_of_week": self._fmt_date(shabbat_of_week),
            "skipped": False,
            "skipped_reason": None,
            "reading_index": None,
            "reading_total": None,
            "chapter_label": None,
            "chapter_number": None,  # int or [n1, n2]
        }

        if not (first_shabbos <= shabbat_of_week <= last_shabbos):
            state = "נישט אין די צייט פון פרקי אבות"
            self._attr_extra_state_attributes = attrs
            self._attr_native_value = state
            self.async_write_ha_state()
            return

        # If this Shabbos is a skip, surface the reason and stop.
        reason = self._skip_reason(shabbat_of_week)
        if reason:
            attrs["skipped"] = True
            attrs["skipped_reason"] = reason
            state = reason  # user-facing friendly message
            self._attr_extra_state_attributes = attrs
            self._attr_native_value = state
            self.async_write_ha_state()
            return

        # Count valid reading weeks up to & including this Shabbos
        valid_week_count = 0
        d = first_shabbos
        while d <= shabbat_of_week:
            if not self._skip_reason(d):
                valid_week_count += 1
            d += timedelta(days=7)

        # Count valid reading weeks remaining including this Shabbos
        valid_remaining = 0
        d = shabbat_of_week
        while d <= last_shabbos:
            if not self._skip_reason(d):
                valid_remaining += 1
            d += timedelta(days=7)

        # Final three valid Shabbosim → pairs 1-2, 3-4, 5-6
        if valid_remaining <= 3 and valid_remaining > 0:
            pairs = [(1, 2), (3, 4), (5, 6)]
            n1, n2 = pairs[3 - valid_remaining]
            chapter: ChapterType = (n1, n2)
            chapter_label = f"פרק {int_to_hebrew(n1)}-{int_to_hebrew(n2)}"
        else:
            n = ((valid_week_count - 1) % 6) + 1
            chapter = n
            chapter_label = f"פרק {int_to_hebrew(n)}"

        attrs["reading_index"] = valid_week_count
        # Compute total reading weeks for the season (for reference)
        total = 0
        d = first_shabbos
        while d <= last_shabbos:
            if not self._skip_reason(d):
                total += 1
            d += timedelta(days=7)
        attrs["reading_total"] = total
        attrs["chapter_label"] = chapter_label
        attrs["chapter_number"] = list(chapter) if isinstance(chapter, tuple) else chapter

        self._attr_extra_state_attributes = attrs
        self._attr_native_value = chapter_label
        self.async_write_ha_state()
