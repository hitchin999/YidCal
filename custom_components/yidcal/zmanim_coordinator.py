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
1. NOT an interval poller. Zmanim change once per civil day, and the
   relevant rollover instant differs per sensor (some roll at civil
   midnight, some at Alos HaShachar). We therefore use
   ``DataUpdateCoordinator`` only for its listener/refresh plumbing
   (``update_interval=None``) and drive refreshes from scheduled
   point-in-time callbacks at BOTH anchors:
       • next civil midnight
       • next Alos HaShachar
   computing/​rescheduling whichever comes first. Computation is
   shared, so honoring both anchors costs nothing extra and keeps
   every sensor's existing timing intact.

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
from homeassistant.helpers.event import async_track_point_in_time
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
            update_interval=None,  # event-driven; see module docstring
        )
        self._unsub_timer = None
        self._tallis_offset = DEFAULT_TALLIS_TEFILIN_OFFSET
        self._havdalah_offset = 72

    # ── lifecycle ──────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Do the first computation and arm the refresh timer.

        Called once from async_setup_entry after config is in
        hass.data. Safe to call again on reload (re-arms cleanly).
        """
        cfg = (self.hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._tallis_offset = int(
            cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET)
        )
        self._havdalah_offset = int(cfg.get("havdalah_offset", 72))
        await self.async_refresh()
        self._schedule_next()

    @callback
    def async_shutdown_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

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

    # ── refresh scheduling (midnight + Alos anchors) ───────────────

    def _next_anchor(self) -> datetime:
        """Return the next instant we must recompute at: the EARLIER of
        the next civil midnight and the next Alos HaShachar.

        Recomputing at both keeps the midnight-rollover sensors and the
        Alos-rollover sensors each seeing fresh data exactly when their
        own rollover fires. Computation is shared so honoring both is
        free.
        """
        win = self.data
        if win is not None:
            tz = win.tz
        else:
            # No window yet (first schedule before first compute) —
            # resolve tz the safe way (name string, never off geo).
            _, _tzname = _resolve_geo_and_tz(self.hass)
            tz = ZoneInfo(_tzname)
        now_local = dt_util.now().astimezone(tz)

        # Next civil midnight (start of tomorrow, local).
        next_midnight = datetime.combine(
            now_local.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=tz,
        )

        # Next Alos: today's if still ahead, else tomorrow's. Use the
        # cached window when available; otherwise fall back to midnight
        # only (first run before any data — rare, self-corrects on the
        # immediately-following refresh).
        next_alos = None
        if win is not None:
            today = now_local.date()
            for cand in (today, today + timedelta(days=1)):
                a = win.alos_for(cand)
                if a is not None and a > now_local:
                    next_alos = a
                    break

        if next_alos is None:
            return next_midnight
        return min(next_midnight, next_alos)

    @callback
    def _schedule_next(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        when = self._next_anchor()
        self._unsub_timer = async_track_point_in_time(
            self.hass, self._handle_anchor, when
        )
        _LOGGER.debug("YidCal zmanim: next recompute scheduled at %s", when)

    async def _handle_anchor(self, _now: datetime) -> None:
        await self.async_refresh()
        self._schedule_next()


@callback
def get_zmanim_coordinator(hass: HomeAssistant) -> ZmanimCoordinator | None:
    """Fetch the live coordinator, or None if not yet set up."""
    return (hass.data.get(DOMAIN, {}) or {}).get(COORDINATOR_KEY)
