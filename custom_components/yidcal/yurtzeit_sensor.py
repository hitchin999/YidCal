from __future__ import annotations

import logging
import time
import json
import os
import datetime as dt
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp
from astral import LocationInfo
from astral.sun import sun

from homeassistant.const import STATE_UNKNOWN
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from pyluach.hebrewcal import Year, HebrewDate as PHebrewDate

from .yidcal_lib.helper import int_to_hebrew
from .device import YidCalDisplayDevice
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# ----------------------
# Static Hebrew mappings
# ----------------------

month_map = {
    "ניסן": 1,
    "אייר": 2,
    "סיון": 3,
    "תמוז": 4,
    "אב": 5,
    "מנחם אב": 5,
    "מנ\"א": 5,
    "אלול": 6,
    "תשרי": 7,
    "חשון": 8,
    "מרחשון": 8,
    "כסלו": 9,
    "טבת": 10,
    "שבט": 11,
    "אדר": 12,
    "אדר א": 12,
    "אדר א'": 12,   # ASCII apostrophe (legacy)
    "אדר א׳": 12,   # Hebrew geresh (preferred)
    "אדר א״": 12,   # tolerate odd input
    "אדר ב": 13,
    "אדר ב'": 13,   # ASCII apostrophe (legacy)
    "אדר ב׳": 13,   # Hebrew geresh (preferred)
    "אדר ב״": 13,
}

hebrew_digits = {
    'א': 1, 'ב': 2, 'ג': 3, 'ד': 4, 'ה': 5, 'ו': 6, 'ז': 7, 'ח': 8, 'ט': 9,
    'י': 10, 'כ': 20, 'ל': 30, 'מ': 40, 'נ': 50, 'ס': 60, 'ע': 70, 'פ': 80, 'צ': 90,
}

day_labels = [
    "ליום א'",
    "ליום ב'",
    "ליום ג'",
    "ליום ד'",
    "ליום ה'",
    "ליום ו'",
    "לשבת קודש"
]

# Months that can be 29 or 30 days (Cheshvan + Kislev)
FLEX_29_30_MONTHS = {8, 9}

# Suffix to show when a 30th is being displayed on the 29th of a short month
MOVED_SUFFIX_BY_MONTH = {
    8: " (ל' חשון)",  # 30 Cheshvan shown on 29th in short Cheshvan years
    9: " (ל' כסלו)",   # 30 Kislev shown on 29th in short Kislev years
}

# ============================================================
# Daily Yurtzeit
# ============================================================

class YurtzeitSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Today's Yurtzeits, flipping at sunset + user-set havdalah_offset."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        havdalah_offset: int,
        database: str,             # "standard" | "satmar"
        legacy_ids: bool = False,  # Preserve historical entity IDs when only "standard" is selected
    ) -> None:
        super().__init__()
        self.hass = hass
        self._database = database
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )

        # Use global config if present; fallback to ctor value
        config = hass.data.get(DOMAIN, {}).get("config", {})
        self._havdalah = int(config.get("havdalah_offset", havdalah_offset))
        self._havdalah_offset = timedelta(minutes=self._havdalah)

        # Names / IDs (legacy for single standard DB)
        name_suffix = "" if legacy_ids else (" — Satmar" if database == "satmar" else " — Standard")
        self._attr_name = "Yurtzeit" + ("" if legacy_ids else name_suffix)
        slug = "yurtzeit" if legacy_ids else f"yurtzeit_{database}"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        # State
        self._state: str | None = None
        self._attributes: dict = {}

        # Data stores
        self._yurtzeits: dict[tuple[int, int], list[dict]] = {}      # (month, day) -> [{'text': str}, ...]
        self._custom_yurtzeits: dict[tuple[int, int], list[dict]] = {}
        self._muted_yurtzeits: set[str] = set()

    # ----- HA lifecycle -----

    async def async_added_to_hass(self) -> None:
        start_time = time.time()
        await super().async_added_to_hass()

        # Restore previous state/attributes
        last = await self.async_get_last_state()
        if last:
            self._state = last.state
            self._attributes = last.attributes or {}

        # Load data
        await self._fetch_yurtzeits()
        await self._load_custom_and_muted()

        # Initial compute
        await self._update_state()

        # Schedule sunset + offset update
        self._register_sunset(self.hass, self._schedule_update, offset=self._havdalah_offset)

        # Minute-by-minute updates for precise flip
        self._register_interval(self.hass, self._schedule_update, timedelta(minutes=1))

        _LOGGER.debug("YurtzeitSensor[%s] init in %.2fs", self._database, time.time() - start_time)

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    # ----- Data fetch & helpers -----

    async def _fetch_yurtzeits(self) -> None:
        """Fetch Yahrtzeits (file/URL keep legacy spelling)."""
        start_time = time.time()
        database = self._database
        cache_file = self.hass.config.path('www/yidcal-data', f'yahrtzeit_cache_{database}.json')
        refetch = True

        if os.path.exists(cache_file):
            try:
                mtime = dt.datetime.fromtimestamp(os.stat(cache_file).st_mtime)
                if (dt.datetime.now() - mtime) < dt.timedelta(days=30):
                    def load_cache():
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        valid_data = {}
                        for k, v in data.items():
                            try:
                                month_day = tuple(map(int, k.split('_')))
                                valid_data[month_day] = v
                            except (ValueError, TypeError):
                                pass
                        return valid_data
                    self._yurtzeits = await self.hass.async_add_executor_job(load_cache)
                    refetch = False
            except Exception as e:
                _LOGGER.warning("Failed reading Yahrtzeit cache %s: %s", cache_file, e)

        if refetch:
            if database == "standard":
                github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache.json"
            else:
                github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache_satmar.json"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(github_url, timeout=15) as response:
                        if response.status != 200:
                            _LOGGER.warning("Yahrtzeit fetch failed (%s): HTTP %s", database, response.status)
                            return
                        text = await response.text()
                        data = json.loads(text)

                valid_data = {}
                for k, v in data.items():
                    try:
                        month_day = tuple(map(int, k.split('_')))
                        valid_data[month_day] = v
                    except (ValueError, TypeError):
                        pass

                self._yurtzeits = valid_data

                def save_cache():
                    cache_dir = self.hass.config.path('www/yidcal-data')
                    if not os.path.exists(cache_dir):
                        os.makedirs(cache_dir, mode=0o755)
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)

                await self.hass.async_add_executor_job(save_cache)
            except Exception as e:
                _LOGGER.warning("Yahrtzeit download failed (%s): %s", database, e)

        _LOGGER.debug("Loaded Yahrtzeit %s in %.2fs", database, time.time() - start_time)

    async def _load_custom_and_muted(self) -> None:
        """Load custom/muted lists from text files (legacy filenames)."""
        def load_files():
            folder = self.hass.config.path('www/yidcal-data')
            custom_path = os.path.join(folder, 'custom_yahrtzeits.txt')
            muted_path = os.path.join(folder, 'muted_yahrtzeits.txt')

            def parse_custom(file_path):
                data = {}
                if not os.path.exists(file_path):
                    return data
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or ':' not in line:
                            continue
                        date_str, name = line.split(':', 1)
                        date_str = date_str.strip()
                        name = name.strip()
                        parsed = self._parse_hebrew_date(date_str)
                        if parsed is None:
                            continue
                        m, d = parsed
                        data.setdefault((m, d), []).append({'text': name})
                return data

            def parse_muted(file_path):
                data = set()
                if not os.path.exists(file_path):
                    return data
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        data.add(line)
                return data

            custom_data = parse_custom(custom_path)
            muted_data = parse_muted(muted_path)
            return custom_data, muted_data

        custom, muted = await self.hass.async_add_executor_job(load_files)
        self._custom_yurtzeits = custom
        self._muted_yurtzeits = muted

    def _parse_hebrew_date(self, date_str: str) -> tuple[int, int] | None:
        date_str = (
            date_str.replace('״', '"')
                    .replace('’', "'")
                    .replace('׳', "'")
                    .replace('״', '"')
        )
        parts = date_str.split()
        if len(parts) < 2:
            return None
        day_str = parts[0].strip('"\'')
        month_str = ' '.join(parts[1:]).strip()

        # Parse day from Hebrew letters
        day = 0
        for char in day_str:
            day += hebrew_digits.get(char, 0)
        if day < 1 or day > 30:
            return None

        # Parse month
        month = month_map.get(month_str)
        if month is None:
            return None

        return month, day

    # ----- HA state -----

    @property
    def state(self) -> str:
        return self._state if self._state is not None else STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict:
        return self._attributes

    async def _update_state(self) -> None:
        """Recompute Yurtzeits based on current Hebrew date."""
        now = datetime.now(self._tz)
        s = sun(self._loc.observer, date=now.date(), tzinfo=self._tz)
        switch_time = s["sunset"] + self._havdalah_offset
        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()

        heb = PHebrewDate.from_pydate(py_date)
        is_leap = Year(heb.year).leap

        # Normalize Adar / Adar II to a single bucket (your existing logic)
        lookup_month = 12 if heb.month in [12, 13] else heb.month
        key = (lookup_month, heb.day)

        # Base entries for the actual Hebrew date
        github_entries = self._yurtzeits.get(key, [])
        custom_entries = self._custom_yurtzeits.get(key, [])

        # --- NEW: 29-day Cheshvan fallback with (ל' חשוון) tag ---
        extra_entries: list[dict] = []
        if heb.month in FLEX_29_30_MONTHS and heb.day == 29:
            # If adding 1 day jumps to the next month, this month has only 29 days
            has_day_30 = heb.add(days=1).month == heb.month
            if not has_day_30:
                fallback_key = (lookup_month, 30)
                base_fallback_entries = (
                    self._yurtzeits.get(fallback_key, []) +
                    self._custom_yurtzeits.get(fallback_key, [])
                )
                suffix = MOVED_SUFFIX_BY_MONTH.get(heb.month, "")
                for e in base_fallback_entries:
                    text = e.get("text", "")
                    # Avoid double-tagging if someone already typed it manually
                    if suffix and suffix not in text:
                        text = f"{text}{suffix}"
                    extra_entries.append({"text": text})

        fetched_entries = github_entries + custom_entries + extra_entries

        todays = [
            e["text"] for e in fetched_entries
            if e["text"] not in self._muted_yurtzeits
        ]

        if todays:
            heb_day = int_to_hebrew(heb.day)
            if is_leap and heb.month == 12:
                month_name = "אדר א׳"
            elif is_leap and heb.month == 13:
                month_name = "אדר ב׳"
            else:
                month_name = next((k for k, v in month_map.items() if v == heb.month), '')
            self._state = f"יארצייטן {heb_day} {month_name}"
        else:
            self._state = ""

        attrs: dict[str, str] = {}
        for i, entry in enumerate(todays, start=1):
            attrs[f"יארצייט {i}"] = entry
        self._attributes = attrs

        self.async_write_ha_state()

# ============================================================
# Weekly Yurtzeit
# ============================================================

class YurtzeitWeeklySensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Weekly Yurtzeits, flipping at Saturday sunset + havdalah_offset."""

    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        havdalah_offset: int,
        database: str,
        legacy_ids: bool = False,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._database = database
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )

        # Use global config if present; fallback to ctor value
        config = hass.data.get(DOMAIN, {}).get("config", {})
        self._havdalah = int(config.get("havdalah_offset", havdalah_offset))
        self._havdalah_offset = timedelta(minutes=self._havdalah)

        # Names / IDs (legacy for single standard DB)
        name_suffix = "" if legacy_ids else (" — Satmar" if database == "satmar" else " — Standard")
        self._attr_name = "Yurtzeits Weekly" + ("" if legacy_ids else name_suffix)
        slug = "yurtzeits_weekly" if legacy_ids else f"yurtzeits_weekly_{database}"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self._state: str | None = None
        self._attributes: dict = {}

        self._yurtzeits: dict[tuple[int, int], list[dict]] = {}
        self._custom_yurtzeits: dict[tuple[int, int], list[dict]] = {}
        self._muted_yurtzeits: set[str] = set()

    async def async_added_to_hass(self) -> None:
        start_time = time.time()
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._state = last.state
            self._attributes = last.attributes or {}

        await self._fetch_yurtzeits()
        await self._load_custom_and_muted()

        await self._update_state()

        self._register_sunset(self.hass, self._schedule_update, offset=self._havdalah_offset)
        self._register_interval(self.hass, self._schedule_update, timedelta(minutes=1))

        _LOGGER.debug("YurtzeitWeeklySensor[%s] init in %.2fs", self._database, time.time() - start_time)

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def _fetch_yurtzeits(self) -> None:
        """Fetch Yahrtzeits (file/URL keep legacy spelling)."""
        start_time = time.time()
        database = self._database
        cache_file = self.hass.config.path('www/yidcal-data', f'yahrtzeit_cache_{database}.json')
        refetch = True

        if os.path.exists(cache_file):
            try:
                mtime = dt.datetime.fromtimestamp(os.stat(cache_file).st_mtime)
                if (dt.datetime.now() - mtime) < dt.timedelta(days=30):
                    def load_cache():
                        with open(cache_file, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        valid_data = {}
                        for k, v in data.items():
                            try:
                                month_day = tuple(map(int, k.split('_')))
                                valid_data[month_day] = v
                            except (ValueError, TypeError):
                                pass
                        return valid_data
                    self._yurtzeits = await self.hass.async_add_executor_job(load_cache)
                    refetch = False
            except Exception as e:
                _LOGGER.warning("Failed reading Yahrtzeit cache %s: %s", cache_file, e)

        if refetch:
            if database == "standard":
                github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache.json"
            else:
                github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache_satmar.json"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(github_url, timeout=15) as response:
                        if response.status != 200:
                            _LOGGER.warning("Yahrtzeit fetch failed (%s): HTTP %s", database, response.status)
                            return
                        text = await response.text()
                        data = json.loads(text)

                valid_data = {}
                for k, v in data.items():
                    try:
                        month_day = tuple(map(int, k.split('_')))
                        valid_data[month_day] = v
                    except (ValueError, TypeError):
                        pass

                self._yurtzeits = valid_data

                def save_cache():
                    cache_dir = self.hass.config.path('www/yidcal-data')
                    if not os.path.exists(cache_dir):
                        os.makedirs(cache_dir, mode=0o755)
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)

                await self.hass.async_add_executor_job(save_cache)
            except Exception as e:
                _LOGGER.warning("Yahrtzeit download failed (%s): %s", database, e)

        _LOGGER.debug("Loaded Yahrtzeit %s (weekly) in %.2fs", database, time.time() - start_time)

    async def _load_custom_and_muted(self) -> None:
        """Load custom/muted lists from text files (legacy filenames)."""
        def load_files():
            folder = self.hass.config.path('www/yidcal-data')
            custom_path = os.path.join(folder, 'custom_yahrtzeits.txt')
            muted_path = os.path.join(folder, 'muted_yahrtzeits.txt')

            def parse_custom(file_path):
                data = {}
                if not os.path.exists(file_path):
                    return data
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#') or ':' not in line:
                            continue
                        date_str, name = line.split(':', 1)
                        date_str = date_str.strip()
                        name = name.strip()
                        parsed = self._parse_hebrew_date(date_str)
                        if parsed is None:
                            continue
                        m, d = parsed
                        data.setdefault((m, d), []).append({'text': name})
                return data

            def parse_muted(file_path):
                data = set()
                if not os.path.exists(file_path):
                    return data
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        data.add(line)
                return data

            custom_data = parse_custom(custom_path)
            muted_data = parse_muted(muted_path)
            return custom_data, muted_data

        custom, muted = await self.hass.async_add_executor_job(load_files)
        self._custom_yurtzeits = custom
        self._muted_yurtzeits = muted

    def _parse_hebrew_date(self, date_str: str) -> tuple[int, int] | None:
        date_str = (
            date_str.replace('״', '"')
                    .replace('’', "'")
                    .replace('׳', "'")
                    .replace('״', '"')
        )
        parts = date_str.split()
        if len(parts) < 2:
            return None
        day_str = parts[0].strip('"\'')
        month_str = ' '.join(parts[1:]).strip()

        # Parse day from Hebrew letters
        day = 0
        for char in day_str:
            day += hebrew_digits.get(char, 0)
        if day < 1 or day > 30:
            return None

        # Parse month
        month = month_map.get(month_str)
        if month is None:
            return None

        return month, day

    @property
    def state(self) -> str:
        return self._state if self._state is not None else STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict:
        return self._attributes

    async def _update_state(self) -> None:
        """Recompute weekly Yurtzeits based on current Hebrew date."""
        now = datetime.now(self._tz)
        s = sun(self._loc.observer, date=now.date(), tzinfo=self._tz)
        switch_time = s["sunset"] + self._havdalah_offset
        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()

        # Calculate the start of the current week (Sunday)
        weekday = py_date.weekday()  # 0 = Monday … 6 = Sunday
        days_to_prev_sunday = (weekday + 1) % 7
        week_start_greg = py_date - timedelta(days=days_to_prev_sunday)
        week_end_greg = week_start_greg + timedelta(days=6)

        # Compute Hebrew date for start/end for title
        heb_start = PHebrewDate.from_pydate(week_start_greg)
        heb_end = PHebrewDate.from_pydate(week_end_greg)

        def _month_name(hd: PHebrewDate) -> str:
            is_leap = Year(hd.year).leap
            if is_leap and hd.month == 12:
                return "אדר א׳"
            if is_leap and hd.month == 13:
                return "אדר ב׳"
            return next((k for k, v in month_map.items() if v == hd.month), '')

        heb_day_start = int_to_hebrew(heb_start.day)
        heb_day_end = int_to_hebrew(heb_end.day)
        month_name_start = _month_name(heb_start)
        month_name_end = _month_name(heb_end)

        attrs = {}
        has_any = False

        for day_i in range(7):
            day_greg = week_start_greg + timedelta(days=day_i)
            heb = PHebrewDate.from_pydate(day_greg)
            is_leap = Year(heb.year).leap
            lookup_month = 12 if heb.month in [12, 13] else heb.month
            key = (lookup_month, heb.day)

            # Base entries for this Hebrew date
            github_entries = self._yurtzeits.get(key, [])
            custom_entries = self._custom_yurtzeits.get(key, [])

            # --- NEW: 29-day Cheshvan fallback with (ל' חשוון) tag for the weekly view ---
            extra_entries: list[dict] = []
            if heb.month in FLEX_29_30_MONTHS and heb.day == 29:
                has_day_30 = heb.add(days=1).month == heb.month
                if not has_day_30:
                    fallback_key = (lookup_month, 30)
                    base_fallback_entries = (
                        self._yurtzeits.get(fallback_key, []) +
                        self._custom_yurtzeits.get(fallback_key, [])
                    )
                    suffix = MOVED_SUFFIX_BY_MONTH.get(heb.month, "")
                    for e in base_fallback_entries:
                        text = e.get("text", "")
                        if suffix and suffix not in text:
                            text = f"{text}{suffix}"
                        extra_entries.append({"text": text})

            fetched_entries = github_entries + custom_entries + extra_entries

            entries = [
                e["text"] for e in fetched_entries
                if e["text"] not in self._muted_yurtzeits
            ]

            if entries:
                has_any = True
                if is_leap and heb.month == 12:
                    month_name = "אדר א׳"
                elif is_leap and heb.month == 13:
                    month_name = "אדר ב׳"
                else:
                    month_name = next((k for k, v in month_map.items() if v == heb.month), '')

                header = f"יארצייטן {day_labels[day_i]} - {int_to_hebrew(heb.day)} {month_name}"
                attrs[header] = ""  # Header key with empty value

                for i, entry in enumerate(entries, start=1):
                    attrs[f'{day_labels[day_i]} יארצייט {i}'] = entry

        if has_any:
            if heb_start.month == heb_end.month:
                self._state = f"יארצייטן לשבוע {heb_day_start} - {heb_day_end} {month_name_start}"
            else:
                self._state = f"יארצייטן לשבוע {heb_day_start} {month_name_start} - {heb_day_end} {month_name_end}"
        else:
            self._state = ""

        self._attributes = attrs
        self.async_write_ha_state()
