# custom_components/yidcal/yidcal_lib/luach_auto_json.py
"""Automatic once-a-year Erev Rosh Hashanah luach JSON export.

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

Active only while the Luach-PDF feature is enabled (it reuses that
service and its font/geo plumbing). Lifecycle mirrors the zmanim
coordinator: one instance is stashed in ``hass.data[DOMAIN]`` and its
timers are torn down on reload/unload.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
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

# The daily check fires at this local wall-clock time. The luach is
# date-based, so the exact minute is immaterial; 01:00 is safely past
# midnight (and any DST 00:00 fold) so "today" is unambiguous.
_CHECK_HOUR = 1
_CHECK_MINUTE = 0


def _target_hebrew_year(today) -> int:
    """The Hebrew year the stable file should hold.

    = the Hebrew year of TOMORROW. On 29 Elul (Erev Rosh Hashanah) that
    is the incoming year; on every other day it is the current Hebrew
    year. This single rule is both the Erev-RH trigger and the everyday
    "current year is present" invariant.
    """
    return PHebrewDate.from_pydate(today + timedelta(days=1)).year


def _stored_is_fresh(hass: HomeAssistant, target: int) -> bool:
    """True iff the stable JSON exists, is for ``target``, AND already
    carries the embedded weekly grid.

    Runs in the executor (file I/O). Any miss returns False -> regenerate.
    Covers a wrong year, a fresh install, a deleted/unreadable file, AND
    an older file written before the weekly grid was embedded (so an
    upgrading user gets the grid added on the next check, not only at the
    next Erev Rosh Hashanah).
    """
    path = Path(hass.config.config_dir) / _OUTPUT_SUBDIR / STABLE_JSON_NAME
    try:
        if not path.is_file():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("hebrew_year")) != int(target):
            return False
        weekly = data.get("weekly") or {}
        return bool(weekly.get("weeks"))
    except (OSError, ValueError, TypeError):
        return False


class ErevRoshHashanahJsonAutoGen:
    """Keeps the fixed-name yearly luach JSON current, refreshed at Erev RH."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._busy = False
        self._unsub_tick = None
        self._unsub_started = None

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
        if self._unsub_tick is not None:
            self._unsub_tick()
            self._unsub_tick = None
        if self._unsub_started is not None:
            self._unsub_started()
            self._unsub_started = None

    async def _async_check(self, *_) -> None:
        """Regenerate the stable JSON if it's missing, for the wrong year, or lacking the weekly grid."""
        if self._busy:
            return
        self._busy = True
        try:
            today = dt_util.now().date()
            target = _target_hebrew_year(today)
            fresh = await self.hass.async_add_executor_job(
                _stored_is_fresh, self.hass, target
            )
            if fresh:
                return  # current year AND weekly grid present
                        # nothing to do
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
                "\u2192 /local/yidcal-data/%s (yearly rows + weekly grid)",
                target,
                STABLE_JSON_NAME,
            )
        except Exception:  # noqa: BLE001 — the auto-run must never crash setup
            _LOGGER.exception(
                "YidCal: Erev-RH luach JSON auto-generation failed"
            )
        finally:
            self._busy = False


@callback
def async_setup_erev_rh_json(hass: HomeAssistant) -> None:
    """(Re)start the singleton auto-generator. Safe to call on every reload."""
    async_shutdown_erev_rh_json(hass)  # tear down any prior instance first
    inst = ErevRoshHashanahJsonAutoGen(hass)
    hass.data.setdefault(DOMAIN, {})[AUTO_JSON_KEY] = inst
    inst.async_start()


@callback
def async_shutdown_erev_rh_json(hass: HomeAssistant) -> None:
    """Stop and drop the singleton auto-generator if present."""
    inst = (hass.data.get(DOMAIN, {}) or {}).pop(AUTO_JSON_KEY, None)
    if inst is not None:
        inst.async_shutdown()
