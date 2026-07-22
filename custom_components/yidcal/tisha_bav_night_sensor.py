# custom_components/yidcal/tisha_bav_night_sensor.py
"""
Binary sensor: Tisha B'Av Night

ON from the moment the fast begins (shkia on the erev-of-fast) until alos
HaShachar (dawn) the next morning — i.e. the NIGHT portion of Tisha B'Av only.

Why this exists: Tisha B'Av spans two sunsets — the shkia that STARTS the
fast (the evening going into the night) and the shkia on the fast day itself
(near the very end). An automation keyed on "shkia" alone cannot tell them
apart. This sensor is ON only for the night (first shkia → alos), so an
automation can trigger on this sensor's ON/OFF transitions instead of trying
to disambiguate two sunset events. During the daytime of the fast the sensor
is OFF, so it never re-fires on the fast day's own (second) shkia.

Nidche year: when 9 Av falls on Shabbos the fast is deferred to 10 Av
(Sunday). The observed fast day is 10 Av if nidche, else 9 Av, and the night
is always the eve going into that day — so in a nidche year this runs from
shkia on 9 Av (Motzei Shabbos) to alos on 10 Av (Sunday). The case where
9 Av is Sunday (8 Av is Shabbos) is a normal fast on 9 Av whose night simply
begins at Motzei-Shabbos shkia; that falls out of the same logic.

Anchors:
  • Night start — FLOORED shkia on the erev-of-fast, the same fast-onset
    anchor the holiday sensor and the Erev-Tisha-B'Av sensor use.
  • Night end   — alos HaShachar (sunrise − 72 min), half-up rounded, the
    integration's standard alos.

Attributes:
  Now              – ISO current local time
  Fast_Day         – civil date the fast is observed (10 Av if nidche, else 9 Av)
  Is_Nidche        – whether this year's Tisha B'Av is deferred to Sunday
  Night_Start      – ISO floored shkia that begins the fast (sensor turns ON)
  Alos             – ISO alos the sensor turns OFF at
  Activation_Logic – human-readable description
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import (
    dawn_for_date,
    round_half_up as _round_half_up,
    round_floor as _round_floor,
    sunset_for_date,
)
from .zman_sensors import get_geo

_AV = 5  # pyluach month number for Av


class TishaBavNightSensor(YidCalSpecialDevice, BinarySensorEntity):
    """ON from the fast-start shkia until alos — the night of Tisha B'Av."""

    _attr_name = "Tisha Bav Night"
    _attr_icon = "mdi:weather-night"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "tisha_bav_night"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])

        self._geo = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # Update every minute (on the :00 second, like other YidCal sensors)
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

    # ── helpers ──

    def _shkia_floor_for(self, d: date) -> datetime:
        """Floored shkia — the fast-start anchor (matches holiday sensor)."""
        return _round_floor(
            sunset_for_date(geo=self._geo, tz=self._tz, base_date=d)
        )

    def _alos_for(self, d: date) -> datetime:
        """Alos HaShachar (sunrise − 72 min), half-up rounded."""
        return _round_half_up(
            dawn_for_date(geo=self._geo, tz=self._tz, base_date=d)
        )

    def _fast_day(self, hyear: int) -> date:
        """Civil date the fast is observed: 10 Av if nidche, else 9 Av."""
        day = 10 if he.is_tisha_bav_nidche(hyear) else 9
        return PHebrewDate(hyear, _AV, day).to_pydate()

    # ── main update ──

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # Av is mid-summer, far from Rosh Hashanah, so the Hebrew year of the
        # civil date is unambiguous whether "now" is in the fast's evening or
        # its early morning. The window below is computed absolutely from the
        # Hebrew calendar, so membership is correct across civil midnight.
        hyear = PHebrewDate.from_pydate(today).year
        is_nidche = he.is_tisha_bav_nidche(hyear)

        fast_day = self._fast_day(hyear)          # 10 Av if nidche, else 9 Av
        fast_erev = fast_day - timedelta(days=1)  # the eve going into the fast

        night_start = self._shkia_floor_for(fast_erev)  # shkia — fast begins
        night_end = self._alos_for(fast_day)            # alos — sensor turns off

        self._attr_is_on = night_start <= now_local < night_end

        self._attr_extra_state_attributes = {
            "Now": now_local.isoformat(),
            "Fast_Day": fast_day.isoformat(),
            "Is_Nidche": is_nidche,
            "Night_Start": night_start.isoformat(),
            "Alos": night_end.isoformat(),
            "Activation_Logic": (
                "ON from the floored shkia that begins Tisha B'Av (on the "
                "erev-of-fast) until alos HaShachar the next morning — the "
                "night portion only. In a nidche year (9 Av on Shabbos) the "
                "fast, and this night, shift to 10 Av: shkia on Motzei Shabbos "
                "→ alos Sunday. OFF at all other times, including Tisha B'Av "
                "daytime, so it never re-fires on the fast day's own shkia."
            ),
        }

        if self._added:
            self.async_write_ha_state()
