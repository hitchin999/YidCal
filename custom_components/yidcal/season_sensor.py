# custom_components/yidcal/season_sensor.py
"""
Sensor: Jewish Calendar Season

State: "פסח עד סוכות" or "סוכות עד פסח"
  (easy to select in automation triggers / conditions)

Attributes (all boolean):
  Pesach_to_Sukkos             – 15 Nisan through 14 Tishrei
  Sukkos_to_Pesach             – 15 Tishrei through 14 Nisan
  Pesach_till_Shvuos           – 15 Nisan through 5 Sivan
  Shvuos_till_Rosh_Hashanah    – 6 Sivan through 29 Elul
  After_Shvuos_till_DST_OFF    – after Shavuos AND DST is currently ON
  DST_OFF_till_Pesach          – DST is OFF AND before Pesach (winter→spring)
  DST_ON_till_Pesach           – DST is ON AND before Pesach (spring, clocks forward)
  DST_OFF_till_Chanukah        – DST is OFF AND before 25 Kislev
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from pyluach.dates import HebrewDate as PHebrewDate

from .device import YidCalSpecialDevice
from .const import DOMAIN


# Hebrew month numbers (pyluach convention):
#  1=Nisan  2=Iyar  3=Sivan  4=Tammuz  5=Av  6=Elul
#  7=Tishrei  8=Cheshvan  9=Kislev  10=Teves  11=Shvat
#  12=Adar (or Adar I)  13=Adar II (leap years)

_SUMMER_MONTHS = {1, 2, 3, 4, 5, 6}   # Nisan–Elul
_WINTER_MONTHS = {7, 8, 9, 10, 11, 12, 13}  # Tishrei–Adar


class SeasonSensor(YidCalSpecialDevice, SensorEntity):
    """Tracks the broad Jewish-calendar season and sub-periods."""

    _attr_name = "Season"
    _attr_icon = "mdi:weather-partly-snowy-rainy"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "season"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._state: str | None = None

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()
        # Recalculate at midnight and every hour (DST can flip mid-day on transition days)
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, minute=0, second=5)
        )

    # ── helpers ──

    @staticmethod
    def _heb_date(d) -> PHebrewDate:
        return PHebrewDate.from_pydate(d)

    @staticmethod
    def _in_range(hd: PHebrewDate, start_month: int, start_day: int,
                  end_month: int, end_day: int) -> bool:
        """Check if hd is within [start_month/start_day .. end_month/end_day] inclusive.

        Handles wrapping around the Hebrew year boundary (Tishrei=7 → Adar=12/13).
        """
        m, d = hd.month, hd.day
        start = (start_month, start_day)
        end = (end_month, end_day)

        if start <= end:
            return start <= (m, d) <= end
        else:
            # wraps around year boundary
            return (m, d) >= start or (m, d) <= end

    def _is_dst(self, now_local: datetime) -> bool:
        """True when DST is active in the configured timezone."""
        dst = now_local.dst()
        return dst is not None and dst.total_seconds() > 0

    # ── main update ──

    async def async_update(self, now=None) -> None:
        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()
        hd = self._heb_date(today)
        m, d = hd.month, hd.day

        dst_on = self._is_dst(now_local)

        # ── Main season ──
        # Pesach-to-Sukkos: 15 Nisan (m=1,d=15) through 14 Tishrei (m=7,d=14)
        pesach_to_sukkos = self._in_range(hd, 1, 15, 7, 14)
        sukkos_to_pesach = not pesach_to_sukkos

        self._state = "בין פסח לסוכות" if pesach_to_sukkos else "בין סוכות לפסח"

        # ── Sub-periods ──
        # Pesach till Shvuos: 15 Nisan through 5 Sivan
        pesach_till_shvuos = self._in_range(hd, 1, 15, 3, 5)

        # Shvuos till Rosh Hashanah: 6 Sivan through 29 Elul
        shvuos_till_rh = self._in_range(hd, 3, 6, 6, 29)

        # After Shvuos till DST OFF: post-Shavuos AND DST still active
        after_shvuos_till_dst_off = shvuos_till_rh and dst_on

        # DST OFF till Pesach: DST is off AND we're in the Sukkos→Pesach half
        # (narrower: between DST-off and 14 Nisan — but since DST-off happens
        #  in autumn, this is essentially: DST off AND before Pesach)
        dst_off_till_pesach = (not dst_on) and sukkos_to_pesach

        # DST ON till Pesach: DST is on AND before Pesach (spring forward happened
        # but Pesach hasn't started yet)
        dst_on_till_pesach = dst_on and sukkos_to_pesach

        # DST OFF till Chanukah: DST is off AND before 25 Kislev
        # "before Chanukah" = haven't reached 25 Kislev yet this season
        before_chanukah = (
            (m == 7)  # Tishrei
            or (m == 8)  # Cheshvan
            or (m == 9 and d < 25)  # Kislev before 25
        )
        dst_off_till_chanukah = (not dst_on) and before_chanukah

        self._attr_extra_state_attributes = {
            "Pesach_to_Sukkos": pesach_to_sukkos,
            "Sukkos_to_Pesach": sukkos_to_pesach,
            "Pesach_till_Shvuos": pesach_till_shvuos,
            "Shvuos_till_Rosh_Hashanah": shvuos_till_rh,
            "After_Shvuos_till_DST_OFF": after_shvuos_till_dst_off,
            "DST_OFF_till_Pesach": dst_off_till_pesach,
            "DST_ON_till_Pesach": dst_on_till_pesach,
            "DST_OFF_till_Chanukah": dst_off_till_chanukah,
            "DST_Active": dst_on,
        }

        self.async_write_ha_state()
