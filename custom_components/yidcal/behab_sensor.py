# custom_components/yidcal/behab_sensor.py
"""
Binary sensor: בה"ב (BeHaB)

ON during each of the three BeHaB fasts — Monday, Thursday, Monday after
Rosh Chodesh Cheshvan and Rosh Chodesh Iyar — from alos HaShachar until
tzeis, like the other minor fasts.

Dates come from the single source of truth, ``halacha_events.
compute_behab_cycle`` (the same rule the luach uses): מברכין בה"ב is the
first Shabbos of the month that is not Rosh Chodesh, and the fasts are
+2, +5 and +9 days after it.

Anchors:
  • Start — alos HaShachar (sunrise − 72 min), FLOORED — the fast-start
    anchor (zman_compute.fast_start_for_date).
  • End   — tzeis (sunset + the user's havdalah offset), ceil-rounded.

Attributes:
  Fast_Today                    – תענית שני קמא / תענית חמישי /
                                  תענית שני תנינא, or "" on other days
  Window_Start / Window_End     – today's fast window (or the next one)
  Next_Fast_Date / Next_Fast_Label
  Mevorchim_Shabbos             – Shabbos מברכין בה"ב of the current /
                                  upcoming cycle
  Is_Mevorchim_Shabbos_Today    – True on that Shabbos
  Fast_Mon_1 / Fast_Thu / Fast_Mon_2 – the cycle's three dates
  Season                        – Cheshvan or Iyar
  Activation_Logic              – human-readable description
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import (
    FAST_START_ALOS,
    fast_start_for_date,
    round_ceil as _round_ceil,
    sunset_for_date,
)
from .zman_sensors import get_geo

# Labels, in fast order — as the KJ yearly luach prints them.
_LABELS = ("תענית שני קמא", "תענית חמישי", "תענית שני תנינא")
_SEASON_NAME = {"cheshvan": "Cheshvan", "iyar": "Iyar"}


class BehabSensor(YidCalSpecialDevice, BinarySensorEntity):
    """ON from alos to tzeis on each BeHaB fast day."""

    _attr_name = "BeHaB"
    _attr_icon = "mdi:food-off"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "behab"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._havdalah = int(cfg.get("havdalah_offset", 72))

        self._geo = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # Every minute on the :00 second, like the other YidCal sensors.
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

    # ── helpers ──

    def _window(self, d: date) -> tuple[datetime, datetime]:
        """Fast window for ``d``: floored alos → ceil tzeis."""
        start = fast_start_for_date(
            geo=self._geo, tz=self._tz, base_date=d, anchor=FAST_START_ALOS,
        )
        end = _round_ceil(
            sunset_for_date(geo=self._geo, tz=self._tz, base_date=d)
            + timedelta(minutes=self._havdalah)
        )
        return start, end

    @staticmethod
    def _current_cycle(today: date) -> he.BehabCycle | None:
        """The cycle whose last fast is today or later (current / upcoming)."""
        cycles = he.compute_behab_in_range(
            start=today - timedelta(days=9), end=today + timedelta(days=400),
        )
        for cyc in cycles:
            if cyc.fast_mon_2 >= today:
                return cyc
        return None

    # ── main update ──

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        cyc = self._current_cycle(today)

        fast_today = ""
        is_on = False
        win_start = win_end = None
        next_date = None
        next_label = ""

        if cyc is not None:
            for d, label in zip(cyc.fasts, _LABELS):
                if d == today:
                    fast_today = label
                    win_start, win_end = self._window(d)
                    is_on = win_start <= now_local < win_end
                # Next fast whose window hasn't ended yet.
                if next_date is None:
                    _, end = self._window(d)
                    if now_local < end:
                        next_date, next_label = d, label
            if win_start is None and next_date is not None:
                win_start, win_end = self._window(next_date)

        self._attr_is_on = is_on
        self._attr_extra_state_attributes = {
            "Fast_Today": fast_today,
            "Window_Start": win_start.isoformat() if win_start else "",
            "Window_End": win_end.isoformat() if win_end else "",
            "Next_Fast_Date": next_date.isoformat() if next_date else "",
            "Next_Fast_Label": next_label,
            "Mevorchim_Shabbos": cyc.mevorchim_shabbos.isoformat() if cyc else "",
            "Is_Mevorchim_Shabbos_Today": bool(cyc and cyc.mevorchim_shabbos == today),
            "Fast_Mon_1": cyc.fast_mon_1.isoformat() if cyc else "",
            "Fast_Thu": cyc.fast_thu.isoformat() if cyc else "",
            "Fast_Mon_2": cyc.fast_mon_2.isoformat() if cyc else "",
            "Season": _SEASON_NAME.get(cyc.season, "") if cyc else "",
            "Activation_Logic": (
                "ON from alos HaShachar until tzeis (sunset + havdalah offset) "
                "on each BeHaB fast — Monday, Thursday, Monday after the first "
                "Shabbos of Cheshvan / Iyar that is not Rosh Chodesh "
                "(Shabbos מברכין בה\"ב). OFF all other times."
            ),
        }

        if self._added:
            self.async_write_ha_state()
