"""
custom_components/yidcal/upcoming_yomtov_zmanim_sensor.py

Sensor that exposes the zmanim for the UPCOMING Yom Tov, plus a
human-friendly state label.

State labels (diaspora / Israel):
  • Pesach first days:     'לימים ראשונים של פסח' / 'ליום ראשון של פסח'
  • Last days of Pesach:   'לשביעי ואחרון של פסח' / 'לשביעי של פסח'
  • Shavuos:               'לשבועות'
  • Rosh Hashana:          'לראש השנה'
  • Yom Kippur:            'ליום כיפור'
  • Sukkos first days:     'לסוכות'
  • Shmini Atzeres/S"T:    'לשמיני עצרת ושמחת תורה'

Rollover: 12:00 AM the civil day AFTER the final Yom Tov day of the
current block. So e.g. Pesach Day 2 (diaspora) ends at Tzeis on Nisan
16; the sensor continues to show 'לימים ראשונים של פסח' through that
night, and flips to 'לשביעי ואחרון של פסח' at 12:00 AM of Nisan 17.

Attributes: for each day in the block, an empty-value header
(יו״ט א׳ / יו״ט ב׳) followed by that day's zmanim in chronological
order. Israel single-day blocks produce Day 1 only.
"""
from __future__ import annotations

import datetime
from datetime import timedelta, date as date_cls
from zoneinfo import ZoneInfo

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo
from .zman_compute import (
    compute_zmanim_for_date,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)


# Block identifiers keyed by the (month, day) of the first YT day.
# For each block we define the diaspora/Israel length and the Hebrew label.
# Note: pyluach month numbering — Nisan=1, Tishrei=7.
_BLOCK_DEFS = {
    # (month, day):        (diaspora_days, israel_days, diaspora_label, israel_label)
    (1, 15):  (2, 1, "לימים ראשונים של פסח", "ליום ראשון של פסח"),
    (1, 21):  (2, 1, "לשביעי ואחרון של פסח",  "לשביעי של פסח"),
    (3, 6):   (2, 1, "לשבועות",               "לשבועות"),
    (7, 1):   (2, 2, "לראש השנה",             "לראש השנה"),
    (7, 10):  (1, 1, "ליום כיפור",            "ליום כיפור"),
    (7, 15):  (2, 1, "לסוכות",                "לסוכות"),
    (7, 22):  (2, 1, "לשמיני עצרת ושמחת תורה", "לשמיני עצרת ושמחת תורה"),
}


class UpcomingYomTovZmanimSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """Zmanim for the upcoming Yom Tov."""

    _attr_name = "Upcoming Yom Tov Zmanim"
    _attr_icon = "mdi:calendar-star-outline"
    _attr_unique_id = "yidcal_upcoming_yomtov_zmanim"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "upcoming_yomtov_zmanim"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._havdalah = int(cfg.get("havdalah_offset", havdalah_offset))
        self._tallis = int(cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET))
        self._diaspora = bool(cfg.get("diaspora", True))
        self._geo: GeoLocation | None = None

        self._state: str = ""
        self._attributes: dict[str, str] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        return self._attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._state = last.state or ""
            self._attributes = dict(last.attributes or {})

        self._geo = await get_geo(self.hass)
        await self._recompute()

        # Roll at 12:00 AM (handles Motzi Yom Tov → next-day rollover)
        unsub = async_track_time_change(
            self.hass, self._midnight_tick, hour=0, minute=0, second=0
        )
        self._register_listener(unsub)

        # Minute safety tick
        self._register_interval(self.hass, self._minute_tick, timedelta(minutes=1))

    async def _midnight_tick(self, now: datetime.datetime) -> None:
        await self._recompute()

    async def _minute_tick(self, now: datetime.datetime) -> None:
        await self._recompute()

    # ── Block resolution ────────────────────────────────────────────────

    def _find_current_block(
        self, today: date_cls
    ) -> tuple[date_cls, int, str] | None:
        """Return (block_start_date, n_days, state_label) for the block
        whose *release* date is the soonest one strictly after `today`.

        The release date = last YT day + 1 civil day. So the block stays
        "current" for display from any time before it starts through the
        night of its final day, and flips at 12:00 AM the next civil day.

        Scans Hebrew years [current - 1 .. current + 1] to cover any
        calendar position.
        """
        ph_today = PHebrewDate.from_pydate(today)
        years_to_scan = (ph_today.year - 1, ph_today.year, ph_today.year + 1)

        n_idx = 0 if self._diaspora else 1
        label_idx = 2 if self._diaspora else 3

        candidates: list[tuple[date_cls, date_cls, int, str]] = []
        # (release_date, block_start, n_days, label)

        for y in years_to_scan:
            for (m, d), block_def in _BLOCK_DEFS.items():
                n_days = block_def[n_idx]
                label = block_def[label_idx]
                try:
                    start = PHebrewDate(y, m, d).to_pydate()
                except Exception:
                    continue
                last = start + timedelta(days=n_days - 1)
                release = last + timedelta(days=1)
                if release > today:
                    candidates.append((release, start, n_days, label))

        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])  # soonest release first
        _release, start, n_days, label = candidates[0]
        return (start, n_days, label)

    # ── Main recompute ──────────────────────────────────────────────────

    async def _recompute(self) -> None:
        if not self._geo:
            return

        now_local = dt_util.now().astimezone(self._tz)
        today = now_local.date()

        block = self._find_current_block(today)
        if block is None:
            self._state = ""
            self._attributes = {}
            self.async_write_ha_state()
            return

        start_date, n_days, label = block
        fmt = self._get_time_format()

        day_headers = ["יו״ט א׳", "יו״ט ב׳"]  # unambiguous — not weekday names

        attrs: dict[str, str] = {
            "Block_Start_Date": f"{start_date.strftime('%a')}, {start_date.isoformat()}",
            "Block_Days": str(n_days),
        }

        for i in range(n_days):
            day_date = start_date + timedelta(days=i)
            # Header (empty-value attribute, per yurtzeits_weekly pattern)
            attrs[day_headers[i]] = ""

            items = compute_zmanim_for_date(
                geo=self._geo,
                tz=self._tz,
                base_date=day_date,
                tallis_offset=self._tallis,
                havdalah_offset=self._havdalah,
            )
            # Prefix each zman with the day header so attribute keys stay
            # unique even when a zman recurs on Day 2.
            prefix = day_headers[i]
            for entry in items:
                key = f"{prefix} - {entry.label}"
                attrs[key] = self._format_simple_time(entry.dt_local, fmt)

        self._state = label
        self._attributes = attrs
        self.async_write_ha_state()
