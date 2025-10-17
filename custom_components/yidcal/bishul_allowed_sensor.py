from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class BishulAllowedSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    Bishul Allowed

    ON every halachic day (including Yom Tov) from:
        sunset(prev civil day) - candle_offset  →  sunset(today) + havdalah_offset

    EXCEPT:
      • Shabbos (Saturday) — OFF for the whole halachic day.
      • Yom Kippur — OFF for the whole halachic day.

    Attributes: Now, Next_Off_Window_Start, Next_Off_Window_End
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
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ----- helpers -----

    def _sunset(self, d) -> datetime:
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _is_yom_kippur(self, d) -> bool:
        name = PHebrewDate.from_pydate(d).holiday(hebrew=True, prefix_day=False) or ""
        return "יום הכיפורים" in name

    def _window_for_halachic_day(self, d):
        """
        For halachic day 'd' (evening→evening), return (start_dt, end_dt).
        start = sunset(d-1) - candle_offset
        end   = sunset(d) + havdalah_offset
        """
        start_dt = self._sunset(d - timedelta(days=1)) - timedelta(minutes=self._candle)
        end_dt   = self._sunset(d) + timedelta(minutes=self._havdalah)
        return start_dt, end_dt

    def _find_current_halachic_day(self, now_local: datetime):
        """
        Find halachic day d such that window_for(d) contains now_local.
        """
        base = now_local.date()
        for delta in (0, -1, 1, -2, 2):
            d = base + timedelta(days=delta)
            s, e = self._window_for_halachic_day(d)
            if s <= now_local < e:
                return d, s, e
        # Fallback to today
        d = base
        s, e = self._window_for_halachic_day(d)
        return d, s, e

    def _next_off_window_after(self, ref_local: datetime):
        """
        Next OFF window = next Shabbos or Yom Kippur halachic day
        (candle(before) → havdalah(after)).
        If currently inside such a window, returns the current one.
        """
        for i in range(-2, 90):
            d = ref_local.date() + timedelta(days=i)
            is_off_day = (d.weekday() == 5) or self._is_yom_kippur(d)
            if not is_off_day:
                continue
            s, e = self._window_for_halachic_day(d)
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

        # ON unless Shabbos or Yom Kippur
        is_shabbos = (d.weekday() == 5)
        is_yk = self._is_yom_kippur(d)
        self._attr_is_on = (not is_shabbos) and (not is_yk) and (s <= now < e)

        # Next OFF window
        no_start, no_end = self._next_off_window_after(now)
        next_off_start = _round_half_up(no_start) if no_start else None
        next_off_end   = _round_ceil(no_end) if no_end else None

        # Attributes (publish consistently)
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Next_Off_Window_Start": next_off_start.isoformat() if next_off_start else "",
            "Next_Off_Window_End": next_off_end.isoformat() if next_off_end else "",
            "Activation_Logic": "Usually ON; Turns OFF on Shabbos and Yom Kippur from Candle lighting till Havdalah.",
        }
