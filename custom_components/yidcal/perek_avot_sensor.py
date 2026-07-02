from __future__ import annotations
from datetime import date, timedelta
from typing import Optional, Tuple, Union
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

import pyluach.dates as pdates
from .device import YidCalDisplayDevice
from .yidcal_lib import halacha_events as he
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
ChapterType = Union[int, Tuple[int, int]]

class PerekAvotSensor(YidCalDisplayDevice, SensorEntity):
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

        cfg = (hass.data.get(DOMAIN) or {}).get("config") or {}
        self._diaspora: bool = bool(cfg.get("diaspora", True))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._update_state()  # immediate

        async def _midnight_cb(now):
            # recompute once right after midnight; Sunday check isn’t strictly necessary,
            # since we also run every minute, but it’s cheap.
            await self._update_state(now)

        unsub_midnight = async_track_time_change(
            self.hass, _midnight_cb, hour=0, minute=0, second=5
        )
        self._register_listener(unsub_midnight)

        async def _minute_cb(now):
            # catch manual clock jumps/time simulation quickly
            await self._update_state(now)

        unsub_minute = async_track_time_change(self.hass, _minute_cb, second=0)
        self._register_listener(unsub_minute)

    # ───────────────── helpers ─────────────────

    def _skip_reason(self, shabbat_date: date) -> Optional[str]:
        """Canonical skip rule (halacha_events) — shared with the luach."""
        return he.avos_skip_reason(shabbat_date, diaspora=self._diaspora)

    @staticmethod
    def _fmt_date(d: date) -> str:
        return d.isoformat()

    # ───────────────── core update ─────────────────

    async def _update_state(self, now=None) -> None:
        """Compute which Pirkei Avos chapter will be read on the upcoming Shabbos."""
        today_py = dt_util.now().date()  # timezone-safe

        # Week runs Sun→Shabbos; find this week's Shabbos
        days_since_sunday = (today_py.weekday() - 6) % 7  # Mon=0 … Sun=6
        week_start = today_py - timedelta(days=days_since_sunday)
        shabbat_of_week = week_start + timedelta(days=6)

        today_hd = pdates.HebrewDate.from_pydate(today_py)

        # Last day of Pesach (21 EY, 22 Chul); first Shabbos *after* that day
        pesach_last_day = 22 if self._diaspora else 21
        pesach_py = pdates.HebrewDate(today_hd.year, 1, pesach_last_day).to_pydate()
        offset = (5 - pesach_py.weekday()) % 7 or 7  # Saturday=5; ensure strictly after
        first_shabbos = pesach_py + timedelta(days=offset)

        # Last Shabbos before Rosh Hashanah (1 Tishrei of the *next* Hebrew year)
        rh_py = pdates.HebrewDate(today_hd.year + 1, 7, 1).to_pydate()
        prev_day = rh_py - timedelta(days=1)
        days_to_sat = (prev_day.weekday() - 5) % 7
        last_shabbos = prev_day - timedelta(days=days_to_sat)

        attrs = {
            "first_shabbos": self._fmt_date(first_shabbos),
            "last_shabbos": self._fmt_date(last_shabbos),
            "shabbos_of_week": self._fmt_date(shabbat_of_week),
            "diaspora": self._diaspora,
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
            attrs["skipped"] = "true"
            attrs["skipped_reason"] = reason
            state = reason
            self._attr_extra_state_attributes = attrs
            self._attr_native_value = state
            self.async_write_ha_state()
            return

        # Canonical computation (halacha_events.pirkei_avos_info) — the
        # exact audited v0.7.8 algorithm, now shared with the luach.
        chapter_label, _reason, valid_week_count, total = he.pirkei_avos_info(
            shabbat_of_week, diaspora=self._diaspora
        )
        if "-" in chapter_label:
            # Reconstruct the (n1, n2) pair for the chapter_number attr
            valid_remaining = 0
            d = shabbat_of_week
            while d <= last_shabbos:
                if not self._skip_reason(d):
                    valid_remaining += 1
                d += timedelta(days=7)
            pairs = [(1, 2), (3, 4), (5, 6)]
            n1, n2 = pairs[(3 - valid_remaining) % 3]
            chapter: ChapterType = (n1, n2)
        else:
            chapter = ((valid_week_count - 1) % 6) + 1

        attrs["reading_index"] = valid_week_count
        attrs["reading_total"] = total
        attrs["chapter_label"] = chapter_label
        attrs["chapter_number"] = list(chapter) if isinstance(chapter, tuple) else chapter

        self._attr_extra_state_attributes = attrs
        self._attr_native_value = chapter_label
        self.async_write_ha_state()
