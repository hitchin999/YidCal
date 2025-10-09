from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

import homeassistant.util.dt as dt_util
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_interval

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .device import YidCalDevice
from .const import DOMAIN
from .zman_sensors import get_geo  # reuse geo helper like Zman Erev

_LOGGER = logging.getLogger(__name__)


class UpcomingYomTovSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    Turns ON before the next target event, OFF at candle-lighting (erev).

    Targets (diaspora-aware):
      • Any Yom Tov day (HDateInfo(...).is_yom_tov == True)
      • PLUS: Shabbos Chol HaMoed ("שבת חול המועד סוכות" / "שבת חול המועד פסח")

    Freeze rule (like Zman Erev):
      • Do NOT advance to a new target until 12:00 AM of the civil day AFTER the most recent Motzi
        (Shabbos or Yom Tov). All “Next_On” calculations respect this gate.
    """

    _attr_name = "Upcoming Yom Tov"
    _attr_icon = "mdi:calendar-star-outline"
    _attr_has_entity_name = False
    _attr_should_poll = False  # we schedule our own minute tick

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass

        # Defaults; central config will override on first update
        self._candle_offset = int(candle_offset)
        self._havdalah_offset = int(havdalah_offset)
        self._diaspora: bool = True
        self._tz: ZoneInfo = ZoneInfo(hass.config.time_zone)
        self._geo: GeoLocation | None = None

        self._attr_unique_id = "yidcal_upcoming_yomtov"
        self.entity_id = "binary_sensor.yidcal_upcoming_yomtov"

        self._attr_is_on = False
        self._attr_extra_state_attributes: dict[str, object] = {
            "Next_Holiday": "",
            "Date": "",
            "Next_On": "",
        }

        self._cfg_sig: tuple | None = None  # passive change detection

    # ── Central config hydration (passive, like Zman Erev) ───────────────────
    async def _maybe_refresh_config(self) -> None:
        cfg = self.hass.data.get(DOMAIN, {}).get("config", {}) or {}
        tzname = cfg.get("tzname", self.hass.config.time_zone)
        diaspora = bool(cfg.get("diaspora", True))
        candle = int(cfg.get("candlelighting_offset", self._candle_offset))
        havd = int(cfg.get("havdalah_offset", self._havdalah_offset))
        lat = float(cfg.get("latitude", self.hass.config.latitude or 0.0))
        lon = float(cfg.get("longitude", self.hass.config.longitude or 0.0))

        new_sig = (tzname, diaspora, candle, havd, round(lat, 6), round(lon, 6))
        if new_sig == self._cfg_sig and self._geo is not None:
            return

        self._diaspora = diaspora
        self._candle_offset = candle
        self._havdalah_offset = havd
        try:
            self._tz = ZoneInfo(tzname)
        except Exception:
            self._tz = ZoneInfo(self.hass.config.time_zone)

        self._geo = await get_geo(self.hass)
        self._cfg_sig = new_sig

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == "on")
            self._attr_extra_state_attributes = {
                "Next_Holiday": last.attributes.get("Next_Holiday", ""),
                "Date": last.attributes.get("Date", ""),
                "Next_On": last.attributes.get("Next_On", ""),
            }

        await self._maybe_refresh_config()
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _last_motzi_cutoff_date(self, ref: datetime.date) -> datetime.date | None:
        """Civil date when we may advance (the day AFTER the last Motzi)."""
        for back in range(0, 14):
            d = ref - timedelta(days=back)
            hd0 = HDateInfo(d, diaspora=self._diaspora)
            hd1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)
            ended_shabbos = (d.weekday() == 5) and (not hd1.is_yom_tov)
            ended_yomtov  = hd0.is_yom_tov and (not hd1.is_yom_tov)
            if ended_shabbos or ended_yomtov:
                return d + timedelta(days=1)
        return None

    def _is_chm_day(self, d: datetime.date) -> tuple[bool, str]:
        """
        Return (is_chm, tag) where tag is 'סוכות' or 'פסח' for the CHM week.
        Uses hdate flag if present; falls back to Hebrew month/day ranges (diaspora).
        """
        info = HDateInfo(d, diaspora=self._diaspora)
        # Try hdate's chol hamoed flag (attr or method, versions differ)
        is_flag = False
        try:
            is_flag = bool(getattr(info, "is_chol_hamoed"))
        except Exception:
            try:
                is_flag = bool(info.is_chol_hamoed())
            except Exception:
                is_flag = False

        h = PHebrewDate.from_pydate(d)
        mon = h.month  # 7=Tishrei, 1=Nisan
        day = h.day

        if is_flag:
            if mon == 7:
                return (True, "סוכות")
            if mon == 1:
                return (True, "פסח")
            return (True, "")

        # Fallback windows (diaspora):
        #   Sukkos (Tishrei): CHM = 17..21
        if mon == 7 and 17 <= day <= 21:
            return (True, "סוכות")
        #   Pesach (Nisan): CHM = 17..20
        if mon == 1 and 17 <= day <= 20:
            return (True, "פסח")

        return (False, "")

    def _week_has_chm(self, d: datetime.date) -> tuple[bool, bool]:
        """
        Search [d-6 .. d+6] for any CHM day (using _is_chm_day). Returns (has_sukkos, has_pesach).
        """
        has_sukkos = False
        has_pesach = False
        for delta in range(-6, 7):
            dd = d + timedelta(days=delta)
            is_chm, tag = self._is_chm_day(dd)
            if is_chm:
                if tag == "סוכות":
                    has_sukkos = True
                elif tag == "פסח":
                    has_pesach = True
        return (has_sukkos, has_pesach)

    def _find_next_event(self, base_date: datetime.date) -> tuple[str, datetime.date | None]:
        """
        Prefer any Shabbos Chol HaMoed (פסח/סוכות) that falls before the next true Yom Tov.
        Otherwise, return the next true Yom Tov.
        """
        # 1) Next true Yom Tov
        next_yt_name, next_yt_date = "", None
        for j in range(1, 366):
            d2 = base_date + timedelta(days=j)
            if HDateInfo(d2, diaspora=self._diaspora).is_yom_tov:
                nm = PHebrewDate.from_pydate(d2).holiday(hebrew=True) or ""
                if nm:
                    next_yt_name, next_yt_date = nm, d2
                    break

        # 2) If we have a next YT, check ALL Saturdays before it for Chol HaMoed
        if next_yt_date is not None:
            d = base_date + timedelta(days=1)
            while d < next_yt_date:
                if d.weekday() == 5:  # Saturday
                    cand_info = HDateInfo(d, diaspora=self._diaspora)
                    cand_base = PHebrewDate.from_pydate(d).holiday(hebrew=True, prefix_day=False) or ""
                    is_chm_str = ("חול המועד" in cand_base) and (not cand_info.is_yom_tov)
                    if is_chm_str:
                        if "סוכות" in cand_base:
                            return ("שבת חול המועד סוכות", d)
                        if "פסח" in cand_base:
                            return ("שבת חול המועד פסח", d)

                    # Fallback: logic-based CHM detection in the surrounding week
                    if not cand_info.is_yom_tov:
                        has_chm_sukkos, has_chm_pesach = self._week_has_chm(d)
                        if has_chm_sukkos:
                            return ("שבת חול המועד סוכות", d)
                        if has_chm_pesach:
                            return ("שבת חול המועד פסח", d)
                d += timedelta(days=1)

            # No Chol-HaMoed Shabbos -> return the next YT
            return (next_yt_name, next_yt_date)

        # 3) No YT at all (very rare) → search ahead for a Shabbos Chol HaMoed
        d = base_date + timedelta(days=1)
        for _ in range(365):
            if d.weekday() == 5:
                cand_info = HDateInfo(d, diaspora=self._diaspora)
                cand_base = PHebrewDate.from_pydate(d).holiday(hebrew=True, prefix_day=False) or ""
                is_chm_str = ("חול המועד" in cand_base) and (not cand_info.is_yom_tov)
                if is_chm_str:
                    if "סוכות" in cand_base:
                        return ("שבת חול המועד סוכות", d)
                    if "פסח" in cand_base:
                        return ("שבת חול המועד פסח", d)

                if not cand_info.is_yom_tov:
                    has_chm_sukkos, has_chm_pesach = self._week_has_chm(d)
                    if has_chm_sukkos:
                        return ("שבת חול המועד סוכות", d)
                    if has_chm_pesach:
                        return ("שבת חול המועד פסח", d)
            d += timedelta(days=1)

        return ("", None)

    def _candle_lighting_prev_day(self, target: datetime.date) -> datetime.datetime:
        """Candle-lighting time for the eve of `target` (prev civil day before sunset)."""
        assert self._geo is not None
        cal = ZmanimCalendar(geo_location=self._geo, date=target - timedelta(days=1))
        sunset = cal.sunset().astimezone(self._tz)
        return sunset - timedelta(minutes=self._candle_offset)

    # ── Core update ──────────────────────────────────────────────────────────
    async def async_update(self, now: datetime.datetime | None = None) -> None:
        await self._maybe_refresh_config()
        if not self._geo:
            return

        tz = self._tz
        now = (now or dt_util.now()).astimezone(tz)
        today = now.date()

        # Freeze rule: allow lookahead only from 12:00 AM after last Motzi
        cutoff = self._last_motzi_cutoff_date(today)
        allow_forward_jump_today = (cutoff is not None and today >= cutoff)
        base_scan_date = today if allow_forward_jump_today else (today - timedelta(days=1))

        # Next target (Yom Tov or Shabbos Chol HaMoed)
        next_name, next_date = self._find_next_event(base_scan_date)

        # --- Compute state window for the CURRENT target ---
        is_on = False
        next_on_time: datetime.datetime | None = None
        if next_date:
            # Theoretical 7-days-before midnight (legacy semantics; used only as a floor)
            theoretical_on = datetime.datetime(
                (next_date - timedelta(days=7)).year,
                (next_date - timedelta(days=7)).month,
                (next_date - timedelta(days=7)).day,
                0, 0, tzinfo=tz
            )

            # OFF at candle-lighting on the eve of target
            off_time = self._candle_lighting_prev_day(next_date)

            # Effective ON for *current* target is gated by today's cutoff
            if cutoff:
                cutoff_midnight = datetime.datetime(cutoff.year, cutoff.month, cutoff.day, 0, 0, tzinfo=tz)
                next_on_time = max(theoretical_on, cutoff_midnight)
            else:
                next_on_time = theoretical_on

            is_on = (now >= next_on_time) and (now < off_time)

        # --- Decide what to show as Next_On (always future-facing) ---
        # OFF → show effective ON for current target
        # ON  → show effective ON for FOLLOWING target, gated by current target's Motzi (midnight of target+1)
        next_on_for: datetime.datetime | None = None
        shown_target_date = next_date

        if next_date:
            if is_on:
                _n2, next2_date = self._find_next_event(next_date)
                if next2_date:
                    shown_target_date = next2_date
                    theoretical_on2 = datetime.datetime(
                        (next2_date - timedelta(days=7)).year,
                        (next2_date - timedelta(days=7)).month,
                        (next2_date - timedelta(days=7)).day,
                        0, 0, tzinfo=tz
                    )
                    gate_midnight = datetime.datetime(
                        (next_date + timedelta(days=1)).year,
                        (next_date + timedelta(days=1)).month,
                        (next_date + timedelta(days=1)).day,
                        0, 0, tzinfo=tz
                    )
                    next_on_for = max(theoretical_on2, gate_midnight)
            else:
                next_on_for = next_on_time

        # Safety: if Next_On still ended up <= now (edge cases), advance once more
        if next_on_for is not None and next_on_for <= now and shown_target_date is not None:
            _n3, next3_date = self._find_next_event(shown_target_date)
            if next3_date:
                next_on_for = datetime.datetime(
                    (next3_date - timedelta(days=7)).year,
                    (next3_date - timedelta(days=7)).month,
                    (next3_date - timedelta(days=7)).day,
                    0, 0, tzinfo=tz
                )

        # Attributes for dashboards (e.g., "זמנים ל(...)")
        attrs: dict[str, object] = {
            "Next_Holiday": next_name,
            "Date": next_date.isoformat() if next_date else "",
            "Next_On": next_on_for.isoformat() if next_on_for else "",
        }

        self._attr_is_on = is_on
        self._attr_extra_state_attributes = attrs
        self.async_write_ha_state()
