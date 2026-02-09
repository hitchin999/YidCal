# custom_components/yidcal/longer_shabbos_shachris_sensor.py
"""
Binary sensor: Longer Shabbos Shachris

ON for the entire Shabbos (candle-lighting Friday → havdalah Motzei Shabbos)
when the davening is longer due to:
  • שבת שקלים, שבת זכור, שבת פרה, שבת החודש  (4 special parshiyos)
  • שבת הגדול
  • שבת ראש חודש
  • פורים משולש
  • שבת מברכים  (birkas hachodesh)
  • שבת חנוכה
  • שבת חנוכה ראש חודש
  • שבת חול המועד סוכות
  • שבת חול המועד פסח

Always OFF on weekdays (use the existing "Longer Shachris" sensor for weekday scenarios).

Attributes:
  Now, Window_Start, Window_End, Reason, Activation_Logic
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar

from pyluach.dates import HebrewDate as PHebrewDate
from pyluach.hebrewcal import Year as PYear
from pyluach import parshios as pyluach_parshios, dates as pyluach_dates

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (
        (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if dt.second or dt.microsecond
        else dt
    )


def _month_length_safe(y: int, m: int) -> int:
    try:
        PHebrewDate(y, m, 30)
        return 30
    except Exception:
        return 29


class LongerShabbosSensor(YidCalSpecialDevice, RestoreEntity, BinarySensorEntity):
    """ON for the full Shabbos (candle-lighting → havdalah) on qualifying Shabbosim."""

    _attr_name = "Longer Shabbos Shachris"
    _attr_icon = "mdi:alarm-plus"
    _attr_unique_id = "yidcal_longer_shabbos_shachris"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_longer_shabbos_shachris"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)
        self._is_in_israel = not self._diaspora

        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None

        self._attr_extra_state_attributes: dict = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ─── qualification logic ───

    def _get_reasons(self, shabbat_date) -> list[str]:
        """Return list of reasons this Shabbos qualifies (empty = not special)."""
        greg = pyluach_dates.GregorianDate.from_pydate(shabbat_date)
        hd = greg.to_heb()
        Y = hd.year
        reasons: list[str] = []

        is_leap = PYear(Y).leap
        adar_month = 13 if is_leap else 12

        # ── Four Parshiyos ──
        rc_adar = PHebrewDate(Y, adar_month, 1).to_pydate()
        if 0 <= (rc_adar - shabbat_date).days <= 6:
            reasons.append("שבת שקלים")

        purim = PHebrewDate(Y, adar_month, 14).to_pydate()
        if 1 <= (purim - shabbat_date).days <= 6:
            reasons.append("שבת זכור")

        rc_nisan = PHebrewDate(Y, 1, 1).to_pydate()
        if 0 <= (rc_nisan - shabbat_date).days <= 6:
            reasons.append("שבת החודש")

        next_week = shabbat_date + timedelta(days=7)
        nw_heb = pyluach_dates.GregorianDate.from_pydate(next_week).to_heb()
        rc_nisan2 = PHebrewDate(nw_heb.year, 1, 1).to_pydate()
        if "שבת החודש" not in reasons and 0 <= (rc_nisan2 - next_week).days <= 6:
            reasons.append("שבת פרה")

        # ── שבת הגדול (Shabbos before Pesach) ──
        pesach = PHebrewDate(Y, 1, 15).to_pydate()
        if 0 < (pesach - shabbat_date).days <= 8:
            reasons.append("שבת הגדול")

        # ── פורים משולש (Shushan Purim on Shabbos = 15 Adar) ──
        if hd.month == adar_month and hd.day == 15:
            reasons.append("פורים משולש")

        # ── שבת ראש חודש (not in Tishrei) ──
        if hd.month != 7:
            length_cur = _month_length_safe(hd.year, hd.month)
            if hd.day == 1 or (hd.day == 30 and length_cur == 30):
                reasons.append("שבת ראש חודש")

        # ── שבת מברכים (skip Tishrei) ──
        if hd.month == 13 or (hd.month == 12 and not is_leap):
            next_month_num = 1
            next_month_year = hd.year + 1
        else:
            next_month_num = hd.month + 1
            next_month_year = hd.year

        if next_month_num != 7:  # skip Mevorchim for Tishrei
            rc_gdays = []
            length_cur = _month_length_safe(hd.year, hd.month)
            if length_cur == 30:
                rc_gdays.append(PHebrewDate(hd.year, hd.month, 30).to_pydate())
            rc_gdays.append(PHebrewDate(next_month_year, next_month_num, 1).to_pydate())
            first_rc = min(rc_gdays)
            first_wd = first_rc.weekday()
            if first_wd == 5:
                mevorchim_date = first_rc - timedelta(days=7)
            else:
                days_back = (first_wd - 5) % 7
                mevorchim_date = first_rc - timedelta(days=days_back)
            if shabbat_date == mevorchim_date:
                reasons.append("שבת מברכים")

        # ── שבת חנוכה (Chanukah: 25–30 Kislev + 1–2 Teves) ──
        is_chanukah = (hd.month == 9 and 25 <= hd.day <= 30) or (
            hd.month == 10 and hd.day in (1, 2)
        )
        if is_chanukah:
            # Check if also Rosh Chodesh
            is_rc = hd.day == 1 or (hd.day == 30 and _month_length_safe(hd.year, hd.month) == 30)
            if is_rc and hd.month != 7:
                reasons.append("שבת חנוכה ראש חודש")
            else:
                reasons.append("שבת חנוכה")

        # ── שבת חול המועד סוכות ──
        if self._diaspora:
            is_chm_sukkos = hd.month == 7 and 17 <= hd.day <= 20
        else:
            is_chm_sukkos = hd.month == 7 and 16 <= hd.day <= 20
        if is_chm_sukkos:
            reasons.append("שבת חול המועד סוכות")

        # ── שבת חול המועד פסח ──
        if self._diaspora:
            is_chm_pesach = hd.month == 1 and 17 <= hd.day <= 20
        else:
            is_chm_pesach = hd.month == 1 and 16 <= hd.day <= 20
        if is_chm_pesach:
            reasons.append("שבת חול המועד פסח")

        return reasons

    def _shabbos_window(self, friday, saturday) -> tuple[datetime, datetime]:
        """Candle-lighting Friday → havdalah Motzei Shabbos."""
        fri_sunset = (
            ZmanimCalendar(geo_location=self._geo, date=friday)
            .sunset()
            .astimezone(self._tz)
        )
        sat_sunset = (
            ZmanimCalendar(geo_location=self._geo, date=saturday)
            .sunset()
            .astimezone(self._tz)
        )
        on_time = _round_half_up(fri_sunset - timedelta(minutes=self._candle))
        off_time = _round_ceil(sat_sunset + timedelta(minutes=self._havdalah))
        return on_time, off_time

    def _next_qualifying_shabbos(self, ref) -> tuple | None:
        """Find the next Saturday on or after ref that qualifies."""
        wd = ref.weekday()
        d = ref + timedelta(days=(5 - wd) % 7)
        for _ in range(55):  # scan ~1 year of Shabbosim
            reasons = self._get_reasons(d)
            if reasons:
                return d, reasons
            d += timedelta(days=7)
        return None

    # ─── main update ───

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)
        today = now.date()
        wd = today.weekday()  # 0=Mon … 4=Fri, 5=Sat

        window_start = window_end = None
        reasons: list[str] = []

        if wd in (4, 5):  # Friday or Saturday — could be inside Shabbos window
            friday = today if wd == 4 else (today - timedelta(days=1))
            saturday = friday + timedelta(days=1)

            reasons = self._get_reasons(saturday)
            if reasons:
                window_start, window_end = self._shabbos_window(friday, saturday)
                self._attr_is_on = (window_start <= now < window_end)

                if not self._attr_is_on and now >= window_end:
                    # This Shabbos passed; find the next one
                    nxt = self._next_qualifying_shabbos(saturday + timedelta(days=1))
                    if nxt:
                        next_sat, reasons = nxt
                        next_fri = next_sat - timedelta(days=1)
                        window_start, window_end = self._shabbos_window(next_fri, next_sat)
            else:
                self._attr_is_on = False
                nxt = self._next_qualifying_shabbos(saturday + timedelta(days=1))
                if nxt:
                    next_sat, reasons = nxt
                    next_fri = next_sat - timedelta(days=1)
                    window_start, window_end = self._shabbos_window(next_fri, next_sat)
        else:
            self._attr_is_on = False
            nxt = self._next_qualifying_shabbos(today)
            if nxt:
                next_sat, reasons = nxt
                next_fri = next_sat - timedelta(days=1)
                window_start, window_end = self._shabbos_window(next_fri, next_sat)

        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat() if window_start else "",
            "Window_End": window_end.isoformat() if window_end else "",
            "Reason": " / ".join(reasons) if reasons else "",
            "Activation_Logic": (
                "ON for entire Shabbos (candle-lighting → havdalah) when shachris is longer due to: "
                "שבת שקלים, שבת זכור, שבת פרה, שבת החודש, שבת הגדול, "
                "שבת ראש חודש, פורים משולש, שבת מברכים, "
                "שבת חנוכה, שבת חנוכה ראש חודש, "
                "שבת חול המועד סוכות, שבת חול המועד פסח."
            ),
        }
