"""
custom_components/yidcal/pruzbol_sensors.py

Pruzbol (פרוזבול) sensors, both grouped under the YidCal — Special Sensors
device:

  binary_sensor.yidcal_pruzbol      ON from alos until candle-lighting on an
                                    Erev Rosh Hashana that BRACKETS a Shmita
                                    year — the same alos → candle-lighting
                                    window convention as
                                    binary_sensor.yidcal_erev.
  sensor.yidcal_pruzbol_display     ALWAYS shows the printed-luach Hebrew line
                                    for the next pruzbol, e.g.
                                    "בער״ה תש״צ צריכין לעשות פרוזבול".
                                    The year is derived, never hard-coded.

TWO OCCURRENCES PER CYCLE — the printed SF sheets carry two different notes,
because the Erev Rosh Hashana that ENTERS shmita and the one that LEAVES it are
not the same halacha:

  • 29 Elul of cycle-year 6 → "chumra"    — entering shmita:
        בער״ה תשפ״ב יש מחמירים לעשות פרוזבול (לכתחלה)
  • 29 Elul of cycle-year 7 → "required"  — leaving shmita (shevi'is has just
    ended and the debts are about to be cancelled):
        בער״ה תשפ״ג צריכין לעשות פרוזבול

They fall in CONSECUTIVE years (5781 then 5782), then nothing for six years —
so the look-ahead walks year by year rather than stepping a whole cycle.

NOTHING here re-derives the rule. ``halacha_events`` is the single source of
truth for all four pieces:

    pruzbol_kind()        which note (if any) belongs on a date
    pruzbol_shmita_year() the shmita year the date brackets
    pruzbol_note()        the printed Hebrew line
    PRUZBOL_FOOTNOTE_HE   the נוסח

The yearly luach's note and its נוסח footnote read the very same functions, so
these sensors and the printed sheet can never disagree.

ROLLOVER: the occurrence "in view" advances the moment its deadline passes —
i.e. at candle-lighting on that Erev Rosh Hashana, in lock-step with the binary
sensor going OFF. Both sensors then point at the next occurrence (the "required"
one a year later, or — once that has passed — the "chumra" one six years on).
"""
from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import get_geo
from .yidcal_lib.halacha_events import (
    PRUZBOL_FOOTNOTE_HE,
    hebrew_year_letters,
    pruzbol_kind,
    pruzbol_note,
    pruzbol_shmita_year,
)
from .yidcal_lib.zman_compute import (
    dawn_for_date,
    round_half_up as _round_half_up,
    sunset_for_date,
)

_LOGGER = logging.getLogger(__name__)

# 29 Elul (pyluach month 6) is always the last day of the Hebrew year — i.e.
# Erev Rosh Hashana of the year that follows. The same coordinates
# pruzbol_kind() checks; named here only so the look-ahead can build candidates.
_ELUL = 6
_EREV_RH_DAY = 29

# Worst case the search has to cover: standing just past a "required" day, the
# next occurrence is the "chumra" one six years later. Eight is comfortable.
_SCAN_YEARS = 8

# The luach appends a '*' to the נוסח as the marker tying it to the 'פרוזבול*'
# note. A sensor attribute has no footnote to tie, so it's dropped.
NUSACH_HE = PRUZBOL_FOOTNOTE_HE.lstrip("*").strip()


class _PruzbolBase(YidCalSpecialDevice):
    """Shared base: the next pruzbol occurrence and its alos → candle window."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle = candle_offset

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

        self._geo = None
        self._added = False
        self._attrs: dict = {}

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._added = True

        # Shared geo (same engine as the Date / Zman sensors)
        self._geo = await get_geo(self.hass)

        # Immediate first calculation
        await self._update_state()

        # Wall-clock minute scheduling (house convention — survives clock steps
        # on the test rig). The window edges land on the first :00 tick after
        # the true alos / candle-lighting instant.
        self._register_listener(
            async_track_time_change(self.hass, self._schedule_update, second=0)
        )
        self._register_interval(
            self.hass, self._schedule_update, timedelta(minutes=1)
        )

    # ── Window math ──────────────────────────────────────────────────────

    def _window(self, d: date_cls) -> tuple[datetime, datetime]:
        """(alos, candle-lighting) on civil date ``d`` — the identical cuts
        binary_sensor.yidcal_erev uses: alos = RAW sunrise − 72 (the house
        definition, via dawn_for_date), candle = sunset − candle_offset, both
        rounded half-up.
        """
        start = _round_half_up(
            dawn_for_date(geo=self._geo, tz=self._tz, base_date=d)
        )
        end = _round_half_up(
            sunset_for_date(geo=self._geo, tz=self._tz, base_date=d)
            - timedelta(minutes=self._candle)
        )
        return start, end

    def _compute(self, now_local: datetime) -> dict:
        """The pruzbol occurrence in view: the next Erev Rosh Hashana that
        brackets a shmita year and whose candle-lighting deadline has not yet
        passed.

        Walks YEAR BY YEAR, never by whole cycles — the two occurrences are in
        consecutive years (chumra, then required), so a 7-year stride would
        step straight over the first of them.
        """
        hy = PHebrewDate.from_pydate(now_local.date()).year

        for _ in range(_SCAN_YEARS):
            d = PHebrewDate(hy, _ELUL, _EREV_RH_DAY).to_pydate()
            kind = pruzbol_kind(d)          # ← the single source of truth
            if kind:
                start, end = self._window(d)
                if now_local < end:         # deadline still ahead
                    incoming = hy + 1       # the year Rosh Hashana brings in
                    shmita = pruzbol_shmita_year(d)
                    return {
                        "kind": kind,                   # 'chumra' | 'required'
                        "entering": kind == "chumra",
                        "shmita_year": shmita,          # year bracketed (same
                                                        # for BOTH occurrences)
                        "hebrew_year": incoming,        # the year the line names
                        "letters": hebrew_year_letters(incoming),
                        "line": pruzbol_note(incoming, kind),
                        "date": d,
                        "start": start,
                        "end": end,
                        "in_window": start <= now_local < end,
                        "days_until": (d - now_local.date()).days,
                    }
            hy += 1

        raise RuntimeError("no upcoming pruzbol occurrence found")

    # ── Entity plumbing ──────────────────────────────────────────────────

    async def _update_state(self) -> None:
        if not self._geo:
            return
        now_local = dt_util.now().astimezone(self._tz)
        try:
            cyc = self._compute(now_local)
        except Exception as e:  # noqa: BLE001 — never kill the tick loop
            _LOGGER.error("Pruzbol update failed: %s", e)
            return
        self._recompute(cyc, now_local)
        if self._added:
            self.async_write_ha_state()

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        raise NotImplementedError

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self._attrs)


class PruzbolSensor(_PruzbolBase, BinarySensorEntity):
    """ON from alos until candle-lighting on an Erev RH bracketing shmita."""

    _attr_name = "Pruzbol"
    _attr_icon = "mdi:file-document-edit-outline"

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__(hass, candle_offset)
        slug = "pruzbol"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_is_on = False

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        self._attr_is_on = cyc["in_window"]
        self._attrs = {
            "Now": now_local.isoformat(),
            "Pruzbol_Date": cyc["date"].isoformat(),
            # 'chumra'   → entering shmita (יש מחמירים … לכתחלה)
            # 'required' → leaving  shmita (צריכין)
            "Pruzbol_Kind": cyc["kind"],
            # House convention: boolean ATTRIBUTES as strings, so HA state
            # conditions can match them directly.
            "Entering_Shmita": "true" if cyc["entering"] else "false",
            "Shmita_Year": cyc["shmita_year"],
            "Hebrew_Year": cyc["hebrew_year"],
            "Hebrew_Year_Letters": cyc["letters"],
            "Line": cyc["line"],
            "Days_Until": cyc["days_until"],
            "Next_Window_Start": cyc["start"].isoformat(),
            "Next_Window_End": cyc["end"].isoformat(),
            "Activation_Logic": (
                "ON from alos until candle-lighting on an Erev Rosh Hashana "
                "(29 Elul) that brackets a Shmita year — the year ENTERING it "
                "(יש מחמירים, לכתחלה) and the year LEAVING it (צריכין). "
                "OFF otherwise."
            ),
        }


class PruzbolDisplaySensor(_PruzbolBase, SensorEntity):
    """Printed-luach Hebrew line for the next pruzbol; always populated."""

    _attr_name = "Pruzbol Display"
    _attr_icon = "mdi:script-text-outline"

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__(hass, candle_offset)
        slug = "pruzbol_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value: str | None = None

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        self._attr_native_value = cyc["line"]
        self._attrs = {
            "Pruzbol_Date": cyc["date"].isoformat(),
            "Pruzbol_Kind": cyc["kind"],
            "Entering_Shmita": "true" if cyc["entering"] else "false",
            "Shmita_Year": cyc["shmita_year"],
            "Hebrew_Year": cyc["hebrew_year"],
            "Hebrew_Year_Letters": cyc["letters"],
            "Days_Until": cyc["days_until"],
            "Window_Start": cyc["start"].isoformat(),
            "Window_End": cyc["end"].isoformat(),
            # House convention: boolean ATTRIBUTES as strings for HA
            # state-condition matching.
            "Is_Today": "true" if cyc["date"] == now_local.date() else "false",
            "In_Window": "true" if cyc["in_window"] else "false",
            "Nusach": NUSACH_HE,
        }
