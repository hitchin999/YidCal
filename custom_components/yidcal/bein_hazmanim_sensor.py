# /config/custom_components/yidcal/bein_hazmanim_sensor.py
"""
Binary sensor for "Bein Hazmanim" (Yeshiva vacation periods):

Two spans per annual cycle, all civil‑midnight boundaries:
  1. Nissan span  – 12:00 AM on 1 Nissan → 12:00 AM on 1 Iyar
     (30 Nissan / RC Iyar day 1 is the last full day included.)
  2. Tishrei span – 12:00 AM on Friday before Alef Slichos → 12:00 AM on 1 Cheshvan
     (30 Tishrei / RC Cheshvan day 1 is the last full day included.)

Attributes:
  Now:                  ISO current local time
  Next_Window_Start:    ISO start of current/upcoming bein hazmanim window
  Next_Window_End:      ISO end of current/upcoming bein hazmanim window
  Nissan_Span_Start:    ISO start of the current/upcoming Nissan span
  Nissan_Span_End:      ISO end of the current/upcoming Nissan span
  Tishrei_Span_Start:   ISO start of the current/upcoming Tishrei span
  Tishrei_Span_End:     ISO end of the current/upcoming Tishrei span
  בין_הזמנים_פסח:       "true" / "false" — currently in Nissan span
  בין_הזמנים_סוכות:      "true" / "false" — currently in Tishrei span
  Activation_Logic:     concise ON/OFF rules
"""

from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalSpecialDevice
from .const import DOMAIN


class BeinHazmonimSensor(YidCalSpecialDevice, BinarySensorEntity):
    _attr_name = "Bein Hazmanim"
    _attr_icon = "mdi:calendar-check"

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__()
        slug = "bein_hazmanim"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])

        # caches
        self._now_local: Optional[datetime] = None
        self._next_window_start: Optional[datetime] = None
        self._next_window_end: Optional[datetime] = None
        self._nissan_span: Optional[Tuple[datetime, datetime]] = None
        self._tishrei_span: Optional[Tuple[datetime, datetime]] = None
        self._active_label: Optional[str] = None  # "nissan" | "tishrei" | None

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ------------- midnight helper -------------
    def _midnight(self, gdate: date) -> datetime:
        """Return 12:00 AM local on the given civil date."""
        return datetime(gdate.year, gdate.month, gdate.day, tzinfo=self._tz)

    # ------------- span computation -------------
    def _compute_nissan_span(self, heb_year: int) -> Tuple[datetime, datetime]:
        """
        12:00 AM on 1 Nissan  →  12:00 AM on 1 Iyar.
        (30 Nissan / 1st day RC Iyar is the last full day.)
        """
        nissan_1_greg = PHebrewDate(heb_year, 1, 1).to_pydate()
        iyar_1_greg = PHebrewDate(heb_year, 2, 1).to_pydate()
        return (self._midnight(nissan_1_greg), self._midnight(iyar_1_greg))

    def _compute_tishrei_span(self, tishrei_year: int) -> Tuple[datetime, datetime]:
        """
        12:00 AM on Friday before Alef Slichos  →  12:00 AM on 1 Cheshvan.
        (30 Tishrei / 1st day RC Cheshvan is the last full day.)
        """
        tishrei_1_greg = PHebrewDate(tishrei_year, 7, 1).to_pydate()

        # ── Alef Slichos Shabbos (same algorithm as slichos_sensor.py) ──
        rh_wd = tishrei_1_greg.weekday()  # Mon=0 … Sun=6
        pre_rh = tishrei_1_greg - timedelta(days=1)
        alef_shabbos = pre_rh - timedelta(days=((pre_rh.weekday() - 5) % 7))
        if rh_wd in (0, 1):  # Mon or Tue R"H → one week earlier
            alef_shabbos -= timedelta(days=7)

        # Friday before that Shabbos
        friday_before = alef_shabbos - timedelta(days=1)

        # ── End: 12 AM on 1 Cheshvan (2nd day of RC Cheshvan) ──
        cheshvan_1_greg = PHebrewDate(tishrei_year, 8, 1).to_pydate()

        return (self._midnight(friday_before), self._midnight(cheshvan_1_greg))

    # ------------- update -------------
    async def async_update(self, now=None) -> None:
        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        self._now_local = now

        hd = PHebrewDate.from_pydate(now.date())
        year = hd.year

        # Build candidate spans (current year ± 1 to cover all edge cases)
        candidates: list[Tuple[datetime, datetime, str]] = []  # (start, end, label)
        for y in (year - 1, year, year + 1):
            try:
                ns = self._compute_nissan_span(y)
                candidates.append((ns[0], ns[1], "nissan"))
            except Exception:
                pass
            try:
                ts = self._compute_tishrei_span(y)
                candidates.append((ts[0], ts[1], "tishrei"))
            except Exception:
                pass

        # Find if we're currently in a span
        current_span: Optional[Tuple[datetime, datetime, str]] = None
        for start, end, label in candidates:
            if start <= now < end:
                current_span = (start, end, label)
                break

        # Find the next upcoming span (earliest start > now)
        next_span: Optional[Tuple[datetime, datetime, str]] = None
        for start, end, label in sorted(candidates, key=lambda x: x[0]):
            if start > now:
                next_span = (start, end, label)
                break

        # State
        self._attr_is_on = current_span is not None
        self._active_label = current_span[2] if current_span else None

        if current_span:
            self._next_window_start = current_span[0]
            self._next_window_end = current_span[1]
        elif next_span:
            self._next_window_start = next_span[0]
            self._next_window_end = next_span[1]
        else:
            self._next_window_start = None
            self._next_window_end = None

        # Populate the individual span attributes:
        # Show the current/upcoming Nissan and Tishrei spans closest to now.
        self._nissan_span = None
        self._tishrei_span = None

        for start, end, label in sorted(candidates, key=lambda x: x[0]):
            if label == "nissan" and self._nissan_span is None and end > now:
                self._nissan_span = (start, end)
            if label == "tishrei" and self._tishrei_span is None and end > now:
                self._tishrei_span = (start, end)

        if self._added:
            self.async_write_ha_state()

    # ------------- attributes -------------
    def _activation_logic_text(self) -> str:
        return (
            "ON from 12 AM on Rosh Chodesh Nissan until 12 AM on the 2nd day of "
            "Rosh Chodesh Iyar, and from 12 AM on Friday before Alef Slichos until "
            "12 AM on the 2nd day of Rosh Chodesh Cheshvan. OFF otherwise."
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        attrs: dict[str, str | bool] = {}
        if self._now_local:
            attrs["Now"] = self._now_local.isoformat()
        if self._next_window_start:
            attrs["Next_Window_Start"] = self._next_window_start.isoformat()
        if self._next_window_end:
            attrs["Next_Window_End"] = self._next_window_end.isoformat()
        # Individual span attributes
        if self._nissan_span:
            attrs["Nissan_Span_Start"] = self._nissan_span[0].isoformat()
            attrs["Nissan_Span_End"] = self._nissan_span[1].isoformat()
        else:
            attrs["Nissan_Span_Start"] = ""
            attrs["Nissan_Span_End"] = ""
        if self._tishrei_span:
            attrs["Tishrei_Span_Start"] = self._tishrei_span[0].isoformat()
            attrs["Tishrei_Span_End"] = self._tishrei_span[1].isoformat()
        else:
            attrs["Tishrei_Span_Start"] = ""
            attrs["Tishrei_Span_End"] = ""
        # Which span is active (lowercase string booleans for HA conditions)
        attrs["בין_הזמנים_פסח"] = str(self._active_label == "nissan").lower()
        attrs["בין_הזמנים_סוכות"] = str(self._active_label == "tishrei").lower()
        attrs["Activation_Logic"] = self._activation_logic_text()
        return attrs
