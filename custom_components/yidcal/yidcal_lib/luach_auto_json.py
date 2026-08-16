# custom_components/yidcal/yidcal_lib/luach_auto_json.py
"""Automatic Erev Rosh Hashanah luach JSON export.

On Erev Rosh Hashanah (29 Elul) every year, YidCal regenerates a
fixed-name JSON file holding the full ``yearly_multi_page`` luach for
the INCOMING Hebrew year, WITH the full-year weekly grid (per-day
zmanim) embedded under a ``weekly`` key, so a dashboard/card can
always fetch one stable URL and get the current year's data. The file lives at::

    /config/www/yidcal-data/luach_erev_rosh_hashanah.json

served as ``/local/yidcal-data/luach_erev_rosh_hashanah.json``. It keeps
the same name across years — overwritten in place, never timestamped,
and never touched by the auto-pruner (which only deletes files carrying
a generated-timestamp suffix).

Trigger design (self-healing — no reliance on being up at any instant):

* **Target year T** = the Hebrew year of *tomorrow*. On 29 Elul that is
  next year (the incoming year); every other day of the year it is the
  current Hebrew year. This one rule doubles as the Erev-RH trigger and
  the everyday "keep the current year present" invariant.
* A once-daily wall-clock tick, plus a check once Home Assistant has
  finished starting, compare the stored file's ``hebrew_year`` against
  T. If the file is missing, unreadable, or for a different year, it is
  regenerated for T via ``yidcal.generate_luach`` in ``json_only`` mode
  (writes only the JSON, stays quiet).
* Because the check is idempotent, an HA outage across Erev RH simply
  self-heals on the next tick / restart, and a healthy instance
  regenerates at most once per Hebrew year. A fresh install (or a
  deleted file) is filled in promptly rather than waiting for Erev RH.
* The same check compares the stored file's LOCATION against the one the
  integration resolved at setup. Move Home Assistant's home location and
  the next start re-geocodes it (that is what already moves every zman
  sensor); the stored luach then no longer matches and is rebuilt for the
  new place, instead of quietly serving the old town's times forever.

Multi-year window (opt-in)
--------------------------
With ``luach_json_multiyear`` enabled in the options, the same daily
check ALSO maintains a rolling window of per-year files::

    luach_erev_rosh_hashanah_5785.json      <- one year back
    luach_erev_rosh_hashanah_5786.json      <- T (the current year)
    luach_erev_rosh_hashanah_5787.json ...  <- N years ahead
    luach_years.json                        <- index of the above

The window is ``T-1 .. T+years_ahead``, recomputed on every check. Since
T advances on Erev Rosh Hashanah, the window advances with it: the new
far year gets generated and the year that fell off the back is deleted,
leaving exactly one past year on disk at all times. Nothing here is
date-triggered — the window is a pure function of T, so an instance that
was off over Rosh Hashanah catches up on its next check.

Generating a year with the weekly grid is expensive (a full year of
per-day zmanim), so at most ONE year is generated per pass and another
pass is armed a few minutes later while any remain. Filling a fresh
7-year window therefore takes about half an hour of light background
work rather than pinning a Raspberry Pi for minutes on end. The year
for T is not generated at all — it is copied from the stable file,
which the same pass has just brought up to date.

Active only while the Luach-PDF feature is enabled (it reuses that
service and its font/geo plumbing). Lifecycle mirrors the zmanim
coordinator: one instance is stashed in ``hass.data[DOMAIN]`` and its
timers are torn down on reload/unload.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change, async_call_later
from homeassistant.helpers.start import async_at_started
import homeassistant.util.dt as dt_util

from ..const import DOMAIN
from .luach_service import SERVICE_GENERATE_LUACH, _OUTPUT_SUBDIR

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN] key holding the singleton instance.
AUTO_JSON_KEY = "erev_rh_json_autogen"

# Stable output stem — identical across every year. The service treats
# ``output_path`` as the PDF path and derives the ``.json`` sidecar from
# it; in json_only mode only the ``.json`` is ever written.
_STABLE_STEM = "luach_erev_rosh_hashanah"
STABLE_PDF_NAME = f"{_STABLE_STEM}.pdf"
STABLE_JSON_NAME = f"{_STABLE_STEM}.json"

# Per-year files in the multi-year window, plus the index that lists
# them. ``_YEAR_FILE_RE`` is what makes cleanup safe: only files this
# module named are ever deleted.
INDEX_JSON_NAME = "luach_years.json"
_YEAR_FILE_RE = re.compile(rf"^{re.escape(_STABLE_STEM)}_(\d{{4,5}})\.json$")

#: Years kept BEHIND the current one. Fixed at 1 by design — a luach
#: card wants last year reachable (a yurtzeit, a date someone is looking
#: back at) but nobody needs a shelf of them, and each file is ~1MB.
YEARS_BACK = 1

#: Guard rails for the configured look-ahead. Mirrors the slider range in
#: config_flow so a hand-edited config entry can't ask for 400 years.
MIN_YEARS_AHEAD = 1
MAX_YEARS_AHEAD = 10
DEFAULT_YEARS_AHEAD = 5

#: Gap between per-year generations. Long enough that a Pi is idle again
#: before the next one starts; short enough that a fresh 7-year window
#: finishes within the hour.
_REARM_SECONDS = 300

# The daily check fires at this local wall-clock time. The luach is
# date-based, so the exact minute is immaterial; 01:00 is safely past
# midnight (and any DST 00:00 fold) so "today" is unambiguous.
_CHECK_HOUR = 1
_CHECK_MINUTE = 0

#: Bump if the index file's shape changes in a breaking way.
_INDEX_SCHEMA_VERSION = 1


def year_json_name(hebrew_year: int) -> str:
    """Filename of the per-year JSON for ``hebrew_year``."""
    return f"{_STABLE_STEM}_{int(hebrew_year)}.json"


def year_pdf_name(hebrew_year: int) -> str:
    """``output_path`` handed to the service for ``hebrew_year``.

    The service derives the ``.json`` sidecar from the PDF path, and in
    ``json_only`` mode no PDF is ever written — so this names the JSON
    without producing a stray PDF.
    """
    return f"{_STABLE_STEM}_{int(hebrew_year)}.pdf"


def _output_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.config_dir) / _OUTPUT_SUBDIR


def _target_hebrew_year(today) -> int:
    """The Hebrew year the stable file should hold.

    = the Hebrew year of TOMORROW. On 29 Elul (Erev Rosh Hashanah) that
    is the incoming year; on every other day it is the current Hebrew
    year. This single rule is both the Erev-RH trigger and the everyday
    "current year is present" invariant.
    """
    return PHebrewDate.from_pydate(today + timedelta(days=1)).year


def _clamp_years_ahead(value) -> int:
    """Coerce the configured look-ahead into the supported range."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_YEARS_AHEAD
    return max(MIN_YEARS_AHEAD, min(MAX_YEARS_AHEAD, n))


def expected_location(hass: HomeAssistant) -> dict | None:
    """Where the luach should be for, per the integration's own snap.

    Reads ``resolved_location`` from the config entry — the geocode
    async_setup_entry caches and every zman sensor uses. When Home
    Assistant's home coordinates move, setup re-resolves and rewrites that
    snap, so comparing against it is exactly the trigger the sensors
    already follow. Falls back to HA's raw coordinates when no snap is
    cached yet (first run, or a failed resolve), and returns None when
    there is nothing to compare against at all.

    Must be called on the event loop; the result is a plain dict, safe to
    hand to the executor.
    """
    try:
        entries = hass.config_entries.async_entries(DOMAIN)
        if not entries:
            return None
        data = {**(entries[0].data or {}), **(entries[0].options or {})}
        snap = data.get("resolved_location") or {}
        lat, lon, tz = snap.get("lat"), snap.get("lon"), snap.get("tzname")
        if lat is None or lon is None:
            lat, lon, tz = (
                hass.config.latitude, hass.config.longitude, hass.config.time_zone,
            )
        if lat is None or lon is None:
            return None
        # Rounded to ~11 m so float noise from a round-trip through JSON
        # can never look like a move.
        return {
            "lat": round(float(lat), 4),
            "lon": round(float(lon), 4),
            "tzname": str(tz or ""),
        }
    except (TypeError, ValueError, AttributeError):
        return None


def _location_matches(data: dict, expected: dict) -> bool:
    """Was this stored luach built for the location YidCal has now?

    A file that recorded no coordinates is treated as "can't say" and left
    alone — better than rebuilding every file on an upgrade. Every file the
    service has ever written does record them, so that only covers
    hand-made ones.
    """
    loc = data.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if lat is None or lon is None:
        return True
    try:
        if round(float(lat), 4) != expected["lat"]:
            return False
        if round(float(lon), 4) != expected["lon"]:
            return False
    except (TypeError, ValueError):
        return True
    stored_tz = str(loc.get("tzname") or "")
    if stored_tz and expected["tzname"] and stored_tz != expected["tzname"]:
        return False
    return True


def _file_is_fresh(
    path: Path, target: int, expected: dict | None = None,
) -> bool:
    """True iff ``path`` exists, is for Hebrew year ``target``, carries the
    embedded weekly grid, AND was built for the current location.

    Runs in the executor (file I/O). Any miss returns False -> regenerate.
    Covers a wrong year, a fresh install, a deleted/unreadable file, an
    older file written before the weekly grid was embedded (so an
    upgrading user gets the grid added on the next check, not only at the
    next Erev Rosh Hashanah), and a file left over from a previous
    location.
    """
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("hebrew_year")) != int(target):
            return False
        weekly = data.get("weekly") or {}
        if not weekly.get("weeks"):
            return False
        if expected is not None and not _location_matches(data, expected):
            return False
        return True
    except (OSError, ValueError, TypeError):
        return False


def _stored_is_fresh(
    hass: HomeAssistant, target: int, expected: dict | None = None,
) -> bool:
    """``_file_is_fresh`` for the fixed-name current-year file."""
    return _file_is_fresh(_output_dir(hass) / STABLE_JSON_NAME, target, expected)


def _hebrew_year_letters(hebrew_year: int) -> str:
    """``5786`` -> ``תשפ״ו``; empty string if it can't be rendered."""
    try:
        from . import halacha_events as _he

        return _he.hebrew_year_letters(int(hebrew_year)) or ""
    except Exception:  # noqa: BLE001 — cosmetic field, never fatal
        return ""


# ── Executor-side filesystem work ────────────────────────────────────
# Everything below touches the disk and is called via
# hass.async_add_executor_job.

def _scan_window(
    hass: HomeAssistant, window: list[int], expected: dict | None = None,
) -> dict:
    """Survey the output directory against the desired ``window``.

    Returns ``{"missing": [...], "stale": [...], "moved": [...]}`` — years
    in the window whose file is absent or unusable, per-year files whose
    year is no longer in the window (i.e. it rolled off the back), and the
    subset of ``missing`` that exists but was built for another location.
    """
    out_dir = _output_dir(hass)
    missing: list[int] = []
    moved: list[int] = []
    for y in window:
        path = out_dir / year_json_name(y)
        if not path.is_file():
            missing.append(y)
        elif not _file_is_fresh(path, y, expected):
            moved.append(y)
            missing.append(y)

    stale: list[str] = []
    try:
        if out_dir.is_dir():
            for f in out_dir.iterdir():
                m = _YEAR_FILE_RE.match(f.name)
                if m and int(m.group(1)) not in window:
                    stale.append(f.name)
    except OSError as err:
        _LOGGER.warning("YidCal: could not list %s: %s", out_dir, err)
    return {"missing": missing, "stale": sorted(stale), "moved": moved}


def _delete_names(hass: HomeAssistant, names: list[str]) -> list[str]:
    """Delete the given basenames from the output dir. Best-effort.

    Only ever called with names this module generated (they matched
    ``_YEAR_FILE_RE`` or are the index) — a user's own files and the
    service's timestamped outputs are never candidates.
    """
    out_dir = _output_dir(hass)
    gone: list[str] = []
    for name in names:
        try:
            (out_dir / name).unlink()
            gone.append(name)
        except FileNotFoundError:
            continue
        except OSError as err:
            _LOGGER.warning("YidCal: could not delete %s: %s", name, err)
    return gone


def _copy_stable_to_year(
    hass: HomeAssistant, target: int, expected: dict | None = None,
) -> bool:
    """Seed the per-year file for T by copying the stable file.

    The stable file already holds exactly this year's data (the same
    pass just verified/regenerated it), so copying it costs a file write
    instead of a full year's zmanim computation. Returns False if the
    stable file isn't actually for ``target``, in which case the caller
    falls back to generating it.
    """
    out_dir = _output_dir(hass)
    src = out_dir / STABLE_JSON_NAME
    if not _file_is_fresh(src, target, expected):
        return False
    try:
        shutil.copyfile(src, out_dir / year_json_name(target))
        return True
    except OSError as err:
        _LOGGER.warning("YidCal: could not copy the stable luach JSON: %s", err)
        return False


def _write_index(hass: HomeAssistant, window: list[int], target: int) -> None:
    """(Re)write ``luach_years.json`` describing the window.

    ``ready`` says whether that year's file is on disk right now, so a
    card can show what's available while the rest is still building.
    """
    out_dir = _output_dir(hass)
    years = []
    for y in window:
        name = year_json_name(y)
        path = out_dir / name
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        years.append({
            "hebrew_year": y,
            "hebrew_year_he": _hebrew_year_letters(y),
            "file": name,
            "url": f"/local/yidcal-data/{name}",
            "ready": size > 0,
            "bytes": size,
            "current": y == target,
        })
    payload = {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(),
        "current_hebrew_year": target,
        "current_hebrew_year_he": _hebrew_year_letters(target),
        "current_url": f"/local/yidcal-data/{STABLE_JSON_NAME}",
        "years_back": YEARS_BACK,
        "years_ahead": len(window) - YEARS_BACK - 1,
        "years": years,
    }
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / INDEX_JSON_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as err:
        _LOGGER.warning("YidCal: could not write %s: %s", INDEX_JSON_NAME, err)


def _list_all_year_files(hass: HomeAssistant) -> list[str]:
    """Every per-year file plus the index — used to clean up on disable."""
    out_dir = _output_dir(hass)
    names: list[str] = []
    try:
        if out_dir.is_dir():
            for f in out_dir.iterdir():
                if _YEAR_FILE_RE.match(f.name) or f.name == INDEX_JSON_NAME:
                    names.append(f.name)
    except OSError:
        return []
    return sorted(names)


class ErevRoshHashanahJsonAutoGen:
    """Keeps the fixed-name yearly luach JSON current, refreshed at Erev RH.

    With ``multiyear`` on it additionally maintains the rolling
    ``T-YEARS_BACK .. T+years_ahead`` window of per-year files.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        multiyear: bool = False,
        years_ahead: int = DEFAULT_YEARS_AHEAD,
    ) -> None:
        self.hass = hass
        self.multiyear = bool(multiyear)
        self.years_ahead = _clamp_years_ahead(years_ahead)
        self._busy = False
        self._unsub_tick = None
        self._unsub_started = None
        self._unsub_rearm = None

    @callback
    def async_start(self) -> None:
        """Arm the daily tick and a run-at-startup catch-up check."""
        # Daily wall-clock tick (the same primitive the zmanim
        # coordinator uses). Date-based, so any time on the Erev-RH civil
        # day triggers the year rollover.
        self._unsub_tick = async_track_time_change(
            self.hass,
            self._async_check,
            hour=_CHECK_HOUR,
            minute=_CHECK_MINUTE,
            second=0,
        )
        # Catch-up once HA has finished starting (fires immediately on a
        # reload, when HA is already running). Regenerates only if the
        # file is missing / stale for the target year.
        self._unsub_started = async_at_started(self.hass, self._async_check)

    @callback
    def async_shutdown(self) -> None:
        """Cancel timers/listeners so nothing fires after reload/unload."""
        for attr in ("_unsub_tick", "_unsub_started", "_unsub_rearm"):
            unsub = getattr(self, attr, None)
            if unsub is not None:
                unsub()
                setattr(self, attr, None)

    @callback
    def _async_rearm(self, delay: float = _REARM_SECONDS) -> None:
        """Schedule one more pass, replacing any already pending."""
        if self._unsub_rearm is not None:
            self._unsub_rearm()
            self._unsub_rearm = None

        async def _fire(_now):
            self._unsub_rearm = None
            await self._async_check()

        self._unsub_rearm = async_call_later(self.hass, delay, _fire)

    def _window(self, target: int) -> list[int]:
        """The Hebrew years that should be on disk right now."""
        return list(range(target - YEARS_BACK, target + self.years_ahead + 1))

    async def _async_generate_year(self, hebrew_year: int) -> None:
        """Write one per-year JSON via the luach service."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_LUACH,
            {
                "style": "yearly_multi_page",
                "hebrew_year": int(hebrew_year),
                "output_path": year_pdf_name(hebrew_year),
                "json_only": True,
                "include_weekly": True,
            },
            blocking=True,
        )

    async def _async_check(self, *_) -> None:
        """Bring the stable file — and, if enabled, the window — up to date."""
        if self._busy:
            # A pass is already running (a daily tick landing on top of a
            # re-arm). Don't drop the work: come back for it shortly.
            self._async_rearm(60)
            return
        self._busy = True
        try:
            today = dt_util.now().date()
            target = _target_hebrew_year(today)
            # Where the luach should be for right now. Read on the event
            # loop, then handed to the executor. If Home Assistant's home
            # location moved, setup has already re-geocoded it, so this no
            # longer matches what the stored files say and they get rebuilt.
            expected = expected_location(self.hass)

            # ── 1. The fixed-name current-year file (always maintained) ──
            fresh = await self.hass.async_add_executor_job(
                _stored_is_fresh, self.hass, target, expected
            )
            if not fresh:
                await self.hass.services.async_call(
                    DOMAIN,
                    SERVICE_GENERATE_LUACH,
                    {
                        "style": "yearly_multi_page",
                        "hebrew_year": target,
                        "output_path": STABLE_PDF_NAME,
                        "json_only": True,
                        "include_weekly": True,
                    },
                    blocking=True,
                )
                _LOGGER.info(
                    "YidCal: refreshed Erev-RH luach JSON for Hebrew year %s "
                    "→ /local/yidcal-data/%s (yearly rows + weekly grid)%s",
                    target,
                    STABLE_JSON_NAME,
                    (
                        " at %s, %s" % (expected["lat"], expected["lon"])
                        if expected else ""
                    ),
                )

            # ── 2. The rolling multi-year window (opt-in) ──
            if not self.multiyear:
                # Turned off (or never on): drop any window files left
                # behind so the folder returns to the single-file layout
                # instead of keeping years nothing will ever refresh.
                leftovers = await self.hass.async_add_executor_job(
                    _list_all_year_files, self.hass
                )
                if leftovers:
                    gone = await self.hass.async_add_executor_job(
                        _delete_names, self.hass, leftovers
                    )
                    _LOGGER.info(
                        "YidCal: multi-year luach JSON is off — removed %d "
                        "unmaintained file(s): %s",
                        len(gone), ", ".join(gone),
                    )
                return

            window = self._window(target)
            scan = await self.hass.async_add_executor_job(
                _scan_window, self.hass, window, expected
            )

            # Drop years that rolled off the back of the window. This is
            # what makes the window advance on Erev Rosh Hashanah: the
            # new far year appears below, the oldest disappears here.
            if scan["stale"]:
                gone = await self.hass.async_add_executor_job(
                    _delete_names, self.hass, scan["stale"]
                )
                if gone:
                    _LOGGER.info(
                        "YidCal: multi-year luach window moved on — removed "
                        "%s", ", ".join(gone),
                    )

            if scan.get("moved"):
                _LOGGER.info(
                    "YidCal: location changed — %d stored luach year(s) %s "
                    "were built elsewhere; rebuilding them for %s, %s one at "
                    "a time",
                    len(scan["moved"]), scan["moved"],
                    expected["lat"] if expected else "?",
                    expected["lon"] if expected else "?",
                )

            missing = list(scan["missing"])

            # The current year is a copy of the stable file we just
            # verified — no need to recompute a whole year of zmanim.
            if target in missing:
                if await self.hass.async_add_executor_job(
                    _copy_stable_to_year, self.hass, target, expected
                ):
                    missing.remove(target)
                    _LOGGER.debug(
                        "YidCal: seeded %s from the stable luach JSON",
                        year_json_name(target),
                    )

            if missing:
                # Nearest years first: the ones a dashboard is most
                # likely to open become available soonest.
                missing.sort(key=lambda y: (abs(y - target), y))
                nxt = missing[0]
                _LOGGER.info(
                    "YidCal: building multi-year luach JSON for %s (%s) — "
                    "%d of %d year(s) still to go",
                    nxt, _hebrew_year_letters(nxt) or nxt,
                    len(missing), len(window),
                )
                await self._async_generate_year(nxt)
                missing.remove(nxt)

            await self.hass.async_add_executor_job(
                _write_index, self.hass, window, target
            )

            if missing:
                # More to build — come back for the next one shortly so
                # the whole window fills in without a long CPU pin.
                self._async_rearm()
            else:
                _LOGGER.debug(
                    "YidCal: multi-year luach window complete (%s–%s)",
                    window[0], window[-1],
                )
        except Exception:  # noqa: BLE001 — the auto-run must never crash setup
            _LOGGER.exception(
                "YidCal: Erev-RH luach JSON auto-generation failed"
            )
        finally:
            self._busy = False


@callback
def async_setup_erev_rh_json(
    hass: HomeAssistant,
    *,
    multiyear: bool = False,
    years_ahead: int = DEFAULT_YEARS_AHEAD,
) -> None:
    """(Re)start the singleton auto-generator. Safe to call on every reload."""
    async_shutdown_erev_rh_json(hass)  # tear down any prior instance first
    inst = ErevRoshHashanahJsonAutoGen(
        hass, multiyear=multiyear, years_ahead=years_ahead,
    )
    hass.data.setdefault(DOMAIN, {})[AUTO_JSON_KEY] = inst
    inst.async_start()


@callback
def async_shutdown_erev_rh_json(hass: HomeAssistant) -> None:
    """Stop and drop the singleton auto-generator if present."""
    inst = (hass.data.get(DOMAIN, {}) or {}).pop(AUTO_JSON_KEY, None)
    if inst is not None:
        inst.async_shutdown()
