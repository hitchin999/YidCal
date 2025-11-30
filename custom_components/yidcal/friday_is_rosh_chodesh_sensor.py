from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import homeassistant.util.dt as dt_util
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_state_change_event,
    async_track_sunset,
)
from homeassistant.helpers.restore_state import RestoreEntity

from zmanim.zmanim_calendar import ZmanimCalendar

from .device import YidCalDisplayDevice
from .yidcal_lib.helper import YidCalHelper
from .const import DOMAIN
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)

NAILS_TEXT = "שניידן די נעגל, האר היינט לכבוד שבת"
STATE_OPTIONS = [NAILS_TEXT, ""]


def _round_half_up(dt: datetime) -> datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    """Always bump to the next minute (Motzi-style)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class FridayIsRoshChodeshSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Reminder to cut nails early when Friday is (or is part of) Rosh Chodesh."""

    _attr_icon = "mdi:content-cut"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_name = "Friday Is Rosh Chodesh"

    def __init__(
        self,
        hass: HomeAssistant,
        helper: YidCalHelper,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "friday_is_rosh_chodesh"
        self.hass = hass
        self.helper = helper
        self._havdalah_offset = havdalah_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])

        self._geo = None  # filled in async_added_to_hass

        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value = ""

        self._attr_extra_state_attributes = {
            "Possible states": STATE_OPTIONS,
            "Activation_Logic": (
                "Shows on Thu if upcoming Rosh Chodesh is Fri (1 day) "
                "or Fri/Sat (2 days). Shows on Wed if upcoming Rosh Chodesh "
                "is Thu/Fri (2 days). Excludes Tishrei except the Elul→Tishrei overlap."
            ),
        }

    @property
    def options(self) -> list[str]:
        return STATE_OPTIONS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Grab shared geo (same as BishulAllowed)
        self._geo = await get_geo(self.hass)

        # Restore last value if valid
        last = await self.async_get_last_state()
        if last and last.state in STATE_OPTIONS:
            self._attr_native_value = last.state

        await self.async_update()

        # Polling + key flips
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=30))
        async_track_state_change_event(
            self.hass,
            ["sensor.yidcal_molad"],
            self._handle_molad_change,
        )
        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )

    async def _handle_molad_change(self, event) -> None:
        await self.async_update()

    # ---------------- helpers ----------------

    def _sunset(self, d: date) -> datetime:
        """Zmanim sunset using shared geo."""
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _alos(self, d: date) -> datetime:
        """
        Get Alos via zmanim calendar.
        Fallback to sunrise-72 via zmanim (no Astral).
        """
        cal = ZmanimCalendar(geo_location=self._geo, date=d)

        # Try multiple common method names safely
        for name in ("alos_72", "alos72", "alos"):
            fn = getattr(cal, name, None)
            if callable(fn):
                try:
                    return fn().astimezone(self._tz)
                except Exception:
                    pass

        # Fallback: zmanim sunrise - 72
        sunrise = cal.sunrise().astimezone(self._tz)
        return sunrise - timedelta(minutes=72)

    def _get_upcoming_rosh_chodesh_gdays(
        self, today: date
    ) -> tuple[list[date], int, int]:
        """
        Return (gdays, heb_year, heb_month) for upcoming Rosh Chodesh (next Hebrew month).
        Uses pyluach to compute if RC is 1 or 2 Gregorian days.
        """
        nxt = self.helper.get_next_numeric_month_year(today)
        year = int(nxt["year"])
        month = int(nxt["month"])

        first_day = PHebrewDate(year, month, 1).to_pydate()
        prev_day = first_day - timedelta(days=1)

        prev_hd = PHebrewDate.from_pydate(prev_day)
        if prev_hd.day == 30:
            return [prev_day, first_day], year, month

        return [first_day], year, month

    def _compute_reminder_day(
        self, rc_gdays: list[date], rc_month: int
    ) -> date | None:
        """Determine which Gregorian day should show the reminder, or None."""

        # Exclude "pure Tishrei" RC,
        # but allow Elul→Tishrei overlap (30 Elul + 1 Tishrei).
        if rc_month == 7:
            span_months = {PHebrewDate.from_pydate(d).month for d in rc_gdays}
            if span_months == {7}:
                return None

        wds = [d.weekday() for d in rc_gdays]  # Mon=0..Sun=6

        # 1-day RC on Friday -> show Thursday
        if len(rc_gdays) == 1 and wds[0] == 4:
            return rc_gdays[0] - timedelta(days=1)

        # 2-day patterns
        if len(rc_gdays) == 2:
            # Fri/Sat RC -> show Thursday
            if wds[0] == 4 and wds[1] == 5:
                return rc_gdays[0] - timedelta(days=1)
            # Thu/Fri RC -> show Wednesday
            if wds[0] == 3 and wds[1] == 4:
                return rc_gdays[0] - timedelta(days=1)

        return None

    def _window_for_day(self, d: date) -> tuple[datetime, datetime]:
        """Return (rounded_start, rounded_end) for reminder day d."""
        ws_raw = self._alos(d)
        we_raw = self._sunset(d) + timedelta(minutes=self._havdalah_offset)
        return _round_half_up(ws_raw), _round_ceil(we_raw)

    def _next_window_after(self, ref_local: datetime):
        """
        Find the next reminder window (or current one if we're inside it).
        Scans forward ~½ year.
        Returns (start_dt, end_dt, reminder_date) rounded.
        """
        ref_date = ref_local.date()

        for i in range(0, 184):  # ~half year
            d = ref_date + timedelta(days=i)

            try:
                rc_gdays, _rc_year, rc_month = self._get_upcoming_rosh_chodesh_gdays(d)
                reminder_day = self._compute_reminder_day(rc_gdays, rc_month)
            except Exception:
                continue

            if reminder_day != d:
                continue

            ws, we = self._window_for_day(d)

            # If this window isn't fully in the past, it's the next one (or current)
            if we > ref_local:
                return ws, we, d

        return None, None, None

    # ---------------- main ----------------

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        try:
            rc_gdays, rc_year, rc_month = self._get_upcoming_rosh_chodesh_gdays(today)
            reminder_day = self._compute_reminder_day(rc_gdays, rc_month)
        except Exception as e:
            _LOGGER.error("FridayIsRoshChodesh update failed: %s", e)
            self._attr_native_value = ""
            return

        window_start = None
        window_end = None
        active = False

        # Only show current Window_* if TODAY is the reminder day
        if reminder_day == today:
            window_start, window_end = self._window_for_day(today)
            active = window_start <= now_local < window_end

        self._attr_native_value = NAILS_TEXT if active else ""

        # Next window (includes current if active)
        next_ws, next_we, next_day = self._next_window_after(now_local)

        self._attr_extra_state_attributes.update(
            {
                "Window_Start": window_start.isoformat() if window_start else "",
                "Window_End": window_end.isoformat() if window_end else "",

                "Next_Window_Start": next_ws.isoformat() if next_ws else "",
                "Next_Window_End": next_we.isoformat() if next_we else "",
                #"Next_Reminder_Day": next_day.isoformat() if next_day else "",

                "Active_Window_Today": active,
            }
        )

