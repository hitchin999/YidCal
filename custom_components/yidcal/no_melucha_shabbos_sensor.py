from __future__ import annotations

from hdate import HDateInfo
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

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


class NoMeluchaShabbosSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    ON only on *regular* Shabbos:
      Friday sunset − candle_offset → Saturday sunset + havdalah_offset

    OFF if that Shabbos is also Yom Tov (e.g., RH on Shabbos, YT day on Shabbos).
    ON on Shabbos Chol HaMoed (since it isn't is_yom_tov).
    """
    _attr_name = "No Melucha – Regular Shabbos"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_regular_shabbos"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.entity_id = "binary_sensor.yidcal_no_melucha_regular_shabbos"

        self.hass = hass
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

    def _round_half_up(self, dt: datetime) -> datetime:
        if dt.second >= 30:
            dt += timedelta(minutes=1)
        return dt.replace(second=0, microsecond=0)

    def _round_ceil(self, dt: datetime) -> datetime:
        return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

    def _week_window_if_regular(self, base_date: datetime.date) -> tuple[datetime | None, datetime | None]:
        """
        Return the window for the *nearest* Shabbos at/after base_date that is NOT Yom Tov.
        Skips Shabbos that is Yom Tov. (Chol HaMoed Shabbos is allowed.)
        """
        # Find the Friday of the week containing/after base_date
        wd = base_date.weekday()  # Mon=0..Sat=5..Sun=6
        friday = base_date - timedelta(days=(wd - 4) % 7)

        for k in range(0, 16):  # look ahead up to ~4 months (safety)
            f = friday + timedelta(days=7 * k)
            s = f + timedelta(days=1)  # Saturday
            # Skip if this Shabbos is Yom Tov
            if HDateInfo(s, diaspora=self._diaspora).is_yom_tov:
                continue

            start_dt = ZmanimCalendar(geo_location=self._geo, date=f).sunset().astimezone(self._tz) - timedelta(minutes=self._candle)
            end_dt   = ZmanimCalendar(geo_location=self._geo, date=s).sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)
            return start_dt, end_dt

        return None, None  # shouldn't happen

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)
        today = now.date()

        # Find the current or next *regular* Shabbos window (skips Yom Tov on Shabbos)
        s_raw, e_raw = self._week_window_if_regular(today)
        if s_raw is None:
            # no window found; publish off with blanks
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": "",
                "Window_End": "",
                "Activation_Logic": "On for regular Shabbos only: From Candle lighting till Havdalah. Off if Shabbos is Yom Tov. On on Shabbos Chol HaMoed.",
            }
            return

        # If we've already passed this window, jump to the next regular Shabbos
        if now >= e_raw:
            s_raw, e_raw = self._week_window_if_regular(today + timedelta(days=7))

        window_start = self._round_half_up(s_raw)
        window_end   = self._round_ceil(e_raw)

        self._attr_is_on = window_start <= now < window_end
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
            "Activation_Logic": "On for regular Shabbos only: From Candle lighting till Havdalah. Off if Shabbos is Yom Tov. On on Shabbos Chol HaMoed.",
        }

