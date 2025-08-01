#/config/custom_components/yidcal/yurtzeit_sensor.py
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
from homeassistant.helpers.event import async_track_sunset
from homeassistant.helpers.restore_state import RestoreEntity
from pyluach.hebrewcal import Year, HebrewDate as PHebrewDate

from .yidcal_lib.helper import int_to_hebrew
from .device import YidCalDevice
from .const import DOMAIN  

_LOGGER = logging.getLogger(__name__)

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
    "אדר א'": 12,
    "אדר א״": 12,
    "אדר ב": 13,
    "אדר ב'": 13,
    "אדר ב״": 13,
}

hebrew_digits = {
    'א': 1, 'ב': 2, 'ג': 3, 'ד': 4, 'ה': 5, 'ו': 6, 'ז': 7, 'ח': 8, 'ט': 9,
    'י': 10, 'כ': 20, 'ל': 30, 'מ': 40, 'נ': 50, 'ס': 60, 'ע': 70, 'פ': 80, 'צ': 90,
}

class YurtzeitSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Today's Yurtzeits, flipping at sunset + user-set havdalah_offset."""

    _attr_name = "Yurtzeit"
    _attr_icon = "mdi:candle"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "yurtzeit"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        
        # Fetch havdalah_offset from config with passed value as fallback
        config = hass.data[DOMAIN]["config"]
        self._havdalah = config.get("havdalah_offset", havdalah_offset)
        self._havdalah_offset = timedelta(minutes=self._havdalah)
        
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )
        self._state: str | None = None
        self._attributes: dict = {}
        self._yurtzeits: dict[tuple[int, int], list[dict]] = {}  # (month, day): list of {'text': str}
        self._custom_yurtzeits: dict[tuple[int, int], list[dict]] = {}
        self._muted_yurtzeits: set[str] = set()

    async def async_added_to_hass(self) -> None:
        start_time = time.time()
        await super().async_added_to_hass()
        
        # Restore previous state
        last = await self.async_get_last_state()
        if last:
            self._state = last.state
            self._attributes = last.attributes or {}
            
        # Fetch Yurtzeits once on startup
        await self._fetch_yurtzeits()
        
        # Load custom and muted files
        await self._load_custom_and_muted()
        
        # Immediate calculation
        await self._update_state()
        
        # Schedule sunset + offset update
        self._register_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )
        
        # Minute-by-minute updates for precise flip
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )
        
        total_time = time.time() - start_time

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def _fetch_yurtzeits(self) -> None:
        """Fetch Yurtzeits from GitHub-hosted JSON."""
        start_time = time.time()
        cache_file = self.hass.config.path('yidcal-data', 'yurtzeit_cache.json')
        refetch = True
        
        if os.path.exists(cache_file):
            mtime = dt.datetime.fromtimestamp(os.stat(cache_file).st_mtime)
            if (dt.datetime.now() - mtime) < dt.timedelta(days=30):
                try:
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
                except json.JSONDecodeError:
                    pass
                    
        if refetch:
            github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache.json"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(github_url, timeout=10) as response:
                        if response.status != 200:
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
                    cache_dir = self.hass.config.path('yidcal-data')
                    if not os.path.exists(cache_dir):
                        os.makedirs(cache_dir, mode=0o755)
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                        
                await self.hass.async_add_executor_job(save_cache)
            except Exception as e:
                pass
                
        total_time = time.time() - start_time

    async def _load_custom_and_muted(self) -> None:
        def load_files():
            folder = self.hass.config.path('yidcal-data')
            custom_path = os.path.join(folder, 'custom_yahrtzeits.txt')
            muted_path = os.path.join(folder, 'muted_yahrtzeits.txt')
            
            def parse_custom(file_path):
                data = {}
                if not os.path.exists(file_path):
                    return data
                with open(file_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if ':' not in line:
                            continue
                        date_str, name = line.split(':', 1)
                        date_str = date_str.strip()
                        name = name.strip()
                        parsed = self._parse_hebrew_date(date_str)
                        if parsed is None:
                            continue
                        m, d = parsed
                        if (m, d) not in data:
                            data[(m, d)] = []
                        data[(m, d)].append({'text': name})
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
        date_str = date_str.replace('״', '"').replace('’', "'").replace('׳', "'").replace('״', '"')
        parts = date_str.split()
        if len(parts) < 2:
            return None
        day_str = parts[0].strip('"\'')
        month_str = ' '.join(parts[1:]).strip()
        
        # Parse day
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
        return self._state or STATE_UNKNOWN

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
        lookup_month = 12 if heb.month in [12, 13] else heb.month
        key = (lookup_month, heb.day)
        
        github_entries = self._yurtzeits.get(key, [])
        custom_entries = self._custom_yurtzeits.get(key, [])
        fetched_entries = github_entries + custom_entries
        
        todays = [e['text'] for e in fetched_entries if e['text'] not in self._muted_yurtzeits]
        
        if todays:
            heb_day = int_to_hebrew(heb.day)
            if is_leap and heb.month == 12:
                month_name = "אדר א'"
            elif is_leap and heb.month == 13:
                month_name = "אדר ב'"
            else:
                month_name = next((k for k, v in month_map.items() if v == heb.month), '')
            self._state = f"יארצייטן {heb_day} {month_name}"
        else:
            self._state = "No Yurtzeit"
        
        attrs = {}
        for i, entry in enumerate(todays, start=1):
            attrs[f'יארצייט {i}'] = entry
        self._attributes = attrs
        
        self.async_write_ha_state()
