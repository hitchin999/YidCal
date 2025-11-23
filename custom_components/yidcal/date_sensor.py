from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.const import STATE_UNKNOWN
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from pyluach.hebrewcal import Year, HebrewDate as PHebrewDate

from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .zman_sensors import get_geo
from .yidcal_lib.helper import int_to_hebrew
from .device import YidCalDevice, YidCalDisplayDevice

_LOGGER = logging.getLogger(__name__)


def _round_half_up(dt: datetime) -> datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    """Always bump to the *next* full minute (Motzi-style)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


def get_hebrew_month_name(month: int, year: int) -> str:
    """
    Map Pyluach month-numbers to Hebrew month names, handling leap years.
    """
    if month == 12:
        return "אדר א׳" if Year(year).leap else "אדר"
    if month == 13:
        return "אדר ב׳"
    return {
        1:  "ניסן",
        2:  "אייר",
        3:  "סיון",
        4:  "תמוז",
        5:  "אב",
        6:  "אלול",
        7:  "תשרי",
        8:  "חשון",
        9:  "כסלו",
        10: "טבת",
        11: "שבט",
    }.get(month, "")


def _normalize_hebrew_punct(txt: str) -> str:
    """Convert Hebrew geresh/gershayim to ASCII quotes to match your UI preference."""
    return txt.replace("\u05F4", '"').replace("\u05F3", "'")


# Static month options include both leap/non-leap Adar forms (normalized)
CHODESH_OPTIONS = [
    "ניסן",
    "אייר",
    "סיון",
    "תמוז",
    "אב",
    "אלול",
    "תשרי",
    "חשון",
    "כסלו",
    "טבת",
    "שבט",
    "אדר",
    "אדר א׳",
    "אדר ב׳",
]


class DateSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Today’s Hebrew date. Flips at (sunset + havdalah_offset), rounded Motzi-style."""

    _attr_name = "Date"
    _attr_icon = "mdi:calendar-range"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "date"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._havdalah_offset = timedelta(minutes=havdalah_offset)

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        tzname = cfg.get("tzname", hass.config.time_zone)
        self._tz = ZoneInfo(tzname)

        self._geo = None
        self._state: str | None = None

    def _schedule_update(self, *_args) -> None:
        """Thread-safe scheduling of _update_state on the event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous state, compute initial value, and schedule updates."""
        await super().async_added_to_hass()

        # 1) Restore previous state
        last = await self.async_get_last_state()
        if last:
            self._state = last.state

        # 2) Shared geo (same as zman sensors)
        self._geo = await get_geo(self.hass)

        # 3) Immediate first calculation
        await self._update_state()

        # 4) Daily sunset+offset update
        self._register_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )

        # 5) Top-of-minute tick
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._schedule_update,
                second=0,
            )
        )

        # 6) Extra 1-minute interval for time-travel tests
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    @property
    def state(self) -> str:
        return self._state or STATE_UNKNOWN

    async def _update_state(self) -> None:
        """Recompute date based on sunset+offset boundary (with Motzi-style rounding)."""
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        # Zmanim sunset for *today* (same engine as Zman Motzi)
        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=now.date())
            .sunset()
            .astimezone(self._tz)
        )

        raw_switch_time = sunset + self._havdalah_offset

        # Match Motzi rounding: always bump to next full minute
        switch_time = _round_ceil(raw_switch_time)

        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()

        heb = PHebrewDate.from_pydate(py_date)
        day_heb = int_to_hebrew(heb.day)
        month_heb = get_hebrew_month_name(heb.month, heb.year)
        year_num = heb.year % 1000
        year_heb = int_to_hebrew(year_num)

        state = f"{day_heb} {month_heb} {year_heb}"
        state = _normalize_hebrew_punct(state)

        self._state = state
        self.async_write_ha_state()


class ChodeshSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Current Hebrew month (enum). Flips at (sunset + havdalah_offset)."""

    _attr_name = "Chodesh"
    _attr_icon = "mdi:calendar-month"
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CHODESH_OPTIONS

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "chodesh"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._havdalah_offset = timedelta(minutes=havdalah_offset)

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        tzname = cfg.get("tzname", hass.config.time_zone)
        self._tz = ZoneInfo(tzname)

        self._geo = None
        self._state: str | None = None

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._state = last.state

        self._geo = await get_geo(self.hass)
        await self._update_state()

        self._register_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )

        self._register_listener(
            async_track_time_change(
                self.hass,
                self._schedule_update,
                second=0,
            )
        )

        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    @property
    def state(self) -> str:
        return self._state or STATE_UNKNOWN

    async def _update_state(self) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=now.date())
            .sunset()
            .astimezone(self._tz)
        )
        raw_switch_time = sunset + self._havdalah_offset
        switch_time = _round_ceil(raw_switch_time)

        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()
        heb = PHebrewDate.from_pydate(py_date)

        month_heb = get_hebrew_month_name(heb.month, heb.year)

        self._state = month_heb
        self.async_write_ha_state()


class YomLChodeshSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Current Hebrew day-of-month (enum). Flips at (sunset + havdalah_offset)."""

    _attr_name = "Yom L'Chodesh"
    _attr_icon = "mdi:calendar-today"
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [int_to_hebrew(i) for i in range(1, 31)]

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "yom_lchodesh"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._havdalah_offset = timedelta(minutes=havdalah_offset)

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        tzname = cfg.get("tzname", hass.config.time_zone)
        self._tz = ZoneInfo(tzname)

        self._geo = None
        self._state: str | None = None

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._state = last.state

        self._geo = await get_geo(self.hass)
        await self._update_state()

        self._register_sunset(
            self.hass,
            self._schedule_update,
            offset=self._havdalah_offset,
        )

        self._register_listener(
            async_track_time_change(
                self.hass,
                self._schedule_update,
                second=0,
            )
        )

        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    @property
    def state(self) -> str:
        return self._state or STATE_UNKNOWN

    async def _update_state(self) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=now.date())
            .sunset()
            .astimezone(self._tz)
        )
        raw_switch_time = sunset + self._havdalah_offset
        switch_time = _round_ceil(raw_switch_time)

        py_date = now.date() + timedelta(days=1) if now >= switch_time else now.date()
        heb = PHebrewDate.from_pydate(py_date)

        day_heb = int_to_hebrew(heb.day)

        self._state = day_heb
        self.async_write_ha_state()
