from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    """Always bump to the *next* minute (Motzi-style)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class BishulAllowedSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    Bishul Allowed

    ON every halachic day (including Yom Tov) from:
        sunset(prev civil day) - candle_offset  →  sunset(today) + havdalah_offset

    EXCEPT:
      • Shabbos (Saturday) — OFF the whole halachic day.
      • Yom Kippur — OFF the whole halachic day.

    Attributes: Now, Next_Off_Window_Start, Next_Off_Window_End, Activation_Logic
    """
    _attr_name = "Bishul Allowed"
    _attr_icon = "mdi:pot-steam"
    _attr_unique_id = "yidcal_bishul_allowed"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_bishul_allowed"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

        # Tick exactly on the minute so OFF/ON flips match rounded Motzi times
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ----- helpers -----

    def _sunset(self, d) -> datetime:
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _is_yom_kippur(self, d) -> bool:
        """Return True if halachic day 'd' is 10 Tishrei (Yom Kippur)."""
        hd = PHebrewDate.from_pydate(d)
        return (hd.month == 7 and hd.day == 10)

    def _window_for_halachic_day(self, d):
        """
        For halachic day 'd' (evening→evening), return (start_dt, end_dt) *raw*.
        start = sunset(d-1) - candle_offset
        end   = sunset(d)   + havdalah_offset
        """
        start_dt = self._sunset(d - timedelta(days=1)) - timedelta(minutes=self._candle)
        end_dt   = self._sunset(d) + timedelta(minutes=self._havdalah)
        return start_dt, end_dt

    def _find_current_halachic_day(self, now_local: datetime):
        """
        Find halachic day d such that window_for(d) contains now_local.
        Uses the *rounded* window so we don't flip early compared to other sensors.
        """
        base = now_local.date()

        # Rounded candle cutoff for today's civil date
        today_candle_raw = self._sunset(base) - timedelta(minutes=self._candle)
        today_candle = _round_half_up(today_candle_raw)

        if now_local >= today_candle:
            first_try, second_try = base + timedelta(days=1), base
        else:
            first_try, second_try = base, base + timedelta(days=1)

        trial_order = [
            first_try,
            second_try,
            base - timedelta(days=1),
            base + timedelta(days=2),
            base - timedelta(days=2),
        ]

        for d in trial_order:
            s_raw, e_raw = self._window_for_halachic_day(d)
            s = _round_half_up(s_raw)
            e = _round_ceil(e_raw)
            if s <= now_local < e:
                return d, s_raw, e_raw

        # Fallback to today if nothing matched (extremely rare)
        d = base
        s_raw, e_raw = self._window_for_halachic_day(d)
        return d, s_raw, e_raw

    def _in_shabbos_off_window(self, now_local: datetime) -> bool:
        """
        True if now is between Fri candle → Sat havdalah for this or
        adjacent week, using the same rounded boundaries as the main sensors.
        """
        base = now_local.date()
        wd = base.weekday()  # Mon=0..Sun=6
        friday0 = base - timedelta(days=(wd - 4) % 7)

        for week_shift in (-7, 0, 7):
            f = friday0 + timedelta(days=week_shift)
            s_raw = self._sunset(f) - timedelta(minutes=self._candle)
            e_raw = self._sunset(f + timedelta(days=1)) + timedelta(minutes=self._havdalah)

            s = _round_half_up(s_raw)
            e = _round_ceil(e_raw)

            if s <= now_local < e:
                return True

        return False

    def _in_yk_off_window(self, now_local: datetime) -> bool:
        """True if now is inside the Yom Kippur OFF window (candle → havdalah)."""
        base = now_local.date()
        for i in range(-3, 4):
            d = base + timedelta(days=i)
            if not self._is_yom_kippur(d):
                continue

            # For the halachic day 'd' (10 Tishrei), OFF spans:
            # candle(before) → havdalah(after), with rounded boundaries.
            s_raw, e_raw = self._window_for_halachic_day(d)
            s = _round_half_up(s_raw)
            e = _round_ceil(e_raw)

            if s <= now_local < e:
                return True

        return False

    def _next_off_window_after(self, ref_local: datetime):
        """
        Next OFF window = next Shabbos or Yom Kippur halachic day
        (candle(before) → havdalah(after)), using rounded boundaries.
        If currently inside such a window, returns the current one.
        """
        for i in range(-2, 90):
            d = ref_local.date() + timedelta(days=i)
            is_off_day = (d.weekday() == 5) or self._is_yom_kippur(d)
            if not is_off_day:
                continue

            s_raw, e_raw = self._window_for_halachic_day(d)
            s = _round_half_up(s_raw)
            e = _round_ceil(e_raw)

            if e <= ref_local:
                continue

            return s, e

        return None, None

    # ----- main -----

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        # Current halachic day
        d, s_raw, e_raw = self._find_current_halachic_day(now)
        s = _round_half_up(s_raw)
        e = _round_ceil(e_raw)

        # ON unless we're inside a real OFF window (Shabbos or Yom Kippur)
        in_shabbos_off = self._in_shabbos_off_window(now)
        in_yk_off = self._in_yk_off_window(now)
        self._attr_is_on = (not in_shabbos_off) and (not in_yk_off) and (s <= now < e)

        # Next OFF window (Shabbos or YK)
        no_start, no_end = self._next_off_window_after(now)
        next_off_start = _round_half_up(no_start) if no_start else None
        next_off_end   = _round_ceil(no_end) if no_end else None

        # Attributes
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Next_Off_Window_Start": next_off_start.isoformat() if next_off_start else "",
            "Next_Off_Window_End": next_off_end.isoformat() if next_off_end else "",
            "Activation_Logic": (
                "Usually ON; turns OFF on Shabbos and Yom Kippur from candle-lighting "
                "until havdalah (using rounded Zman boundaries)."
            ),
        }
