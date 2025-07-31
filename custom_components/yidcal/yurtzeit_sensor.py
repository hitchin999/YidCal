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
    "אדר א'": 12,
    "אדר ב'": 13,
}

class YurtzeitSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Today's Yurtzeits, flipping at sunset + user-set havdalah_offset."""

    _attr_name = "Yurtzeit"
    _attr_icon = "mdi:candle"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, custom_yurtzeits: list[dict] = None, muted_yurtzeits: list[dict] = None) -> None:
        #_LOGGER.debug("Initializing YurtzeitSensor")
        super().__init__()
        slug = "yurtzeit"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        # Fetch havdalah_offset from config
        config = hass.data[DOMAIN]["config"]
        self._havdalah_offset = timedelta(minutes=config.get("havdalah_offset", 72))  # Default 72 if not set
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
        self._muted_yurtzeits: dict[tuple[int, int], set[str]] = {}  # (month, day): set of muted entries
        self._custom_input = custom_yurtzeits or []  # From config
        self._muted_input = muted_yurtzeits or []  # From config

    async def async_added_to_hass(self) -> None:
        #_LOGGER.debug("YurtzeitSensor added to HA, starting setup")
        start_time = time.time()
        await super().async_added_to_hass()
        # Restore previous state
        last = await self.async_get_last_state()
        if last:
            self._state = last.state
            self._attributes = last.attributes or {}
            #_LOGGER.debug("Restored previous state")
        # Fetch Yurtzeits once on startup
        await self._fetch_yurtzeits()
        # Process custom Yurtzeits
        #_LOGGER.debug("Processing custom Yurtzeits")
        for custom in self._custom_input:
            month = custom.get('month')
            day = custom.get('day')
            if month and day:
                key = (month, day)
                entry = custom.get('entry', '')
                if key not in self._custom_yurtzeits:
                    self._custom_yurtzeits[key] = []
                self._custom_yurtzeits[key].append({'text': entry})
        #_LOGGER.debug(f"Processed {len(self._custom_input)} custom Yurtzeits")
        # Process muted Yurtzeits
        #_LOGGER.debug("Processing muted Yurtzeits")
        for muted in self._muted_input:
            month = muted.get('month')
            day = muted.get('day')
            if month and day:
                key = (month, day)
                entry = muted.get('entry', '')
                if key not in self._muted_yurtzeits:
                    self._muted_yurtzeits[key] = set()
                self._muted_yurtzeits[key].add(entry)
        #_LOGGER.debug(f"Processed {len(self._muted_input)} muted Yurtzeits")
        # Immediate calculation
        #_LOGGER.debug("Performing initial state update")
        await self._update_state()
        # Schedule sunset + offset update
        self._register_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )
        #_LOGGER.debug("Scheduled sunset update")
        # Minute-by-minute updates for precise flip
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )
        #_LOGGER.debug("Scheduled minute-by-minute update")
        total_time = time.time() - start_time
        #_LOGGER.debug(f"YurtzeitSensor setup completed in {total_time:.2f} seconds")

    def _schedule_update(self, *_args) -> None:
        #_LOGGER.debug("Scheduling state update")
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def _fetch_yurtzeits(self) -> None:
        """Fetch Yurtzeits from GitHub-hosted JSON."""
        #_LOGGER.debug("Fetching Yurtzeits from GitHub")
        start_time = time.time()
        cache_file = self.hass.config.path('yurtzeit_cache.json')
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
                                _LOGGER.warning(f"Skipping invalid cache key: {k}")
                        return valid_data
                    self._yurtzeits = await self.hass.async_add_executor_job(load_cache)
                    #_LOGGER.debug("Loaded Yurtzeits from cache")
                    refetch = False
                except json.JSONDecodeError:
                    _LOGGER.error(f"Corrupted cache file {cache_file}, forcing refetch")
        if refetch:
            github_url = "https://raw.githubusercontent.com/hitchin999/yidcal-data/main/yahrtzeit_cache.json"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(github_url, timeout=10) as response:
                        if response.status != 200:
                            _LOGGER.warning(f"Failed to fetch from GitHub: {response.status}")
                            return
                        text = await response.text()
                        data = json.loads(text)
                valid_data = {}
                for k, v in data.items():
                    try:
                        month_day = tuple(map(int, k.split('_')))
                        valid_data[month_day] = v
                    except (ValueError, TypeError):
                        _LOGGER.warning(f"Skipping invalid key: {k}")
                self._yurtzeits = valid_data
                def save_cache():
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=4)
                await self.hass.async_add_executor_job(save_cache)
                #_LOGGER.debug("Fetched and saved Yurtzeits from GitHub")
            except Exception as e:
                _LOGGER.error(f"Error fetching from GitHub: {e}")
        total_time = time.time() - start_time
        #_LOGGER.debug(f"Completed Yurtzeit fetch in {total_time:.2f} seconds")

    @property
    def state(self) -> str:
        return self._state or STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict:
        return self._attributes

    async def _update_state(self) -> None:
        """Recompute Yurtzeits based on current Hebrew date."""
        #_LOGGER.debug("Updating Yurtzeit state")
        now = datetime.now(self._tz)
        s = sun(self._loc.observer, date=now.date(), tzinfo=self._tz)
        switch_time = s["sunset"] + self._havdalah_offset
        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()
        heb = PHebrewDate.from_pydate(py_date)
        is_leap = Year(heb.year).leap
        lookup_month = 12 if heb.month in [12, 13] else heb.month
        key = (lookup_month, heb.day)
        fetched_entries = self._yurtzeits.get(key, [])
        fetched = [e['text'] for e in fetched_entries]  # Use all entries
        custom_entries = self._custom_yurtzeits.get((heb.month, heb.day), [])
        custom = [e['text'] for e in custom_entries]
        muted = self._muted_yurtzeits.get((heb.month, heb.day), set())
        todays = [entry for entry in fetched + custom if entry not in muted]
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
        #_LOGGER.debug(f"Updated state to: {self._state} with {len(todays)} entries")
