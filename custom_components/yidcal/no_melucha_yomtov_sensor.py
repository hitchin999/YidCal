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


class NoMeluchaYomTovSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    ON for any contiguous Yom Tov span from
    candle(before first day) → havdalah(after last day).
    Includes spans that land on Shabbos.
    """
    _attr_name = "No Melucha – Yom Tov"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_yomtov"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_no_melucha_yomtov"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    def _span_end(self, start: datetime.date) -> datetime.date:
        end = start
        while HDateInfo(end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            end += timedelta(days=1)
        if self._diaspora:
            n_end = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
            n_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(hebrew=True, prefix_day=False)
            if n_end == "שמיני עצרת" and n_next == "שמחת תורה":
                end = end + timedelta(days=1)
        return end

    def _find_active_span(self, now_local: datetime) -> tuple[datetime.date | None, datetime.date | None]:
        base = now_local.date()
        for i in range(-1, 32):
            d = base + timedelta(days=i)
            if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                end = self._span_end(d)
                sdt = ZmanimCalendar(geo_location=self._geo, date=d - timedelta(days=1)).sunset().astimezone(self._tz) - timedelta(minutes=self._candle)
                edt = ZmanimCalendar(geo_location=self._geo, date=end).sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)
                if sdt <= now_local < edt:
                    return d, end
        return None, None

    def _next_span_first_day_after(self, ref: datetime.date) -> datetime.date | None:
        for i in range(0, 90):
            d = ref + timedelta(days=i)
            if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                return d
        return None

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return
        now = dt_util.now().astimezone(self._tz)

        start_d, end_d = self._find_active_span(now)
        if start_d is None:
            nxt = self._next_span_first_day_after(now.date())
            if nxt:
                start_d, end_d = nxt, self._span_end(nxt)
            else:
                # nothing upcoming
                self._attr_is_on = False
                self._attr_extra_state_attributes = {
                    "Now": now.isoformat(),
                    "Window_Start": "",
                    "Window_End": "",
                }
                return

        start_dt = ZmanimCalendar(geo_location=self._geo, date=start_d - timedelta(days=1)).sunset().astimezone(self._tz) - timedelta(minutes=self._candle)
        end_dt   = ZmanimCalendar(geo_location=self._geo, date=end_d).sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)

        window_start = _round_half_up(start_dt)
        window_end = _round_ceil(end_dt)
        self._attr_is_on = window_start <= now < window_end
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
        }
