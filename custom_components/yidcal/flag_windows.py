# custom_components/yidcal/flag_windows.py
"""When each holiday flag turns on and off, read from where YidCal already knows.

`sensor.yidcal_holiday` publishes a row of ~106 boolean flags and
`HolidayAttributeBinarySensor` mirrors one flag each. Both answer "is it Sukkos
*now*" and neither said when that stops being true, which is what anything
scheduling around one of them actually needs.

The answer was already being computed and thrown away
-----------------------------------------------------
`HolidaySensor.async_update` calls `zman_compute.compute_holiday_windows` on
every single update, gets all nine window shapes for the current festival date,
and gates each flag on `start <= now < end`. Its `_dynamic_window` resolves
which shape a flag uses, overrides included - Purim on Friday, the last day of
Chol HaMoed, and the rest. That is the only place in YidCal that knows a flag's
real window, and it discarded it the moment the comparison was done.

So it now records it instead, on `HolidaySensor._flag_windows`. Nothing here
recomputes a window and nothing here knows a rule about candle lighting or
havdalah; every moment below is a number the sensor handed over. Change a
window rule in `_dynamic_window` or a shape in `compute_holiday_windows` and
this follows without being touched.

What that leaves
----------------
* A flag that is **on**: its window is in the live table already. Free, exact,
  and correct through every override.

* A flag that is **off**: needs the date it next falls on, which lives inside
  the same update as a row of booleans for one moment - so it is found by
  asking the sensor about future moments, the way `upcoming_holiday_sensor`
  already does. Sampling twice a day is enough: 01:00 and 12:00 between them
  fall inside all nine window shapes. The sample that first reads *on* carries
  the exact window with it, so nothing has to be narrowed down afterwards.

* An **aggregate** flag - `סוכות (כל חג)`, `א׳ דיום טוב` - is `any(...)` of
  other flags, computed after the window filter, so it has no window of its
  own. Those are spanned from the flags they aggregate, which is what the
  aggregate means.

One scan answers all ~106 mirrors, cached behind a lock, so a minute tick
arriving at 106 entities produces one lookup and 105 cache reads.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)

CACHE_KEY = "_flag_windows_cache"

# How far ahead to look for a flag that is currently off.
#
# Two weeks is a deliberate middle. Phase 1 costs two evaluations a day, so the
# horizon is the whole cost of a scan, and this runs on every install rather
# than only on the machine it was written on. Fourteen days still clears the
# largest lookahead anything asks of it by a wide margin - Restart Guard's
# ceiling is 24 hours - and still reaches the next event through a quiet
# stretch: mid-Av finds Rosh Chodesh Elul ten days out.
#
# What it gives up is the far view. "When is Sukkos" asked in August now comes
# back blank rather than answered, which is the honest trade for halving the
# work on somebody else's Pi.
SCAN_DAYS = 14

# Night-only shapes (candle_alos, havdalah_alos) contain 01:00; day-only ones
# (alos_candle, alos_havdalah) contain 12:00; the rest span one or both. Two
# samples a day is the smallest set that can miss nothing.
COARSE_HOURS = (1, 12)

# Recompute at most this often even when nothing has flipped, so a scan cannot
# drift after a config change or a DST jump.
MAX_AGE = dt.timedelta(hours=6)

UNKNOWN = ""


async def _evaluate(sensor_factory, moment: dt.datetime, geo):
    """The flag row and its window table as they will read at `moment`.

    A throwaway HolidaySensor, not the live one. It carries the real entity_id
    but no `platform`, which `HolidaySensor.async_update` already checks before
    writing state - and now also before writing the shared window table, so a
    simulated row cannot overwrite today's.
    """
    sim = sensor_factory()
    sim._geo = geo
    await sim.async_update(moment)
    return (
        dict(getattr(sim, "_bool_attrs", {}) or {}),
        dict(getattr(sim, "_flag_windows", {}) or {}),
    )


def _iso(window) -> dict[str, str]:
    start, end = window
    return {"Window_Start": start.isoformat(), "Window_End": end.isoformat()}


async def _scan(
    hass: HomeAssistant, sensor_factory, now: dt.datetime
) -> dict[str, dict[str, str]]:
    """Every flag's window: the run it is in, or the next one it will enter."""
    cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
    tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
    geo = await get_geo(hass)
    now = now.astimezone(tz)

    seen: dict[dt.datetime, tuple[dict[str, bool], dict]] = {}

    async def evaluate(moment: dt.datetime):
        if moment not in seen:
            seen[moment] = await _evaluate(sensor_factory, moment, geo)
            # one evaluation is a few milliseconds and a whole scan is a few
            # hundred; yielding lets the loop interleave the difference
            await asyncio.sleep(0)
        return seen[moment]

    def day_samples(day: dt.date) -> list[dt.datetime]:
        return [
            dt.datetime.combine(day, dt.time(hour), tzinfo=tz)
            for hour in COARSE_HOURS
        ]

    current, live = await evaluate(now)

    # A flag on for several days holds a *different* window each day - Rosh
    # Chodesh is two, Chanukah is eight - because the table answers for one
    # festival date. Reporting a day boundary as the end would announce a
    # change that never happens, so a run is followed to where it really stops.
    runs: dict[str, list[dt.datetime]] = {
        flag: [window[0], window[1]] for flag, window in live.items()
    }

    # backwards, for the flags already on: how far back does this run go?
    # It stops at the first day the flag reads off, so a one-day flag costs two
    # evaluations and only a genuinely long run costs more.
    active = {flag for flag in runs if current.get(flag)}
    for offset in range(1, SCAN_DAYS + 1):
        if not active:
            break
        day = (now - dt.timedelta(days=offset)).date()
        still: set[str] = set()
        for moment in sorted(day_samples(day), reverse=True):
            row, table = await evaluate(moment)
            for flag in active:
                window = table.get(flag)
                if not row.get(flag) or window is None:
                    continue
                if window[0] < runs[flag][0]:
                    runs[flag][0] = window[0]
                still.add(flag)
        active = still

    # forwards, for everything: extend the runs above, and pick up the first
    # run of every flag that is off right now
    pending = {flag for flag, value in current.items() if not value}
    closed: set[str] = set()
    for offset in range(SCAN_DAYS + 1):
        day = (now + dt.timedelta(days=offset)).date()
        for moment in day_samples(day):
            if moment <= now:
                continue
            row, table = await evaluate(moment)
            for flag, window in table.items():
                if flag in closed:
                    continue
                if flag in runs:
                    if window[1] > runs[flag][1]:
                        runs[flag][1] = window[1]
                elif flag in pending and row.get(flag):
                    runs[flag] = [window[0], window[1]]
                    pending.discard(flag)
            # a run that has ended is finished with: a later occurrence is a
            # different window and must not stretch this one
            for flag in list(runs):
                if flag not in closed and not row.get(flag) and moment > runs[flag][1]:
                    closed.add(flag)

    windows = {flag: _iso(tuple(span)) for flag, span in runs.items()}

    # Anything with no window keeps blank values. An absent attribute cannot be
    # told apart from a broken scan; an empty one says "asked, found nothing".
    for flag in current:
        entry = windows.setdefault(flag, {})
        entry.setdefault("Window_Start", UNKNOWN)
        entry.setdefault("Window_End", UNKNOWN)

    return windows


class FlagWindows:
    """Shared, lazily refreshed cache of the flag window table."""

    def __init__(self, hass: HomeAssistant, sensor_factory):
        self._hass = hass
        self._factory = sensor_factory
        self._lock = asyncio.Lock()
        self._windows: dict[str, dict[str, str]] = {}
        self._computed_at: dt.datetime | None = None
        self._next_edge: dt.datetime | None = None

    def _stale(self, now: dt.datetime) -> bool:
        if self._computed_at is None:
            return True
        if now - self._computed_at >= MAX_AGE:
            return True
        return self._next_edge is not None and now >= self._next_edge

    async def async_windows_for(self, flag: str, now: dt.datetime) -> dict[str, str]:
        """This flag's window, refreshing the table if needed.

        Answers come from the cached table rather than from the live one
        directly: the live table holds one festival day, and a flag on for
        several days needs the run it belongs to, not today's slice of it.
        """
        async with self._lock:
            if self._stale(now):
                try:
                    self._windows = await _scan(self._hass, self._factory, now)
                    edges = [
                        dt.datetime.fromisoformat(value)
                        for entry in self._windows.values()
                        for value in entry.values()
                        if value
                    ]
                    ahead = [edge for edge in edges if edge > now]
                    self._next_edge = min(ahead) if ahead else None
                except Exception:  # noqa: BLE001 - never break the mirrors
                    _LOGGER.exception("YidCal could not scan flag windows")
                    self._windows = {}
                    self._next_edge = None
                # stamped either way, so a failure backs off instead of being
                # retried by each of 106 mirrors on every minute tick
                self._computed_at = now
        return dict(
            self._windows.get(flag)
            or {"Window_Start": UNKNOWN, "Window_End": UNKNOWN}
        )


def async_get_cache(hass: HomeAssistant, sensor_factory) -> FlagWindows:
    """The one cache for this Home Assistant, created on first use."""
    store = hass.data.setdefault(DOMAIN, {})
    cache = store.get(CACHE_KEY)
    if cache is None:
        cache = FlagWindows(hass, sensor_factory)
        store[CACHE_KEY] = cache
    return cache
