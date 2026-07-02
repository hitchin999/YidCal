# /config/custom_components/yidcal/eruv_tavshilin.py
"""
Binary sensor for "Eruv Tavshilin":

- Triggers when the next Yom Tov span includes Friday (so Shabbos follows).
- Window: ON from alos (dawn) on the Eruv day until tzeis that evening
          (tzeis = sunset + havdalah_offset). OFF otherwise.

Attributes:
  Now:                 ISO current local time (local tz)
  Next_Window_Start:   ISO alos on the Eruv day (local tz)
  Next_Window_End:     ISO tzeis that evening (local tz)
  Activation_Logic:    "ON from alos (dawn) on the Eruv day until tzeis that evening. OFF otherwise."
"""

from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from hdate import HDateInfo

from .device import YidCalSpecialDevice
from .yidcal_lib.zman_compute import (
    dawn_for_date,
    round_ceil as _round_ceil,
    round_half_up as _round_half_up,
    sunset_for_date,
)
from .zman_sensors import get_geo
from .const import DOMAIN


class EruvTavshilinSensor(YidCalSpecialDevice, BinarySensorEntity):
    _attr_name = "Eruv Tavshilin"
    _attr_icon = "mdi:food-drumstick"

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        """
        Signature
        Uses:
          - candle:   candle-lighting offset (minutes before sunset)  [candle accepted for parity; not used.]
          - havdalah: havdalah offset (minutes after sunset)          [used for state end]
        """
        super().__init__()
        slug = "eruv_tavshilin"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        self._candle = candle
        self._havdalah = havdalah

        self._geo = None
        # Use integration-normalized tz/flag so we match all other sensors
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora: bool = cfg.get("diaspora", True)

        # caches
        self._now_local: Optional[datetime] = None
        self._next_window_start: Optional[datetime] = None
        self._next_window_end: Optional[datetime] = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        cfg = self.hass.data[DOMAIN]["config"]
        self._diaspora = cfg.get("diaspora", True)
        await self.async_update()
        # Update every minute
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ------------- helpers -------------
    def _alos_local_on(self, gdate) -> datetime:
        """Alos (MGA 72) for gdate: sunrise - 72 minutes, rounded half-up."""
        return _round_half_up(
            dawn_for_date(geo=self._geo, tz=self._tz, base_date=gdate)
        )

    def _tzeis_local_on(self, gdate) -> datetime:
        """Tzeis for gdate: sunset + havdalah offset, rounded ceil."""
        return _round_ceil(
            sunset_for_date(geo=self._geo, tz=self._tz, base_date=gdate)
            + timedelta(minutes=self._havdalah)
        )

    def _yt_span_end(self, start: date) -> date:
        """
        Return the last civil date of the Yom Tov span beginning at `start`.
        In diaspora, treat Shemini Atzeres + Simchas Torah as a continuous span.
        """
        end = start
        while HDateInfo(end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            end += timedelta(days=1)

        if self._diaspora:
            name_end  = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
            name_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(hebrew=True, prefix_day=False)
            if name_end == "שמיני עצרת" and name_next == "שמחת תורה":
                end = end + timedelta(days=1)
        return end

    def _span_includes_friday(self, start, end) -> bool:
        days = (end - start).days + 1
        for k in range(days):
            if (start + timedelta(days=k)).weekday() == 4:  # Friday
                return True
        return False

    def _find_next_window_after(self, ref: datetime) -> Optional[Tuple[datetime, datetime]]:
        """
        Find the (current or next) Eruv Tavshilin window whose END is after `ref`.
        Returns (win_start_dt_local, win_end_dt_local).
        - win_start = alos of the Eruv day
        - win_end   = tzeis of the Eruv day
        """
        base_date = ref.date()
        for i in range(0, 400):  # scan forward comfortably through the year
            d = base_date + timedelta(days=i)
            hd = HDateInfo(d, diaspora=self._diaspora)
            hd_prev = HDateInfo(d - timedelta(days=1), diaspora=self._diaspora)

            if hd.is_yom_tov and not hd_prev.is_yom_tov:
                span_start = d
                span_end = self._yt_span_end(span_start)
                if not self._span_includes_friday(span_start, span_end):
                    continue

                eruv_day = span_start - timedelta(days=1)
                win_start = self._alos_local_on(eruv_day)
                win_end = self._tzeis_local_on(eruv_day)

                if win_end <= ref:
                    continue
                return (win_start, win_end)

        return None

    def _activation_logic_text(self) -> str:
        return "ON from alos (dawn) on the Eruv day until tzeis that evening. OFF otherwise."

    # ------------- update -------------
    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        self._now_local = now

        window = self._find_next_window_after(now)
        if not window:
            # Nothing found in horizon: publish OFF and empty Next_*
            self._attr_is_on = False
            self._next_window_start = None
            self._next_window_end = None
            if self._added:
                self.async_write_ha_state()
            return

        win_start, win_end = window
        in_window = (win_start <= now < win_end)

        self._attr_is_on = in_window
        self._next_window_start = win_start
        self._next_window_end = win_end

        if self._added:
            self.async_write_ha_state()

    # ------------- attributes -------------
    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        attrs: dict[str, str | bool] = {}
        if self._now_local:
            attrs["Now"] = self._now_local.isoformat()
        if self._next_window_start:
            attrs["Next_Window_Start"] = self._next_window_start.isoformat()
        if self._next_window_end:
            attrs["Next_Window_End"] = self._next_window_end.isoformat()
        attrs["Activation_Logic"] = self._activation_logic_text()
        return attrs
