# custom_components/yidcal/flag_windows.py
"""When each holiday flag turns on and off, computed from the flag specs.

`sensor.yidcal_holiday` publishes a row of boolean flags and
`HolidayAttributeBinarySensor` mirrors one flag each. Both say what is true
*now*; anything scheduling around a flag also needs to know when it starts and
when it stops.

Every flag is defined once, in `halacha_events.FLAG_SPECS`: the days it belongs
to, plus the shape of its window on such a day. The holiday sensor turns a flag
on exactly while `now` is inside one of those windows, so the windows are
computed here straight from the same spec, day by day, instead of by sampling
the sensor at chosen hours:

* a flag's windows that touch or overlap are one run (two days of Rosh Chodesh,
  the eight days of Chanukah, שובבים);
* an aggregate flag (`FLAG_AGGREGATES`: `סוכות (כל חג)`, `א׳ דיום טוב`, ...) is
  on while any member is, so its runs are the union of the members' windows;
* a mirror publishes the run it is in, or once that ends, the next one. The
  search reaches a year ahead, and further for flags that do not come every
  year (שבת ערב פורים, ערב פסח מוקדם, ...);
* `calendar.yidcal_holiday` gets every run overlapping the range it is asked
  for, whole, from the same computation.

Per-day windows and answers are cached for the location and settings they were
computed with. A change to either - an options update, a reload - starts a
fresh cache on the next lookup, so a new offset is never answered with the old
one. One cache serves every mirror and the calendar.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import flag_window
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)

CACHE_KEY = "_flag_windows_cache"

# A run already under way started at most this long ago (שובבים ת"ת, the
# longest run, is eight weeks).
LOOKBACK_DAYS = 70

# First search horizon, enough for every flag that comes each year; flags that
# skip years are searched further, up to MAX_SEARCH_DAYS.
LOOKAHEAD_DAYS = 400
MAX_SEARCH_DAYS = 3000

UNKNOWN = ""


def _iso(window) -> dict[str, str]:
    if window is None:
        return {"Window_Start": UNKNOWN, "Window_End": UNKNOWN}
    start, end = window
    return {"Window_Start": start.isoformat(), "Window_End": end.isoformat()}


class FlagWindows:
    """Shared cache of each flag's current or next run."""

    def __init__(self, hass: HomeAssistant, sensor_factory):
        self._hass = hass
        self._factory = sensor_factory
        self._lock = asyncio.Lock()
        self._settings: tuple | None = None
        self._checked_minute: dt.datetime | None = None
        self._geo = None
        self._tz: ZoneInfo | None = None
        self._days: dict[tuple[str, dt.date], tuple[dt.datetime, dt.datetime] | None] = {}
        self._answers: dict[str, tuple[tuple[dt.datetime, dt.datetime] | None, dt.date]] = {}

    async def _async_check_settings(self, now: dt.datetime) -> None:
        """Start a fresh cache when the location or any setting changed."""
        minute = now.replace(second=0, microsecond=0)
        if self._checked_minute == minute and self._settings is not None:
            return
        self._checked_minute = minute
        sim = self._factory()
        geo = await get_geo(self._hass)
        cfg = self._hass.data.get(DOMAIN, {}).get("config", {})
        tzname = cfg.get("tzname", self._hass.config.time_zone)
        settings = (
            geo.latitude, geo.longitude, getattr(geo, "elevation", 0), tzname,
            sim._candle_offset, sim._havdalah_offset, sim._diaspora,
            bool(getattr(sim, "_ykk_every_month", False)),
        )
        if settings != self._settings:
            self._settings = settings
            self._geo = geo
            self._tz = ZoneInfo(tzname)
            self._days.clear()
            self._answers.clear()

    def _window(self, name: str, day: dt.date):
        key = (name, day)
        if key not in self._days:
            rule, shape = he.FLAG_SPECS[name]
            _lat, _lon, _elev, _tzname, candle, havdalah, diaspora, every_month = self._settings
            option = he.FLAG_RULE_OPTIONS.get(name)
            kwargs = {option: every_month} if option == "every_month" else {}
            window = None
            if rule(day, diaspora, **kwargs):
                shape_name = shape(day, diaspora) if callable(shape) else shape
                window = flag_window(
                    geo=self._geo, tz=self._tz, day=day, shape=he.FLAG_SHAPES[shape_name],
                    candle_offset=candle, havdalah_offset=havdalah,
                )
            self._days[key] = window
        return self._days[key]

    def _runs(self, flag: str, first: dt.date, last: dt.date) -> list[tuple[dt.datetime, dt.datetime]]:
        """The flag's runs from its windows on days `first`..`last`, in order."""
        members = he.FLAG_AGGREGATES.get(flag, (flag,))
        if not all(member in he.FLAG_SPECS for member in members):
            return []
        windows = sorted(
            window
            for member in members
            for offset in range((last - first).days + 1)
            if (window := self._window(member, first + dt.timedelta(days=offset))) is not None
        )
        runs: list[tuple[dt.datetime, dt.datetime]] = []
        for start, end in windows:
            if runs and start <= runs[-1][1]:
                runs[-1] = (runs[-1][0], max(runs[-1][1], end))
            else:
                runs.append((start, end))
        return runs

    def _find_run(self, flag: str, now: dt.datetime):
        """The run `now` is in, else the next one; None if none within MAX_SEARCH_DAYS."""
        first = now.date() - dt.timedelta(days=LOOKBACK_DAYS)
        horizon = LOOKAHEAD_DAYS
        while True:
            last = now.date() + dt.timedelta(days=horizon)
            run = next((run for run in self._runs(flag, first, last) if run[1] > now), None)
            if run is not None and run[1].date() < last - dt.timedelta(days=1):
                return run
            if horizon >= MAX_SEARCH_DAYS:
                return run
            horizon = min(horizon * 2, MAX_SEARCH_DAYS)

    async def async_windows_for(self, flag: str, now: dt.datetime) -> dict[str, str]:
        """This flag's current run, or its next one once that is over."""
        async with self._lock:
            try:
                await self._async_check_settings(now)
                cached = self._answers.get(flag)
                stale = (
                    cached is None
                    or (cached[0] is not None and now >= cached[0][1])
                    or (cached[0] is None and now.date() != cached[1])
                )
                if stale:
                    if len(self._days) > 400_000:
                        self._days.clear()
                    run = await self._hass.async_add_executor_job(self._find_run, flag, now)
                    self._answers[flag] = (run, now.date())
                window = self._answers[flag][0]
            except Exception:  # noqa: BLE001 - never break the mirrors
                _LOGGER.debug("YidCal could not compute the window for %s", flag, exc_info=True)
                window = None
        return _iso(window)


    async def async_runs_between(
        self, flags, start: dt.datetime, end: dt.datetime
    ) -> dict[str, list[tuple[dt.datetime, dt.datetime]]]:
        """Every run of each flag that overlaps [start, end), whole rather than clipped."""
        first = start.date() - dt.timedelta(days=LOOKBACK_DAYS)
        last = end.date() + dt.timedelta(days=LOOKBACK_DAYS)

        def collect():
            return {
                flag: [run for run in self._runs(flag, first, last) if run[1] > start and run[0] < end]
                for flag in flags
            }

        async with self._lock:
            await self._async_check_settings(start)
            if len(self._days) > 400_000:
                self._days.clear()
            return await self._hass.async_add_executor_job(collect)


def async_get_cache(hass: HomeAssistant, sensor_factory) -> FlagWindows:
    """The one cache for this Home Assistant, created on first use.

    Every caller's factory replaces the stored one, so after a reload the
    settings come from the new entities rather than the ones that first
    created the cache.
    """
    store = hass.data.setdefault(DOMAIN, {})
    cache = store.get(CACHE_KEY)
    if cache is None:
        cache = FlagWindows(hass, sensor_factory)
        store[CACHE_KEY] = cache
    else:
        cache._factory = sensor_factory
    return cache
