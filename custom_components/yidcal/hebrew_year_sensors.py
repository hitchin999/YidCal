"""
custom_components/yidcal/hebrew_year_sensors.py

Hebrew-year-scoped sensors, all grouped under the YidCal — Special device:

  binary_sensor.yidcal_leap_year            ON when the current Hebrew year
                                            is a leap year (שנה מעוברת).
  binary_sensor.yidcal_leap_year_next_year  ON when the NEXT Hebrew year is
                                            a leap year.
  binary_sensor.yidcal_shmita               ON during a Shmita year.
  sensor.yidcal_years_until_shmita          Whole years remaining until the
                                            next Shmita (0 during Shmita
                                            itself).

The "current Hebrew year" flips at the same instant as sensor.yidcal_date:
(sunset + havdalah offset), ceiling-rounded Motzi-style — so on the evening
of Erev Rosh Hashanah all four sensors update in lock-step with the Date
sensor.

Leap-year truth comes from the central halacha_events.is_leap_hebrew_year
(19-year machzor). Shmita follows the universal convention of
Hebrew years divisible by 7 (e.g. 5775, 5782, 5789).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
    is_leap_hebrew_year,
    is_shmita_year,
    next_leap_year,
    next_shmita_year,
    shmita_cycle_year,
    year_in_cycle,
    years_until_shmita,
)
from .yidcal_lib.zman_compute import round_ceil as _round_ceil, sunset_for_date


# All year math lives in yidcal_lib/halacha_events.py (single source of
# truth). Only the rollover helper below is defined here: it is clock-time
# logic, which halacha_events' design rules explicitly exclude.

def effective_py_date(now: datetime, sunset: datetime, havdalah_offset: timedelta) -> date:
    """The YidCal-effective civil date for *now*: tomorrow once past the
    ceiling-rounded (sunset + havdalah) boundary — identical to DateSensor."""
    switch_time = _round_ceil(sunset + havdalah_offset)
    return now.date() + timedelta(days=1) if now >= switch_time else now.date()


# ── Entity plumbing ──────────────────────────────────────────────────────

class _HebrewYearBase(YidCalSpecialDevice):
    """Shared base: nightfall-aware Hebrew year on the DateSensor cadence."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._havdalah_offset = timedelta(minutes=havdalah_offset)

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        tzname = cfg.get("tzname", hass.config.time_zone)
        self._tz = ZoneInfo(tzname)

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

        # Same three listeners as DateSensor, so the year flips in
        # lock-step with sensor.yidcal_date on Erev Rosh Hashanah night
        # (and reacts within a minute to timedatectl time-travel tests).
        self._register_sunset(
            self.hass, self._schedule_update, offset=self._havdalah_offset
        )
        self._register_listener(
            async_track_time_change(self.hass, self._schedule_update, second=0)
        )
        self._register_interval(
            self.hass, self._schedule_update, timedelta(minutes=1)
        )

    def _current_hebrew_year(self) -> int:
        now = dt_util.now().astimezone(self._tz)
        sunset = sunset_for_date(geo=self._geo, tz=self._tz, base_date=now.date())
        py_date = effective_py_date(now, sunset, self._havdalah_offset)
        return PHebrewDate.from_pydate(py_date).year

    async def _update_state(self) -> None:
        if not self._geo:
            return
        self._recompute(self._current_hebrew_year())
        if self._added:
            self.async_write_ha_state()

    def _recompute(self, hy: int) -> None:
        raise NotImplementedError

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self._attrs)


class LeapYearSensor(_HebrewYearBase, BinarySensorEntity):
    """ON when the current Hebrew year is a leap year (שנה מעוברת)."""

    _attr_name = "Leap Year"
    _attr_icon = "mdi:calendar-plus"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__(hass, havdalah_offset)
        slug = "leap_year"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_is_on = False

    def _recompute(self, hy: int) -> None:
        leap = is_leap_hebrew_year(hy)
        self._attr_is_on = leap
        self._attrs = {
            "Hebrew_Year": hy,
            "Year_In_Cycle": year_in_cycle(hy),
            "Months_In_Year": 13 if leap else 12,
            "Next_Leap_Year": next_leap_year(hy),
        }


class LeapYearNextYearSensor(_HebrewYearBase, BinarySensorEntity):
    """ON when the NEXT Hebrew year is a leap year."""

    _attr_name = "Leap Year Next Year"
    _attr_icon = "mdi:calendar-arrow-right"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__(hass, havdalah_offset)
        slug = "leap_year_next_year"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_is_on = False

    def _recompute(self, hy: int) -> None:
        ny = hy + 1
        leap = is_leap_hebrew_year(ny)
        self._attr_is_on = leap
        self._attrs = {
            "Hebrew_Year": hy,
            "Next_Hebrew_Year": ny,
            "Next_Year_In_Cycle": year_in_cycle(ny),
            "Months_In_Next_Year": 13 if leap else 12,
        }


class ShmitaSensor(_HebrewYearBase, BinarySensorEntity):
    """ON during a Shmita year."""

    _attr_name = "Shmita"
    _attr_icon = "mdi:sprout"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__(hass, havdalah_offset)
        slug = "shmita"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_is_on = False

    def _recompute(self, hy: int) -> None:
        self._attr_is_on = is_shmita_year(hy)
        self._attrs = {
            "Hebrew_Year": hy,
            "Shmita_Cycle_Year": shmita_cycle_year(hy),
            "Years_Until_Shmita": years_until_shmita(hy),
            "Next_Shmita_Year": next_shmita_year(hy),
        }


class YearsUntilShmitaSensor(_HebrewYearBase, SensorEntity):
    """Whole years remaining until the next Shmita (0 during Shmita)."""

    _attr_name = "Years Until Shmita"
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "years"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__(hass, havdalah_offset)
        slug = "years_until_shmita"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value: int | None = None

    def _recompute(self, hy: int) -> None:
        self._attr_native_value = years_until_shmita(hy)
        self._attrs = {
            "Hebrew_Year": hy,
            "Shmita_Cycle_Year": shmita_cycle_year(hy),
            "Next_Shmita_Year": next_shmita_year(hy),
            # House convention: boolean ATTRIBUTES as strings for HA
            # state-condition matching.
            "Is_Shmita": "true" if is_shmita_year(hy) else "false",
        }
