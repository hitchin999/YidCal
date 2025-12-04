# upcoming_holiday_sensor.py
"""
UpcomingHolidaySensor for YidCal.

- Progressive pre-activation beginning *two halachic days* before the current/next block.
- Publishes ALL holiday flags as boolean attributes (same Hebrew keys you use),
  excluding the fast timer text attributes.
- Hard-coded halachic day flip at sunset + havdalah offset.
- Simulation time for evaluations is 12:02 AM local.

State: a comma-separated list of flags that are currently true (truncated if too long).
Attributes: only {'lookahead_days': 2} plus all the flag booleans.
"""

from __future__ import annotations
import datetime as dt
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging
from typing import Dict, List, Tuple, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change, async_track_time_interval
from homeassistant.core import callback

from zmanim.zmanim_calendar import ZmanimCalendar
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from .zman_sensors import get_geo

from .device import YidCalDevice
from .holiday_sensor import HolidaySensor
from .const import DOMAIN

def _round_half_up(local_dt: dt.datetime) -> dt.datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if local_dt.second >= 30:
        local_dt += timedelta(minutes=1)
    return local_dt.replace(second=0, microsecond=0)


def _round_ceil(local_dt: dt.datetime) -> dt.datetime:
    """Always bump up to the *next* minute."""
    return (local_dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

def _alos_mga_72_for_date(geo, base_date: dt.date, tz: ZoneInfo) -> dt.datetime:
    """
    MGA alos = sunrise(base_date) - 72 minutes.
    Rounded half-up to minute, matching AlosSensor/HolidaySensor style.
    """
    cal = ZmanimCalendar(geo_location=geo, date=base_date)
    sunrise = cal.sunrise().astimezone(tz)
    alos = sunrise - timedelta(minutes=72)
    if alos.second >= 30:
        alos += timedelta(minutes=1)
    return alos.replace(second=0, microsecond=0)


def _is_hebrew_leap(year: int) -> bool:
    # Leap years are years 3,6,8,11,14,17,19 of the 19-year cycle
    return ((7 * year + 1) % 19) < 7

_LOGGER = logging.getLogger(__name__)

class UpcomingHolidaySensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_name = "Upcoming Holiday"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
        *,
        lookahead_days: int = 2,
        horizon_days: int = 14,
        update_interval_minutes: int = 15,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset = int(candle_offset)
        self._havdalah_offset = int(havdalah_offset)
        self._lookahead_days = int(lookahead_days)
        self._horizon_days = int(horizon_days)
        self._interval = int(update_interval_minutes)

        slug = "upcoming_holiday"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self._attr_native_value: str = ""
        self._attr_extra_state_attributes: Dict[str, object] = {}

        # Read mode (diaspora vs EY) from config
        cfg = hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora: bool = cfg.get("diaspora", True)

        # Base of all boolean flags (False), pruned to the current mode
        self._all_flags_template: Dict[str, bool] = {name: False for name in self._allowed_names()}

    # ───────────────────── Mode pruning helpers ─────────────────────

    def _attr_allowed_in_mode(self, name: str) -> bool:
        """Mirror HolidaySensor’s pruning rules."""
        if self._diaspora:
            return name not in HolidaySensor.EY_ONLY_ATTRS
        return name not in HolidaySensor.DIASPORA_ONLY_ATTRS

    def _allowed_names(self) -> List[str]:
        return [n for n in HolidaySensor.ALL_HOLIDAYS if self._attr_allowed_in_mode(n)]

    def _filter_labels_for_mode(self, labels: List[str]) -> List[str]:
        return [lbl for lbl in labels if lbl in HolidaySensor.ALL_HOLIDAYS and self._attr_allowed_in_mode(lbl)]

    # ─────────────────────────── HA lifecycle ───────────────────────────

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Initial update (thread-safe schedule; HA will call async_update + write)
        self.async_schedule_update_ha_state(True)

        # Periodic refresh (HA-managed, no threads)
        self._unsub_interval = async_track_time_interval(
            self.hass, self._handle_interval, timedelta(minutes=self._interval)
        )
        self.async_on_remove(self._unsub_interval)

        # Snap update at 00:02 local
        self._unsub_midnight = async_track_time_change(
            self.hass, self._handle_midnight, hour=0, minute=2, second=0
        )
        self.async_on_remove(self._unsub_midnight)

    @callback
    def _handle_interval(self, now) -> None:
        self.async_schedule_update_ha_state(True)

    @callback
    def _handle_midnight(self, now) -> None:
        self.async_schedule_update_ha_state(True)

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> Dict[str, object]:
        return self._attr_extra_state_attributes
        
    async def _motzaei_label_active_now(self, lbl: str, now: dt.datetime, tz: ZoneInfo) -> bool:
        """
        True iff we are currently inside the Motzaei window for this motzaei label,
        using MGA-72 alos for the end.
        """
        if not lbl.startswith("מוצאי "):
            return False

        geo = await get_geo(self.hass)
        if not geo:
            return False

        today = now.date()
        yesterday = today - timedelta(days=1)

        dias = self._diaspora

        # Matchers for each motzaei label
        def match_yk(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 7 and hd.day == 10

        def match_pesach(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 1 and hd.day == (22 if dias else 21)

        def match_sukkos(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 7 and hd.day == (23 if dias else 22)

        def match_shavuos(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 3 and hd.day == (7 if dias else 6)

        def match_rh(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 7 and hd.day == 2

        def match_17tammuz(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            y = hd.year
            d17 = PHebrewDate(y, 4, 17).to_pydate()
            observed = d17 if d17.weekday() != 5 else (d17 + timedelta(days=1))
            return d == observed

        def match_9av(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            y = hd.year
            d9 = PHebrewDate(y, 5, 9).to_pydate()
            observed = d9 if d9.weekday() != 5 else (d9 + timedelta(days=1))
            return d == observed

        def match_lag(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            return hd.month == 2 and hd.day == 18 and d.weekday() != 5  # no motzaei if Shabbos

        def match_chanukah(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            first_day = PHebrewDate(hd.year, 9, 25).to_pydate()
            last_day = first_day + timedelta(days=7)
            return d == last_day and d.weekday() != 5  # no distinct motzaei if day 8 is Shabbos

        def match_shushan(d: dt.date) -> bool:
            hd = PHebrewDate.from_pydate(d)
            target_month = 13 if _is_hebrew_leap(hd.year) else 12
            d15 = PHebrewDate(hd.year, target_month, 15).to_pydate()
            observed = d15 if d15.weekday() != 5 else (d15 + timedelta(days=1))
            return d == observed and d.weekday() != 5

        matcher_map = {
            "מוצאי יום הכיפורים": match_yk,
            "מוצאי פסח": match_pesach,
            "מוצאי סוכות": match_sukkos,
            "מוצאי שבועות": match_shavuos,
            "מוצאי ראש השנה": match_rh,
            "מוצאי צום שבעה עשר בתמוז": match_17tammuz,
            "מוצאי תשעה באב": match_9av,
            "מוצאי ל\"ג בעומר": match_lag,
            "מוצאי שושן פורים": match_shushan,
            "מוצאי חנוכה": match_chanukah,
            # safety alias if you ever emit this label:
            "מוצאי זאת חנוכה": match_chanukah,
        }

        matcher = matcher_map.get(lbl)
        if matcher is None:
            return False

        # Determine holiday_date (= today or yesterday) exactly like MotzeiHolidaySensor
        holiday_date: dt.date | None = None
        if matcher(today):
            holiday_date = today
        elif matcher(yesterday):
            holiday_date = yesterday
        else:
            return False

        # Motzaei window
        sunset_hol = ZmanimCalendar(geo_location=geo, date=holiday_date).sunset().astimezone(tz)
        motzei_start = _round_ceil(sunset_hol + timedelta(minutes=self._havdalah_offset))

        motzei_end = _alos_mga_72_for_date(geo, holiday_date + timedelta(days=1), tz)

        # Shabbos blocking (Fri candles → Shabbos havdalah)
        off_from_fri = (holiday_date.weekday() - 4) % 7
        fri = holiday_date - timedelta(days=off_from_fri)
        sat = fri + timedelta(days=1)

        fri_sunset = ZmanimCalendar(geo_location=geo, date=fri).sunset().astimezone(tz)
        sat_sunset = ZmanimCalendar(geo_location=geo, date=sat).sunset().astimezone(tz)

        shabbos_start = fri_sunset - timedelta(minutes=self._candle_offset)
        shabbos_end = _round_ceil(sat_sunset + timedelta(minutes=self._havdalah_offset))

        shabbos_blocks = shabbos_start <= motzei_start <= shabbos_end

        return (motzei_start <= now < motzei_end) and not shabbos_blocks

    # ─────────────────────────── Core update ───────────────────────────

    async def async_update(self, now: Optional[dt.datetime] = None) -> None:
        if self.hass is None:
            return

        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or dt.datetime.now(tz)

        # 1) Today's halachic date (flip at sunset + havdalah offset)
        hal_today = await self._halachic_date_for(now, tz)

        # 2) Find the block start (D0) — prefers current block if we're inside it
        d0_info = await self._find_block_start(hal_today, tz)
        if not d0_info:
            # No block in horizon → show Shovavim (and Shovavim T"T) if applicable
            flags = dict(self._all_flags_template)

            added = False
            for k in range(0, self._lookahead_days + 1):
                d = hal_today + timedelta(days=k)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("שובבים") and "שובבים" in flags:
                    flags["שובבים"] = True
                    added = True
                if a.get("שובבים ת\"ת") and "שובבים ת\"ת" in flags:
                    flags["שובבים ת\"ת"] = True
                    added = True

            state = self._state_from_flags(flags) if added else ""
            self._publish(state=state, flags=flags, meta={"lookahead_days": self._lookahead_days})
            return

        base, d0_date, _d0_hd = d0_info

        # 3) Phase (halachic days relative to D0)
        phase_days = await self._halachic_day_delta(d0_date, now, tz)

        # 4) Buckets for this block (offset → labels), including dynamic Shabbos helpers
        buckets = await self._buckets_for_block(base, d0_date, tz)

        # 5) Which buckets are enabled now (progressive window)
        #    Keep offsets so we can gate Motzaei-* labels by their real-time window.
        if phase_days <= -3:
            min_off, max_off = 1, 0  # empty range
        elif phase_days == -2:
            min_off, max_off = -2, 0
        elif phase_days == -1:
            min_off, max_off = -1, 0 + self._lookahead_days
        else:
            min_off, max_off = phase_days, phase_days + self._lookahead_days

        enabled_pairs: List[Tuple[int, str]] = []
        for off, labels in buckets:
            if min_off <= off <= max_off:
                for lbl in labels:
                    enabled_pairs.append((off, lbl))

        # 6) Build flags (booleans) from mode-pruned template.
        #    Motzaei-* flags for the current/past halachic day should only be True
        #    if we're still inside their Motzaei window (havdalah → alos).
        flags = dict(self._all_flags_template)  # False copy
        for off, lbl in enabled_pairs:
            if lbl.startswith("מוצאי ") and off <= phase_days:
                if not await self._motzaei_label_active_now(lbl, now, tz):
                    continue
            if lbl in flags:
                flags[lbl] = True

        # 7) Aggregates pre-activate as well (respecting mode)
        def _any_true(keys: List[str]) -> bool:
            return any(flags.get(k, False) for k in keys if k in flags)

        # Sukkos aggregates
        sukkos_days = [
            "סוכות א׳", "סוכות ב׳",
            "א׳ דחול המועד סוכות", "ב׳ דחול המועד סוכות",
            "ג׳ דחול המועד סוכות", "ד׳ דחול המועד סוכות",
            "הושענא רבה",
        ]
        if _any_true(sukkos_days):
            if "סוכות" in flags: 
                flags["סוכות"] = True
            if "סוכות (כל חג)" in flags:
                flags["סוכות (כל חג)"] = True

        # CH"M only (stops before הושענא רבה)
        if _any_true(sukkos_days[2:6]) and "חול המועד סוכות" in flags:
            flags["חול המועד סוכות"] = True

        # Pesach aggregates
        pesach_days = [
            "פסח א׳", "פסח ב׳",
            "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח", "ג׳ דחול המועד פסח", "ד׳ דחול המועד פסח",
            "שביעי של פסח", "אחרון של פסח",
        ]
        if _any_true(pesach_days):
            if "פסח" in flags:
                flags["פסח"] = True
            if "פסח (כל חג)" in flags:
                flags["פסח (כל חג)"] = True

        if _any_true(pesach_days[2:6]) and "חול המועד פסח" in flags:
            flags["חול המועד פסח"] = True

        # 8) Build state from the flags that are true (cap length for HA)
        state_summary = self._state_from_flags(flags)

        # 9) Publish (meta only includes lookahead_days)
        self._publish(
            state=state_summary,
            flags=flags,
            meta={"lookahead_days": self._lookahead_days},
        )

    def _publish(self, *, state: str, flags: Dict[str, bool], meta: Dict[str, object]) -> None:
        attrs: Dict[str, object] = {}
        attrs.update(flags)
        attrs.update(meta)  # only {'lookahead_days': 2}
        self._attr_native_value = state
        self._attr_extra_state_attributes = attrs

    # ───────────────────── Helpers: halachic day math ─────────────────────

    async def _halachic_date_for(self, now: dt.datetime, tz: ZoneInfo) -> dt.date:
        """
        Halachic day: flips at sunset + havdalah offset, with the havdalah time
        rounded up to the next minute (to match Holiday/DayType/Date sensors).
        """
        geo = await get_geo(self.hass)
        cal = ZmanimCalendar(geo_location=geo, date=now.date())
        sunset_raw = cal.sunset().astimezone(tz)
        havdalah_raw = sunset_raw + timedelta(minutes=self._havdalah_offset)

        # Use the same rounding convention as HolidaySensor/DayType: Motzei = ceil.
        havdalah_cut = _round_ceil(havdalah_raw)

        return now.date() + (timedelta(days=1) if now >= havdalah_cut else timedelta(days=0))
        
    async def _halachic_day_delta(
        self,
        d0_date: dt.date,
        now: dt.datetime,
        tz: ZoneInfo,
    ) -> int:
        """
        How many halachic days from D0 to 'now' (negative means we're before D0).

        Uses the same halachic-day flip as _halachic_date_for (sunset + havdalah,
        with Motzei rounded up to the next minute).
        """
        today_hal = await self._halachic_date_for(now, tz)
        return (today_hal - d0_date).days

    # ───────────────────── Helpers: simulate + detect ─────────────────────

    async def _simulate_attrs_at_midnight(self, date_: dt.date, tz: ZoneInfo) -> Dict[str, object]:
        """
        Run HolidaySensor at local 12:02 AM for the given civil date and return its attributes.
        (This keeps evaluation consistent with your midnight-style logic.)
        """
        sim = HolidaySensor(self.hass, self._candle_offset, self._havdalah_offset)
        fake_now = dt.datetime.combine(date_, dt.time(0, 2, 0, tzinfo=tz))
        await sim.async_update(fake_now)
        return dict(sim.extra_state_attributes or {})

    async def _simulate_attrs_at(self, date_: dt.date, tz: ZoneInfo, *, hour: int, minute: int) -> Dict[str, object]:
        sim = HolidaySensor(self.hass, self._candle_offset, self._havdalah_offset)
        fake_now = dt.datetime.combine(date_, dt.time(hour, minute, 0, tzinfo=tz))
        await sim.async_update(fake_now)
        return dict(sim.extra_state_attributes or {})

    async def _find_block_start(self, start_hal_day: dt.date, tz: ZoneInfo) -> Optional[Tuple[str, dt.date, "PHebrewDate"]]:
        from pyluach.hebrewcal import HebrewDate as PHebrewDate
        
        # 0) Snapshot "where we are" at halachic midnight of the current halachic day
        today_attrs = await self._simulate_attrs_at_midnight(start_hal_day, tz)

        # If we're in any Motzaei-* window, attach back to its parent block
        if today_attrs.get("מוצאי חנוכה"):
            for back in range(1, 10):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                prev = await self._simulate_attrs_at_midnight(d - timedelta(days=1), tz)
                if a.get("חנוכה") and not prev.get("חנוכה"):
                    return "חנוכה", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי פסח"):
            for back in range(1, 15):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("פסח א׳"):
                    return "פסח", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי סוכות"):
            for back in range(1, 5):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("שמיני עצרת") or a.get("שמיני עצרת/שמחת תורה"):
                    return "שמיני עצרת", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי שבועות"):
            for back in range(1, 5):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("שבועות א׳"):
                    return "שבועות", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי ראש השנה"):
            for back in range(1, 5):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("ראש השנה א׳"):
                    return "ראש השנה", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי יום הכיפורים"):
            d = start_hal_day - timedelta(days=1)
            return "יום הכיפורים", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי צום שבעה עשר בתמוז"):
            for back in range(1, 3):
                d = start_hal_day - timedelta(days=back)
                a_mid  = await self._simulate_attrs_at_midnight(d, tz)
                a_noon = await self._simulate_attrs_at(d, tz, hour=12, minute=0)
                if a_mid.get("צום שבעה עשר בתמוז") or a_noon.get("צום שבעה עשר בתמוז"):
                    return "צום שבעה עשר בתמוז", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי תשעה באב"):
            for back in range(1, 3):
                d = start_hal_day - timedelta(days=back)
                a_mid  = await self._simulate_attrs_at_midnight(d, tz)
                a_noon = await self._simulate_attrs_at(d, tz, hour=12, minute=0)
                if a_mid.get("תשעה באב נדחה") or a_noon.get("תשעה באב נדחה"):
                    return "תשעה באב נדחה", d, PHebrewDate.from_pydate(d)
                if a_mid.get("תשעה באב") or a_noon.get("תשעה באב"):
                    return "תשעה באב", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("מוצאי ל\"ג בעומר"):
            for back in range(1, 3):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("ל\"ג בעומר"):
                    return "ל\"ג בעומר", d, PHebrewDate.from_pydate(d)

        # Motzaei Shushan Purim belongs to the Purim block.
        # In Purim Meshulash years (15 Adar on Shabbos), this still maps back to Friday Purim.
        if today_attrs.get("מוצאי שושן פורים"):
            for back in range(1, 4):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                prev = await self._simulate_attrs_at_midnight(d - timedelta(days=1), tz)
                if a.get("פורים") and not prev.get("פורים"):
                    return "פורים", d, PHebrewDate.from_pydate(d)

        # Handle Isru-Chag days by mapping back to the prior block's D0
        if today_attrs.get("אסרו חג סוכות"):
            for back in (1, 2, 3):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("שמיני עצרת"):
                    return "שמיני עצרת", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("אסרו חג פסח"):
            for back in range(1, 10):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("פסח א׳"):
                    return "פסח", d, PHebrewDate.from_pydate(d)

        if today_attrs.get("אסרו חג שבועות"):
            for back in (1, 2, 3):
                d = start_hal_day - timedelta(days=back)
                a = await self._simulate_attrs_at_midnight(d, tz)
                if a.get("שבועות א׳"):
                    return "שבועות", d, PHebrewDate.from_pydate(d)

        def in_sukkos(a: dict) -> bool:
            return any(a.get(k) for k in [
                "סוכות א׳", "סוכות ב׳",
                "א׳ דחול המועד סוכות","ב׳ דחול המועד סוכות","ג׳ דחול המועד סוכות","ד׳ דחול המועד סוכות",
                "הושענא רבה",
            ])

        def in_pesach(a: dict) -> bool:
            return any(a.get(k) for k in [
                "פסח א׳","פסח ב׳",
                "א׳ דחול המועד פסח","ב׳ דחול המועד פסח","ג׳ דחול המועד פסח","ד׳ דחול המועד פסח",
                "שביעי של פסח","אחרון של פסח",
            ])

        def in_shavuos(a: dict) -> bool:
            return any(a.get(k) for k in ["שבועות א׳","שבועות ב׳"])

        def in_rh(a: dict) -> bool:
            return any(a.get(k) for k in ["ראש השנה א׳","ראש השנה ב׳"])

        def in_chanukah(a: dict) -> bool:
            return bool(a.get("חנוכה"))

        if in_sukkos(today_attrs):
            for back in range(0, 10):
                d = start_hal_day - timedelta(days=back)
                attrs = await self._simulate_attrs_at_midnight(d, tz)
                if attrs.get("סוכות א׳"):
                    return "סוכות", d, PHebrewDate.from_pydate(d)

        if in_pesach(today_attrs):
            for back in range(0, 10):
                d = start_hal_day - timedelta(days=back)
                attrs = await self._simulate_attrs_at_midnight(d, tz)
                if attrs.get("פסח א׳"):
                    return "פסח", d, PHebrewDate.from_pydate(d)

        if in_shavuos(today_attrs):
            for back in range(0, 5):
                d = start_hal_day - timedelta(days=back)
                attrs = await self._simulate_attrs_at_midnight(d, tz)
                if attrs.get("שבועות א׳"):
                    return "שבועות", d, PHebrewDate.from_pydate(d)

        if in_rh(today_attrs):
            for back in range(0, 5):
                d = start_hal_day - timedelta(days=back)
                attrs = await self._simulate_attrs_at_midnight(d, tz)
                if attrs.get("ראש השנה א׳"):
                    return "ראש השנה", d, PHebrewDate.from_pydate(d)

        if in_chanukah(today_attrs):
            for back in range(0, 10):
                d = start_hal_day - timedelta(days=back)
                attrs = await self._simulate_attrs_at_midnight(d, tz)
                prev = await self._simulate_attrs_at_midnight(d - timedelta(days=1), tz)
                if attrs.get("חנוכה") and not prev.get("חנוכה"):
                    return "חנוכה", d, PHebrewDate.from_pydate(d)

        # 1) Otherwise, scan forward for the next block
        for k in range(0, self._horizon_days + 1):
            d = start_hal_day + timedelta(days=k)
            attrs = await self._simulate_attrs_at_midnight(d, tz)

            # Two-day with chol
            if attrs.get("סוכות א׳"):
                return "סוכות", d, PHebrewDate.from_pydate(d)
            if attrs.get("פסח א׳"):
                return "פסח", d, PHebrewDate.from_pydate(d)

            # Two-day without chol
            if attrs.get("שבועות א׳"):
                return "שבועות", d, PHebrewDate.from_pydate(d)
            if attrs.get("ראש השנה א׳"):
                return "ראש השנה", d, PHebrewDate.from_pydate(d)

            # Last days of Sukkos
            if attrs.get("שמיני עצרת"):
                return "שמיני עצרת", d, PHebrewDate.from_pydate(d)
            if attrs.get("שמחת תורה"):
                prev_attrs = await self._simulate_attrs_at_midnight(d - timedelta(days=1), tz)
                if not prev_attrs.get("שמיני עצרת"):
                    return "שמחת תורה", d, PHebrewDate.from_pydate(d)

            # Yom Kippur
            if attrs.get("יום הכיפורים"):
                return "יום הכיפורים", d, PHebrewDate.from_pydate(d)

            # Chanukah
            if attrs.get("חנוכה"):
                prev_attrs = await self._simulate_attrs_at_midnight(d - timedelta(days=1), tz)
                if not prev_attrs.get("חנוכה"):
                    return "חנוכה", d, PHebrewDate.from_pydate(d)

            # Purim / Shushan
            if attrs.get("פורים"):
                return "פורים", d, PHebrewDate.from_pydate(d)
            if attrs.get("שושן פורים"):
                return "שושן פורים", d, PHebrewDate.from_pydate(d)

            # Fasts (00:02 may be before dawn → also recheck at midday)
            fasts = ("צום גדליה", "צום עשרה בטבת", "צום שבעה עשר בתמוז", "תשעה באב", "תשעה באב נדחה", "תענית אסתר")

            # First try at 00:02 local
            if any(attrs.get(f) for f in fasts):
                for f in fasts:
                    if attrs.get(f):
                        return f, d, PHebrewDate.from_pydate(d)

            # Minor fasts won't be true at 00:02 → try noon
            attrs_noon = await self._simulate_attrs_at(d, tz, hour=12, minute=0)
            if any(attrs_noon.get(f) for f in fasts):
                for f in fasts:
                    if attrs_noon.get(f):
                        return f, d, PHebrewDate.from_pydate(d)

            # Singles
            for single in ('ט"ו בשבט', "פסח שני", 'ל"ג בעומר', "זאת חנוכה"):
                if attrs.get(single):
                    return single, d, PHebrewDate.from_pydate(d)

        return None

    # ───────────────────── Helpers: buckets + phasing ─────────────────────

    async def _buckets_for_block(self, base: str, d0: dt.date, tz: ZoneInfo) -> List[Tuple[int, List[str]]]:
        """
        Return an ordered list of (offset, [labels]) describing the block, relative to D0 (Day1).
        """
        buckets: List[Tuple[int, List[str]]] = []

        if base == "סוכות":
            buckets = [
                (-1, ["ערב סוכות"]),
                (0,  ["סוכות א׳", "סוכות א׳ וב׳"]),
                (+1, ["סוכות ב׳", "סוכות א׳ וב׳"]),  # keep combined visible on Day 2
                (+2, ["א׳ דחול המועד סוכות"]),
                (+3, ["ב׳ דחול המועד סוכות"]),
                (+4, ["ג׳ דחול המועד סוכות"]),
                (+5, ["ד׳ דחול המועד סוכות"]),
                (+6, ["הושענא רבה"]),
            ]

        elif base == "שמיני עצרת":
            # diaspora-aware Asru & Motzaei day
            asru_sukkos_offset = 2 if self._diaspora else 1
            buckets = [
                (0,  ["שמיני עצרת", "שמיני עצרת/שמחת תורה"]),
                (+1, ["שמחת תורה", "שמיני עצרת/שמחת תורה"]),
                (asru_sukkos_offset, ["אסרו חג סוכות", "מוצאי סוכות"]),
            ]

        elif base == "פסח":
            asru_pesach_offset = 8 if self._diaspora else 7  # 23 Nisan vs 22 in EY
            buckets = [
                (-1, ["ערב פסח"]),
                (0,  ["פסח א׳", "פסח א׳ וב׳"]),
                (+1, ["פסח ב׳", "פסח א׳ וב׳"]),
                (+2, ["א׳ דחול המועד פסח"]),
                (+3, ["ב׳ דחול המועד פסח"]),
                (+4, ["ג׳ דחול המועד פסח"]),
                (+5, ["ד׳ דחול המועד פסח"]),
                (+6, ["שביעי של פסח", "שביעי/אחרון של פסח"]),  # (pruned in EY)
                (+7, ["אחרון של פסח", "שביעי/אחרון של פסח"]),   # (pruned in EY)
                (asru_pesach_offset, ["אסרו חג פסח", "מוצאי פסח"]),
            ]
            
        elif base == "ל\"ג בעומר":
            buckets = [
                (0, ["ל\"ג בעומר"]),
                (1, ["מוצאי ל\"ג בעומר"]),
            ]

        elif base == "שבועות":
            asru_shavuos_offset = 2 if self._diaspora else 1
            buckets = [
                (-1, ["ערב שבועות"]),
                (0,  ["שבועות א׳", "שבועות א׳ וב׳"]),
                (+1, ["שבועות ב׳", "שבועות א׳ וב׳"]),
                (asru_shavuos_offset, ["אסרו חג שבועות", "מוצאי שבועות"]),
            ]

        elif base == "ראש השנה":
            buckets = [
                (-1, ["ערב ראש השנה"]),
                (0,  ["ראש השנה א׳", "ראש השנה א׳ וב׳"]),
                (+1, ["ראש השנה ב׳", "ראש השנה א׳ וב׳"]),
                (+2, ["מוצאי ראש השנה"]),
            ]

        elif base == "יום הכיפורים":
            buckets = [
                (-1, ["ערב יום כיפור"]),
                (0,  ["יום הכיפורים"]),
                (+1, ["מוצאי יום הכיפורים"]),
            ]
        elif base == "חנוכה":
            last_day = d0 + timedelta(days=7)           # day 8 civil date
            motzei_exists = (last_day.weekday() != 5)  # no distinct motzaei if day 8 is Shabbos

            buckets = [
                (-1, ["ערב חנוכה"]),
                (0,  ["חנוכה", "א׳ דחנוכה"]),
                (1,  ["חנוכה", "ב׳ דחנוכה"]),
                (2,  ["חנוכה", "ג׳ דחנוכה"]),
                (3,  ["חנוכה", "ד׳ דחנוכה"]),
                (4,  ["חנוכה", "ה׳ דחנוכה"]),
                (5,  ["חנוכה", "ו׳ דחנוכה"]),
                (6,  ["חנוכה", "ז׳ דחנוכה"]),
                (7,  ["חנוכה", "זאת חנוכה"]),  # treat this as day 8 (unless you add "ח׳ דחנוכה")
            ]
            if motzei_exists:
                buckets.append((8, ["מוצאי חנוכה"]))
                
        elif base in ("תשעה באב", "תשעה באב נדחה"):
            from pyluach.hebrewcal import HebrewDate as PHebrewDate
            hd0 = PHebrewDate.from_pydate(d0)
            is_deferred = (hd0.month == 5 and hd0.day == 10)
            day0_labels = ["תשעה באב", "תשעה באב נדחה"] if is_deferred else ["תשעה באב"]
            buckets = [
                (-1, ["ערב תשעה באב"]),
                (0,  day0_labels),
                (+1, ["מוצאי תשעה באב"]),
            ]

        elif base == "צום שבעה עשר בתמוז":
            buckets = [
                (0,  ["צום שבעה עשר בתמוז"]),
                (+1, ["מוצאי צום שבעה עשר בתמוז"]),
            ]
        elif base == "פורים":
            # Pre-day is Ta'anit Esther, then Purim
            buckets = [
                (-1, ["תענית אסתר"]),
                (0,  ["פורים"]),
                (1,  ["מוצאי שושן פורים"]),
            ]
        
        elif base == "שושן פורים":
            # No pre-fast before Shushan. Shushan stands alone as D0.
            buckets = [
                (0, ["שושן פורים"]),
            ]
        else:
            buckets = [(-1, []), (0, [base])]

        # Keep only labels known to HolidaySensor AND allowed in the current mode
        buckets = [(off, self._filter_labels_for_mode(labels)) for off, labels in buckets]

        # ---------------- Spill-forward into subsequent holidays ----------------
        if buckets:
            max_off = max(off for off, _ in buckets)
        else:
            max_off = 0

        for extra in range(1, self._lookahead_days + 1):
            off = max_off + extra
            day = d0 + timedelta(days=off)

            # 1) What’s true at 00:02?
            attrs_midnight = await self._simulate_attrs_at_midnight(day, tz)
            future_labels = [k for k, v in attrs_midnight.items() if v]

            # 2) Minor fasts won’t be true at 00:02 → recheck at noon and OR them in
            attrs_noon = await self._simulate_attrs_at(day, tz, hour=12, minute=0)
            for f in ("צום גדליה", "צום עשרה בטבת", "צום שבעה עשר בתמוז", "תענית אסתר", "תשעה באב", "תשעה באב נדחה"):
                if attrs_noon.get(f):
                    future_labels.append(f)

            # keep unique + mode-pruned
            future_labels = self._filter_labels_for_mode(list(dict.fromkeys(future_labels)))

            if not future_labels:
                continue

            already = set(lbl for _, labels in buckets for lbl in labels)
            future_labels = [lbl for lbl in future_labels if lbl not in already]
            if not future_labels:
                continue

            buckets.append((off, future_labels))

        # --- Dynamically add Shabbos/helper flags to the correct offsets (mode-pruned) ---
        def _append_to_offset(off_to_labels: Dict[int, List[str]], off: int, label: str) -> None:
            if label not in HolidaySensor.ALL_HOLIDAYS or not self._attr_allowed_in_mode(label):
                return
            if off in off_to_labels:
                if label not in off_to_labels[off]:
                    off_to_labels[off].append(label)
            else:
                off_to_labels[off] = [label]

        off_to_labels: Dict[int, List[str]] = {off: list(labels) for off, labels in buckets}

        # --- Add ליל בדיקת חמץ once (for years when Pesach is Motzaei Shabbos) ---
        if base == "פסח":
            attrs_m2 = await self._simulate_attrs_at_midnight(d0 - timedelta(days=2), tz)
            if attrs_m2.get("ליל בדיקת חמץ"):
                # Attach to -2 so it only appears two halachic days before D0
                _append_to_offset(off_to_labels, -2, "ליל בדיקת חמץ")

        for off in sorted(off_to_labels.keys()):
            day = d0 + timedelta(days=off)
            attrs_mid = await self._simulate_attrs_at_midnight(day, tz)

            if base == "סוכות" and attrs_mid.get("שבת חול המועד סוכות"):
                _append_to_offset(off_to_labels, off, "שבת חול המועד סוכות")

            if base == "פסח" and attrs_mid.get("שבת חול המועד פסח"):
                _append_to_offset(off_to_labels, off, "שבת חול המועד פסח")

            if attrs_mid.get("שבת ראש חודש"):
                _append_to_offset(off_to_labels, off, "שבת ראש חודש")

            if attrs_mid.get("עשרת ימי תשובה"):
                _append_to_offset(off_to_labels, off, "עשרת ימי תשובה")

            if attrs_mid.get("שובבים"):
                _append_to_offset(off_to_labels, off, "שובבים")
            if attrs_mid.get("שובבים ת\"ת"):
                _append_to_offset(off_to_labels, off, "שובבים ת\"ת")

            for lbl in ("ערב שבת חנוכה", "שבת חנוכה", "שבת חנוכה ראש חודש"):
                if attrs_mid.get(lbl):
                    _append_to_offset(off_to_labels, off, lbl)

            for lbl in ("שבת ערב פסח", "ערב פסח מוקדם"):
                if attrs_mid.get(lbl):
                    _append_to_offset(off_to_labels, off, lbl)

        buckets = [(off, off_to_labels[off]) for off in sorted(off_to_labels.keys())]
        return buckets

    def _labels_enabled_for_phase(self, buckets: List[Tuple[int, List[str]]], phase_days: int) -> List[str]:
        """
        Progressive window (no past carryover):
          phase <= -3 : nothing
          phase == -2 : show offsets [-1 ..  0]
          phase == -1 : show offsets [-1 .. +2]
          phase >=  0 : show offsets [ p .. p+LOOKAHEAD_DAYS]
        """
        if not buckets:
            return []

        if phase_days <= -3:
            return []

        if phase_days == -2:
            min_off, max_off = -2, 0
        elif phase_days == -1:
            min_off, max_off = -1, 0 + self._lookahead_days
        else:
            min_off, max_off = phase_days, phase_days + self._lookahead_days

        enabled: List[str] = []
        for off, labels in buckets:
            if min_off <= off <= max_off:
                enabled.extend(labels)
        return enabled

    # ───────────────────── State string helper ─────────────────────

    def _state_from_flags(self, flags: Dict[str, bool]) -> str:
        """
        Compose a readable state string from the flags that are true.
        Keep Home Assistant's 255-char state limit in mind: show up to ~6 items, then "+N".
        """
        trues = [k for k, v in flags.items() if v]
        if not trues:
            return ""
        order_index = {name: i for i, name in enumerate(HolidaySensor.ALL_HOLIDAYS)}
        trues = [k for k in trues if k in order_index]
        trues.sort(key=lambda k: order_index.get(k, 10_000))

        head = trues[:6]
        more = len(trues) - len(head)
        base = ", ".join(head)
        return f"{base} (+{more})" if more > 0 else base
