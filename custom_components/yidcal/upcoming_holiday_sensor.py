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
from homeassistant.helpers.event import async_track_time_change

from zmanim.zmanim_calendar import ZmanimCalendar
from .zman_sensors import get_geo

from .device import YidCalDevice
from .holiday_sensor import HolidaySensor

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

        # Base of all boolean flags (False)
        self._all_flags_template: Dict[str, bool] = {name: False for name in HolidaySensor.ALL_HOLIDAYS}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()
        # periodic refresh
        self._register_interval(self.hass, self.async_update, timedelta(minutes=self._interval))
        # snap update at 00:02 local every day
        async_track_time_change(
            self.hass,
            lambda *_: self.hass.async_create_task(self.async_update()),
            hour=0, minute=2, second=0,
        )

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> Dict[str, object]:
        return self._attr_extra_state_attributes

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
            # No block in horizon → publish all flags False; attr only lookahead_days
            flags = dict(self._all_flags_template)
            self._publish(
                state="",
                flags=flags,
                meta={"lookahead_days": self._lookahead_days},
            )
            return

        base, d0_date, _d0_hd = d0_info  # d0_hd not needed anymore

        # 3) Phase (halachic days relative to D0)
        phase_days = await self._halachic_day_delta(d0_date, now, tz)

        # 4) Buckets for this block (offset → labels), including dynamic Shabbos helpers
        buckets = await self._buckets_for_block(base, d0_date, tz)

        # 5) Which buckets are enabled now (progressive window: today..today+2; special rules for D0-1, D0-2)
        enabled_labels = self._labels_enabled_for_phase(buckets, phase_days)

        # 6) Build flags (booleans)
        flags = dict(self._all_flags_template)  # False copy
        for lbl in enabled_labels:
            if lbl in flags:
                flags[lbl] = True

        # 7) Aggregates pre-activate as well
        def _any_true(keys: List[str]) -> bool:
            return any(flags.get(k, False) for k in keys)

        # Sukkos aggregates
        sukkos_days = [
            "סוכות א׳", "סוכות ב׳",
            "א׳ דחול המועד סוכות", "ב׳ דחול המועד סוכות",
            "ג׳ דחול המועד סוכות", "ד׳ דחול המועד סוכות",
            "הושענא רבה",
        ]
        if _any_true(sukkos_days):
            flags["סוכות"] = True
        if _any_true(sukkos_days[2:6]):  # CHM only (stops before הושענא רבה)
            flags["חול המועד סוכות"] = True

        # Pesach aggregates
        pesach_days = [
            "פסח א׳", "פסח ב׳",
            "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח", "ג׳ דחול המועד פסח", "ד׳ דחול המועד פסח",
            "שביעי של פסח", "אחרון של פסח",
        ]
        if _any_true(pesach_days):
            flags["פסח"] = True
        if _any_true(pesach_days[2:6]):  # CHM subset
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
        """Halachic day: flips at sunset + havdalah offset (hard-coded)."""
        geo = await get_geo(self.hass)
        cal = ZmanimCalendar(geo_location=geo, date=now.date())
        sunset = cal.sunset().astimezone(tz)
        havdalah_cut = sunset + timedelta(minutes=self._havdalah_offset)
        return now.date() + (timedelta(days=1) if now >= havdalah_cut else timedelta(days=0))

    async def _halachic_day_delta(self, d0_date: dt.date, now: dt.datetime, tz: ZoneInfo) -> int:
        """How many halachic days from D0 to 'now' (negative means we're before D0)."""
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

    async def _find_block_start(self, start_hal_day: dt.date, tz: ZoneInfo) -> Optional[Tuple[str, dt.date, "PHebrewDate"]]:
        from pyluach.hebrewcal import HebrewDate as PHebrewDate

        # 0) If *inside* a block, walk backward to Day 1 and use that as D0.
        today_attrs = await self._simulate_attrs_at_midnight(start_hal_day, tz)

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

            # Fasts
            for fast in ("צום גדליה", "צום עשרה בטבת", "צום שבעה עשר בתמוז", "תשעה באב", "תשעה באב נדחה", "תענית אסתר"):
                if attrs.get(fast):
                    return fast, d, PHebrewDate.from_pydate(d)

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
                (0, ["סוכות א׳", "סוכות א׳ וב׳"]),
                (+1, ["סוכות ב׳"]),
                (+2, ["א׳ דחול המועד סוכות"]),
                (+3, ["ב׳ דחול המועד סוכות"]),
                (+4, ["ג׳ דחול המועד סוכות"]),
                (+5, ["ד׳ דחול המועד סוכות"]),
                (+6, ["הושענא רבה"]),
            ]
        elif base == "שמיני עצרת":
            # treat last days as a 2-day chag for pre-activation
            buckets = [
                (0, ["שמיני עצרת", "שמיני עצרת/שמחת תורה"]),
                (+1, ["שמחת תורה"]),
            ]
        elif base == "פסח":
            buckets = [
                (-1, ["ערב פסח"]),
                (0, ["פסח א׳", "פסח א׳ וב׳"]),
                (+1, ["פסח ב׳"]),
                (+2, ["א׳ דחול המועד פסח"]),
                (+3, ["ב׳ דחול המועד פסח"]),
                (+4, ["ג׳ דחול המועד פסח"]),
                (+5, ["ד׳ דחול המועד פסח"]),
                (+6, ["שביעי של פסח"]),
                (+7, ["אחרון של פסח"]),
            ]
        elif base == "שבועות":
            buckets = [
                (-1, ["ערב שבועות"]),
                (0, ["שבועות א׳", "שבועות א׳ וב׳"]),
                (+1, ["שבועות ב׳"]),
            ]
        elif base == "ראש השנה":
            buckets = [
                (-1, ["ערב ראש השנה"]),
                (0, ["ראש השנה א׳", "ראש השנה א׳ וב׳"]),
                (+1, ["ראש השנה ב׳"]),
            ]
        elif base == "יום הכיפורים":
            buckets = [
                (-1, ["ערב יום כיפור"]),
                (0, ["יום הכיפורים"]),
            ]
        elif base == "חנוכה":
            buckets = [
                (-1, ["ערב חנוכה"]),
                (0, ["חנוכה"]),
            ]
        elif base in ("פורים", "שושן פורים"):
            pre = ["תענית אסתר"]
            buckets = [(-1, pre), (0, [base])]
        else:
            buckets = [(-1, []), (0, [base])]

        # Keep only labels known to HolidaySensor
        buckets = [(off, [lbl for lbl in labels if lbl in HolidaySensor.ALL_HOLIDAYS]) for off, labels in buckets]
        
        # ---------------- Generic spill-forward into subsequent holidays ----------------
        # If lookahead pushes beyond the end of the current block, extend buckets by
        # simulating each following civil day (12:02 AM) and adding any holiday flags
        # that are true on those days. This lets the window light up the *next* block,
        # e.g., סוכות → שמיני עצרת/שמחת תורה, etc.
        if buckets:
            max_off = max(off for off, _ in buckets)
        else:
            max_off = 0

        # We only need to simulate up to our lookahead horizon beyond the last offset
        for extra in range(1, self._lookahead_days + 1):
            off = max_off + extra
            attrs_future = await self._simulate_attrs_at_midnight(d0 + timedelta(days=off), tz)
            # Keep only recognized holiday flags that are True
            future_labels = [k for k, v in attrs_future.items()
                             if v and k in HolidaySensor.ALL_HOLIDAYS]

            if not future_labels:
                continue

            # Deduplicate against anything already in earlier buckets
            already = set(lbl for _, labels in buckets for lbl in labels)
            future_labels = [lbl for lbl in future_labels if lbl not in already]
            if not future_labels:
                continue

            buckets.append((off, future_labels))

        # --- Dynamically add Shabbos helper flags to the correct offsets ---
        def _append_to_offset(off_to_labels: Dict[int, List[str]], off: int, label: str) -> None:
            if label not in HolidaySensor.ALL_HOLIDAYS:
                return
            if off in off_to_labels:
                if label not in off_to_labels[off]:
                    off_to_labels[off].append(label)
            else:
                off_to_labels[off] = [label]

        off_to_labels: Dict[int, List[str]] = {off: list(labels) for off, labels in buckets}

        for off in sorted(off_to_labels.keys()):
            day = d0 + timedelta(days=off)
            attrs_mid = await self._simulate_attrs_at_midnight(day, tz)

            if base == "סוכות" and attrs_mid.get("שבת חול המועד סוכות"):
                _append_to_offset(off_to_labels, off, "שבת חול המועד סוכות")

            if base == "פסח" and attrs_mid.get("שבת חול המועד פסח"):
                _append_to_offset(off_to_labels, off, "שבת חול המועד פסח")

            # Optional but useful across blocks
            if attrs_mid.get("שבת ראש חודש"):
                _append_to_offset(off_to_labels, off, "שבת ראש חודש")

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
            min_off, max_off = -1, 0
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
        # Simple, stable order: sort by appearance in ALL_HOLIDAYS to keep your familiar order
        order_index = {name: i for i, name in enumerate(HolidaySensor.ALL_HOLIDAYS)}
        trues = [k for k in trues if k in order_index]

        # cap length
        head = trues[:6]
        more = len(trues) - len(head)
        base = ", ".join(head)
        return f"{base} (+{more})" if more > 0 else base
