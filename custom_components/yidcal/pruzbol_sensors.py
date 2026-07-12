"""
custom_components/yidcal/pruzbol_sensors.py

Pruzbol (פרוזבול) sensors, both grouped under the YidCal — Special Sensors
device:

  binary_sensor.yidcal_pruzbol      ON from alos until candle-lighting on the
                                    Erev Rosh Hashana that CLOSES a Shmita year
                                    (29 Elul of shmita cycle-year 7) — the same
                                    alos → candle-lighting window convention as
                                    binary_sensor.yidcal_erev.
  sensor.yidcal_pruzbol_display     ALWAYS shows the printed-luach Hebrew line
                                    for the next pruzbol, e.g.
                                    "בער״ה תש״צ צריכין לעשות פרוזבול".
                                    The year is derived, never hard-coded.

The date rule is NOT re-derived here. ``halacha_events.needs_pruzbol()`` is the
single source of truth — the yearly luach's note and its נוסח footnote read the
same predicate — and ``halacha_events.next_shmita_year()`` only *proposes* the
candidate year, which is then validated against it. The Hebrew line reuses
``halacha_events.hebrew_year_letters()`` and the נוסח reuses
``luach_data.PRUZBOL_FOOTNOTE_HE``, so these sensors and the printed sheet can
never disagree.

ROLLOVER: the occurrence "in view" advances the moment its deadline passes —
i.e. at candle-lighting on that Erev Rosh Hashana, in lock-step with the binary
sensor going OFF. Both sensors then point at the occurrence seven years later.
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
    hebrew_year_letters,
    needs_pruzbol,
    next_shmita_year,
)
from .yidcal_lib.luach_data import PRUZBOL_FOOTNOTE_HE
from .yidcal_lib.zman_compute import (
    dawn_for_date,
    round_half_up as _round_half_up,
    sunset_for_date,
)

_LOGGER = logging.getLogger(__name__)

# 29 Elul (pyluach month 6) is always the last day of the Hebrew year — i.e.
# Erev Rosh Hashana of the year that follows. Same coordinates needs_pruzbol()
# checks; named here only so the look-ahead can construct the candidate date.
_ELUL = 6
_EREV_RH_DAY = 29

# One shmita cycle — the gap between consecutive pruzbol occurrences.
_CYCLE_YEARS = 7

# The luach prints a leading '*' on the footnote as the marker that ties it to
# the 'פרוזבול*' note. A sensor attribute has no footnote to tie, so it's dropped.
NUSACH_HE = PRUZBOL_FOOTNOTE_HE.lstrip("*").strip()


def pruzbol_line(hebrew_year: int) -> str:
    """The printed-luach line, e.g. 'בער״ה תש״צ צריכין לעשות פרוזבול'.

    ``hebrew_year`` is the INCOMING year (the one Rosh Hashana brings in) —
    identical to ``luach_data._build_pruzbol_annotations``' ``ph.year + 1``.
    The luach appends a trailing '*' as the footnote marker; a sensor has no
    footnote, so it is dropped here.
    """
    return f"בער״ה {hebrew_year_letters(hebrew_year)} צריכין לעשות פרוזבול"


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
        """The pruzbol occurrence in view: the next Erev RH closing a shmita
        year whose candle-lighting deadline has not yet passed."""
        hy = next_shmita_year(PHebrewDate.from_pydate(now_local.date()).year)

        for _ in range(4):                      # ≤1 step ever needed in practice
            d = PHebrewDate(hy, _ELUL, _EREV_RH_DAY).to_pydate()
            # next_shmita_year() only PROPOSES; halacha_events.needs_pruzbol()
            # remains the single source of truth for the rule itself.
            if needs_pruzbol(d):
                start, end = self._window(d)
                if now_local < end:             # deadline still ahead
                    return {
                        "shmita_year": hy,          # the year being closed
                        "hebrew_year": hy + 1,      # the year the line names
                        "letters": hebrew_year_letters(hy + 1),
                        "line": pruzbol_line(hy + 1),
                        "date": d,
                        "start": start,
                        "end": end,
                        "in_window": start <= now_local < end,
                        "days_until": (d - now_local.date()).days,
                    }
            hy += _CYCLE_YEARS

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
    """ON from alos until candle-lighting on the Erev RH closing a shmita year."""

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
            "Hebrew_Year": cyc["hebrew_year"],
            "Hebrew_Year_Letters": cyc["letters"],
            "Shmita_Year": cyc["shmita_year"],
            "Days_Until": cyc["days_until"],
            "Next_Window_Start": cyc["start"].isoformat(),
            "Next_Window_End": cyc["end"].isoformat(),
            "Activation_Logic": (
                "ON from alos until candle-lighting on the Erev Rosh Hashana "
                "that closes a Shmita year (29 Elul). OFF otherwise."
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
            "Hebrew_Year": cyc["hebrew_year"],
            "Hebrew_Year_Letters": cyc["letters"],
            "Shmita_Year": cyc["shmita_year"],
            "Days_Until": cyc["days_until"],
            "Window_Start": cyc["start"].isoformat(),
            "Window_End": cyc["end"].isoformat(),
            # House convention: boolean ATTRIBUTES as strings for HA
            # state-condition matching.
            "Is_Today": "true" if cyc["date"] == now_local.date() else "false",
            "In_Window": "true" if cyc["in_window"] else "false",
            "Nusach": NUSACH_HE,
        }
