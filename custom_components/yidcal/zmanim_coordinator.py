"""
custom_components/yidcal/zmanim_coordinator.py

Single-source-of-truth coordinator for YidCal daily zmanim.

WHY THIS EXISTS
===============
Historically every zman sensor independently built its own
``ZmanimCalendar``, recomputed sun events, and applied its own
rounding. That is N redundant astronomical computations per update
cycle and N copies of the rounding policy. This coordinator computes
the day's zmanim **once per location** (via the shared
``zman_compute.compute_zmanim_for_date``) and every sensor subscribes.

DESIGN NOTES (read before modifying)
====================================
1. NOT an interval poller, and NOT a one-shot point-in-time timer.
   Zmanim change once per civil day; the relevant rollover instant
   differs per sensor (some roll at civil midnight, some at Alos
   HaShachar). We use ``DataUpdateCoordinator`` only for its
   listener/refresh plumbing (``update_interval=None``) and drive
   refreshes from a single WALL-CLOCK minute tick
   (``async_track_time_change(second=0)``) — the same primitive the
   original per-sensor code used. HA re-arms it against the wall
   clock every tick, so it fires correctly when the system clock is
   STEPPED (NTP correction, host suspend/resume, VM migration,
   container pause, manual set). An earlier draft used
   ``async_track_point_in_time``, which arms a single MONOTONIC
   deadline and therefore never fires on a clock step — that froze
   the migrated sensors and is why this primitive was abandoned.
   There is no HA event for a raw OS clock change, so an event hook
   cannot substitute for a wall-clock tick. Each tick is a cheap
   crossing check; an actual recompute happens only on a civil-date
   change, an Alos-boundary flip, or once hourly as an idempotent
   safety net.

2. The coordinator does NOT decide rollover. It caches a 4-day
   window (civil today-2 … today+1) of fully-computed zmanim. The
   extra trailing day exists so an Alos-rollover sensor, which shows
   the previous civil day before Alos, still has a full
   yesterday/today/tomorrow trio available for its attributes. Each
   subscribing entity applies ITS OWN existing rollover rule when
   reading (midnight-camp picks by civil date; Alos-camp picks by
   comparing now to that day's Alos). This is deliberate: it lets the
   sensor migration be byte-identical per sensor — the coordinator
   changes WHERE computation happens, never WHAT a given sensor shows.

3. Both rounded (``ZmanEntry.dt_local``) and raw unrounded
   (``ZmanEntry.dt_raw_local``) values flow through unchanged — the
   coordinator just hands back ``ZmanEntry`` objects. Sensors that
   expose ``*_With_Seconds`` read ``.dt_raw_local``; everything else
   reads ``.dt_local``.

4. Failure resilience: a transient astro/geo error must not blank
   every zman sensor at once. On refresh failure we keep the last
   good window and reschedule; we only surface failure if there has
   never been a successful computation.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
import homeassistant.util.dt as dt_util

from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .yidcal_lib.zman_compute import (
    compute_zmanim_for_date,
    ZmanEntry,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)

_LOGGER = logging.getLogger(__name__)

# Key under hass.data[DOMAIN] where the live coordinator is stashed so
# entities (and a future check-zmanim service) can fetch it.
COORDINATOR_KEY = "_zmanim_coordinator"

# MGA Alos offset (minutes before sunrise, 0°50′ ≈ 72 min). Mirrors
# zman_compute._ALOS_OFFSET_MIN; duplicated here only for the refresh
# scheduling anchor (we must know Alos to know when to recompute).
_ALOS_OFFSET_MIN = 72


def _resolve_geo_and_tz(hass: HomeAssistant) -> tuple[GeoLocation, str]:
    """Build the GeoLocation AND return the tz NAME string from the
    integration's shared config (fallback to HA core config) — the
    SAME source the existing zman sensors use
    (``ZoneInfo(cfg.get("tzname", hass.config.time_zone))``).

    Critically we return the tz *name string* separately. Do NOT read
    the timezone back off ``GeoLocation.time_zone`` — the zmanim
    library converts it internally to a ``dateutil`` tzfile object,
    which is unhashable and cannot be passed to ``ZoneInfo`` (raises
    ``TypeError: unhashable type: 'tzfile'``).
    """
    cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
    lat = cfg.get("latitude", hass.config.latitude)
    lon = cfg.get("longitude", hass.config.longitude)
    tzname = cfg.get("tzname", hass.config.time_zone)
    geo = GeoLocation(
        name="YidCal",
        latitude=lat,
        longitude=lon,
        time_zone=tzname,
        elevation=0,
    )
    return geo, tzname


class ZmanimWindow:
    """Immutable-ish holder for one location's 4-day zmanim window.

    ``days`` maps a civil ``date`` → ``{label: ZmanEntry}``. Always contains civil (today-2) through (today+1) relative to
    the civil date the window was computed for.
    """

    __slots__ = ("anchor_date", "tz", "geo", "days")

    def __init__(
        self,
        anchor_date: date_cls,
        tz: ZoneInfo,
        geo: GeoLocation,
        days: dict[date_cls, dict[str, ZmanEntry]],
    ) -> None:
        self.anchor_date = anchor_date
        self.tz = tz
        self.geo = geo
        self.days = days

    def entry(self, label: str, civil_date: date_cls) -> ZmanEntry | None:
        """Return the ZmanEntry for ``label`` on ``civil_date``.

        Returns None if that date isn't in the cached window or the
        label is unknown — callers decide how to degrade (typically
        recompute on demand, but in practice the window always covers
        the full civil(today-2 … today+1) span, covering every
        sensor's rollover need including Alos-rollback.).
        """
        day = self.days.get(civil_date)
        if day is None:
            return None
        return day.get(label)

    def alos_for(self, civil_date: date_cls) -> datetime | None:
        """The (rounded) Alos HaShachar datetime for ``civil_date`` in
        this window — used by Alos-rollover sensors to decide which
        civil day's zmanim they should currently be showing.
        """
        e = self.entry("עלות השחר", civil_date)
        return e.dt_local if e else None


class ZmanimCoordinator(DataUpdateCoordinator[ZmanimWindow]):
    """Computes a 4-day zmanim window once per location and notifies
    all subscribed entities. Refresh is event-driven (midnight + Alos
    anchors), not interval-based.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="YidCal Zmanim",
            update_interval=None,  # driven by a wall-clock minute tick
        )
        self._unsub_tick = None
        self._tallis_offset = DEFAULT_TALLIS_TEFILIN_OFFSET
        self._havdalah_offset = 72
        # Crossing-detection memory. The minute tick is cheap; it only
        # triggers a real recompute when one of these changes vs the
        # last successful window:
        #   _last_anchor_date  — the civil date the cached window was
        #                        computed for (catches civil-midnight
        #                        rollover and any clock jump that lands
        #                        on a different date).
        #   _last_alos_state   — for the configured "today", whether we
        #                        were before or after that day's Alos
        #                        last tick (catches the Alos rollover
        #                        instant that midnight-date alone
        #                        wouldn't see).
        self._last_anchor_date: date_cls | None = None
        self._last_alos_state: bool | None = None

    # ── lifecycle ──────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Do the first computation and arm the wall-clock tick.

        Called once from async_setup_entry after config is in
        hass.data. Safe to call again on reload (re-arms cleanly).

        SCHEDULING DESIGN (read before changing):
        The old per-sensor code used HA's
        ``async_track_time_change(...)`` — a WALL-CLOCK primitive that
        HA re-arms every tick and that fires correctly when the system
        clock is stepped (NTP correction, host suspend/resume, VM
        migration, container pause, manual set). The first coordinator
        draft used ``async_track_point_in_time`` instead, which arms a
        single MONOTONIC-clock deadline: stepping the wall clock does
        NOT advance monotonic time, so the anchor never fired on a
        clock jump and the migrated sensors froze (validated: the
        unmigrated ``yidcal_alos`` rolled correctly while the
        coordinator-driven sensors did not). There is no HA event for
        a raw OS clock change, so an event hook cannot fix this.

        This design therefore mirrors the proven old-sensor pattern:
        one ``async_track_time_change`` firing every minute at
        ``second=0``. Each tick is a cheap "did we cross a rollover
        boundary?" check; an actual ``compute_zmanim_for_date`` only
        happens when the civil date changed, the Alos boundary was
        crossed, or once hourly as an idempotent safety recompute.
        1440 trivial datetime comparisons/day on a Pi is negligible
        and is exactly what the old code already did per sensor.
        """
        cfg = (self.hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._tallis_offset = int(
            cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET)
        )
        self._havdalah_offset = int(cfg.get("havdalah_offset", 72))

        await self.async_refresh()
        self._capture_crossing_state()

        # Single wall-clock primitive: fire every minute at second 0.
        # HA re-arms this against the wall clock each tick, so it
        # survives clock steps the way the old per-sensor code did.
        if self._unsub_tick is None:
            self._unsub_tick = async_track_time_change(
                self.hass, self._handle_tick, second=0
            )

    @callback
    def async_shutdown_timer(self) -> None:
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None

    # ── computation ────────────────────────────────────────────────

    async def _async_update_data(self) -> ZmanimWindow:
        """Compute the civil (today-2 … today+1) window for the location.

        Runs the blocking astronomy in the executor. On failure, if we
        already have good data, re-raise is suppressed by returning the
        last window (DataUpdateCoordinator would otherwise mark all
        entities unavailable for a transient hiccup).
        """
        try:
            return await self.hass.async_add_executor_job(
                self._compute_window
            )
        except Exception as err:  # noqa: BLE001 - resilience by design
            if self.data is not None:
                _LOGGER.warning(
                    "YidCal zmanim recompute failed (%s); keeping last "
                    "good window", err,
                )
                return self.data
            # Never had data — let the coordinator surface the failure.
            raise

    def _compute_window(self) -> ZmanimWindow:
        """Blocking: build the 4-day window. Executor-only."""
        geo, tzname = _resolve_geo_and_tz(self.hass)
        tz = ZoneInfo(tzname)
        now_local = dt_util.now().astimezone(tz)
        today = now_local.date()

        days: dict[date_cls, dict[str, ZmanEntry]] = {}
        # Window span: civil (today-2) … (today+1) inclusive — FOUR
        # days, not three. Reason: a sensor that rolls over at Alos
        # shows the PREVIOUS civil day before Alos. That rolled-back
        # "today" then itself needs a yesterday/today/tomorrow trio
        # for its *_Simple attributes — i.e. it reaches back to
        # civil(today-2). A 3-day window would leave that day uncached
        # and the Yesterdays_Simple attribute would render empty in
        # the post-midnight / pre-Alos window. Computation is cheap
        # and shared, so the extra day costs effectively nothing.
        for offset in (-2, -1, 0, 1):
            d = today + timedelta(days=offset)
            entries = compute_zmanim_for_date(
                geo=geo,
                tz=tz,
                base_date=d,
                tallis_offset=self._tallis_offset,
                havdalah_offset=self._havdalah_offset,
            )
            days[d] = {e.label: e for e in entries}

        return ZmanimWindow(
            anchor_date=today, tz=tz, geo=geo, days=days,
        )

    # ── wall-clock tick + rollover-crossing detection ──────────────

    def _crossing_key(self) -> tuple[date_cls, bool]:
        """Return (civil_date, is_after_alos) for 'now' in the
        configured tz, using the cached window's Alos when available.

        These two values together capture every rollover any sensor
        cares about:
          • civil_date changes  → civil-midnight rollover (and any
            clock jump that lands on a different date).
          • is_after_alos flips → the Alos rollover instant on the
            same civil date (which the date alone would miss).
        """
        win = self.data
        if win is not None:
            tz = win.tz
        else:
            _, _tzname = _resolve_geo_and_tz(self.hass)
            tz = ZoneInfo(_tzname)
        now_local = dt_util.now().astimezone(tz)
        today = now_local.date()

        after_alos = True
        if win is not None:
            a = win.alos_for(today)
            if a is not None:
                after_alos = now_local >= a
        return today, after_alos

    @callback
    def _capture_crossing_state(self) -> None:
        """Record the current crossing key as the baseline the next
        tick compares against. Called right after every successful
        refresh.
        """
        d, after = self._crossing_key()
        self._last_anchor_date = d
        self._last_alos_state = after

    async def _handle_tick(self, now: datetime) -> None:
        """Fires every minute at second 0 (wall-clock; HA re-arms it
        each tick, so it survives clock steps — unlike a one-shot
        monotonic point-in-time timer).

        Cheap path: compare the current crossing key to the last
        captured one. Recompute only when:
          • the civil date changed (midnight rollover or a clock jump
            to another day), OR
          • the Alos boundary flipped on the same date, OR
          • a new hour started (idempotent hourly safety recompute, so
            any unforeseen drift self-heals within ≤1 h even if the
            crossing check somehow missed).
        Otherwise do nothing — no recompute, no state write.
        """
        cur_date, cur_after_alos = self._crossing_key()

        date_changed = cur_date != self._last_anchor_date
        alos_flipped = (
            self._last_alos_state is not None
            and cur_after_alos != self._last_alos_state
        )
        hourly_safety = now.minute == 0

        if not (date_changed or alos_flipped or hourly_safety):
            return

        _LOGGER.debug(
            "YidCal zmanim recompute: date_changed=%s alos_flipped=%s "
            "hourly=%s (now=%s)",
            date_changed, alos_flipped, hourly_safety, now,
        )
        await self.async_refresh()
        # Re-baseline against the freshly computed window so the next
        # tick measures crossings from here.
        self._capture_crossing_state()


@callback
def get_zmanim_coordinator(hass: HomeAssistant) -> ZmanimCoordinator | None:
    """Fetch the live coordinator, or None if not yet set up."""
    return (hass.data.get(DOMAIN, {}) or {}).get(COORDINATOR_KEY)
