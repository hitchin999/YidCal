# custom_components/yidcal/erev_tisha_bav_after_chatzos_sensor.py
"""
Binary sensor: Erev Tisha B'Av (After Chatzos)

ON when ALL of these are true:
  1. Today is Erev Tisha B'Av — the daytime of 8 Av
  2. Current time is AFTER chatzos hayom (midday)
  3. Current time is BEFORE shkia (the moment the fast / Tisha B'Av begins)

OFF (never turns on) when the erev of the fast coincides with Shabbos:
  • Nidche year — 9 Av falls on Shabbos, so the fast is deferred to Sunday
    (10 Av). Its erev is Shabbos; 8 Av itself is only Erev Shabbos in the
    Nine Days, not erev-of-fast. No after-chatzos mourning applies.
  • 8 Av itself falls on Shabbos (9 Av on Sunday). The fast still begins at
    Motzei Shabbos, so there is no after-chatzos window on Shabbos day.

The window end is the FLOORED sunset — the same anchor the holiday sensor
uses to flip ערב תשעה באב → תשעה באב and to fire the fast countdown — so this
sensor drops to OFF at the exact minute Tisha B'Av turns on.

Attributes:
  Now                    – ISO current local time
  Is_Erev_Tisha_Bav_Day  – whether today is a live Erev Tisha B'Av (after-chatzos) day
  Chatzos                – ISO chatzos hayom for today
  Tisha_Bav_Onset        – ISO floored shkia for today (fast start / window close)
  Erev_Falls_On_Shabbos  – whether this year's erev-of-fast is Shabbos (sensor suppressed)
  Activation_Logic       – human-readable description
"""

from __future__ import annotations

from datetime import datetime
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
    chatzos_hayom_for_date,
    round_half_up as _round_half_up,
    round_floor as _round_floor,
    sunset_for_date,
)
from .zman_sensors import get_geo

_AV = 5  # pyluach month number for Av


class ErevTishaBavAfterChatzosSensor(YidCalSpecialDevice, BinarySensorEntity):
    """ON from chatzos until shkia on Erev Tisha B'Av (weekday erev only)."""

    _attr_name = "Erev Tisha Bav After Chatzos"
    _attr_icon = "mdi:weather-sunset-down"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "erev_tisha_bav_after_chatzos"
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

    def _chatzos_for(self, d) -> datetime:
        """Chatzos hayom (Grossman true solar transit), half-up rounded."""
        return _round_half_up(
            chatzos_hayom_for_date(geo=self._geo, tz=self._tz, base_date=d)
        )

    def _shkia_floor_for(self, d) -> datetime:
        """Floored shkia — the fast-start anchor (matches holiday sensor)."""
        return _round_floor(
            sunset_for_date(geo=self._geo, tz=self._tz, base_date=d)
        )

    @staticmethod
    def _erev_on_shabbos(hyear: int) -> bool:
        """True when this year's erev-of-fast coincides with Shabbos.

        Two ways that happens:
          • Nidche year — 9 Av on Shabbos (fast deferred to Sunday), so the
            erev of the observed fast is Shabbos.
          • 8 Av itself falls on Shabbos (9 Av on Sunday) — the fast begins
            Motzei Shabbos, so there is no after-chatzos window on Shabbos.
        In both, this after-chatzos sensor stays OFF.
        """
        if he.is_tisha_bav_nidche(hyear):
            return True
        return PHebrewDate(hyear, _AV, 8).to_pydate().weekday() == 5  # 8 Av = Shabbos

    # ── main update ──

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        hd = PHebrewDate.from_pydate(today)
        is_av_8 = hd.month == _AV and hd.day == 8
        erev_on_shabbos = self._erev_on_shabbos(hd.year)

        # A live Erev Tisha B'Av after-chatzos day: 8 Av in a normal year
        # whose erev is not a Shabbos.
        qualifies = is_av_8 and not erev_on_shabbos

        chatzos = self._chatzos_for(today)
        onset = self._shkia_floor_for(today)  # shkia — Tisha B'Av begins

        self._attr_is_on = qualifies and (chatzos <= now_local < onset)

        self._attr_extra_state_attributes = {
            "Now": now_local.isoformat(),
            "Is_Erev_Tisha_Bav_Day": qualifies,
            "Chatzos": chatzos.isoformat(),
            "Tisha_Bav_Onset": onset.isoformat(),
            "Erev_Falls_On_Shabbos": erev_on_shabbos,
            "Activation_Logic": (
                "ON from chatzos hayom until shkia on Erev Tisha B'Av (8 Av), "
                "turning OFF the moment Tisha B'Av begins. Suppressed when the "
                "erev-of-fast is Shabbos — i.e. a nidche year (9 Av on Shabbos, "
                "fast deferred to Sunday) or when 8 Av itself is Shabbos. OFF otherwise."
            ),
        }

        if self._added:
            self.async_write_ha_state()
