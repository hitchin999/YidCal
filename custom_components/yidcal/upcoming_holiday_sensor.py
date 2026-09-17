# upcoming_holiday_sensor.py
"""
UpcomingHolidaySensor for YidCal.

- Progressive pre-activation beginning *two halachic days* before the current/next block.
- Publishes ALL holiday flags as boolean attributes (same Hebrew keys you use),
  excluding the fast timer text attributes.
- Hard-coded halachic day flip at sunset + havdalah offset.
- Flags come from the holiday flag specs (halacha_events); point-in-time checks use 12:02 AM local.

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

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from .zman_sensors import get_geo
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import (
    round_ceil as _round_ceil,
    sunset_for_date,
)

from .device import YidCalDevice
from .holiday_sensor import HolidaySensor
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

class UpcomingHolidaySensor(YidCalDevice, RestoreEntity, SensorEntity):
    # Self-driven (minute guard + 15-min interval + midnight snap). Without
    # this, HA's default 30-second entity poller re-ran the full horizon
    # simulation twice a minute.
    _attr_should_poll = False

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

        # Crossing-check memory for the cheap minute guard.
        self._guard_last_now: Optional[dt.datetime] = None
        self._guard_hal_date: Optional[dt.date] = None

        # Base of all boolean flags (False), pruned to the current mode
        self._all_flags_template: Dict[str, bool] = {name: False for name in self._allowed_names()}
        # Rule option the flag specs need (יום כיפור קטן scope), as the holiday sensor reads it
        self._ykk_every_month: bool = bool(getattr(HolidaySensor(hass, candle_offset, havdalah_offset), "_ykk_every_month", False))
        self._geo = None
        self._day_cache: Dict[dt.date, set] = {}

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

        # Cheap minute guard: detects the halachic-date flip and manual
        # clock jumps within 60s; heavy horizon simulation runs only when
        # something actually changed.
        self._unsub_guard = async_track_time_change(
            self.hass, self._handle_minute_guard, second=30
        )
        self.async_on_remove(self._unsub_guard)

    async def _handle_minute_guard(self, now) -> None:
        try:
            tz = ZoneInfo(self.hass.config.time_zone)
            now_l = dt.datetime.now(tz)
            last = self._guard_last_now
            self._guard_last_now = now_l
            stepped = (
                last is None
                or now_l < last
                or (now_l - last) > timedelta(seconds=90)
            )
            hal = await self._halachic_date_for(now_l, tz)
            flipped = self._guard_hal_date is not None and hal != self._guard_hal_date
            civil_rolled = last is not None and now_l.date() != last.date()
            if stepped or flipped or civil_rolled:
                self.async_schedule_update_ha_state(True)
        except Exception:  # noqa: BLE001 — guard must never kill the tick
            pass

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
        True iff the מוצאי flag `lbl` is on right now: its own spec window in
        halacha_events.FLAG_SPECS, the one the holiday flag uses (a Yom Tov whose
        last day is Friday moves to Motzei Shabbos).
        """
        if lbl == "מוצאי זאת חנוכה":
            lbl = "מוצאי חנוכה"
        return lbl in he.FLAG_SPECS and lbl in self._spec_on_at(now)

    # ─────────────────────────── Core update ───────────────────────────

    async def async_update(self, now: Optional[dt.datetime] = None) -> None:
        if self.hass is None:
            return

        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or dt.datetime.now(tz)
        self._geo = await get_geo(self.hass)
        if not self._geo:
            return
        self._day_cache = {}

        # 1) Today's halachic date (flip at sunset + havdalah offset)
        hal_today = await self._halachic_date_for(now, tz)
        self._guard_hal_date = hal_today

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

        # 7) Aggregates (he.FLAG_AGGREGATES) pre-activate with their members
        for agg, members in he.FLAG_AGGREGATES.items():
            if agg in flags and any(flags.get(m, False) for m in members):
                flags[agg] = True

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
        # Convert bool attrs to lowercase strings for HA state condition compatibility
        attrs = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in attrs.items()}
        self._attr_native_value = state
        self._attr_extra_state_attributes = attrs

    # ───────────────────── Helpers: halachic day math ─────────────────────

    async def _halachic_date_for(self, now: dt.datetime, tz: ZoneInfo) -> dt.date:
        """
        Halachic day: flips at sunset + havdalah offset, with the havdalah time
        rounded up to the next minute (to match Holiday/DayType/Date sensors).
        """
        geo = await get_geo(self.hass)
        sunset_raw = sunset_for_date(geo=geo, tz=tz, base_date=now.date())
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

    # ───────────────────── Flags from the holiday flag specs ─────────────────────
    #
    # Everything here reads halacha_events.FLAG_SPECS / FLAG_AGGREGATES - the
    # definitions sensor.yidcal_holiday decides its flags from - so a label lands
    # on exactly the day (and a מוצאי on exactly the window) the holiday flag
    # does, in either mode.

    # base -> (the flag on the block's first day (D0), the block's labels, offsets spanned)
    _BLOCKS: Dict[str, Tuple[str, Tuple[str, ...], Tuple[int, int]]] = {
        "סוכות": ("סוכות א׳", (
            "ערב סוכות", "סוכות א׳", "סוכות ב׳", "סוכות א׳ וב׳",
            "א׳ דחול המועד סוכות", "ב׳ דחול המועד סוכות", "ג׳ דחול המועד סוכות",
            "ד׳ דחול המועד סוכות", "ה׳ דחול המועד סוכות", "הושענא רבה",
            "חול המועד סוכות", "סוכות (כל חג)", "א׳ דיום טוב", "ב׳ דיום טוב",
        ), (-1, 6)),
        "שמיני עצרת": ("שמיני עצרת", (
            "שמיני עצרת", "שמחת תורה", "שמיני עצרת/שמחת תורה",
            "אסרו חג סוכות", "מוצאי סוכות", "א׳ דיום טוב", "ב׳ דיום טוב",
        ), (0, 3)),
        "פסח": ("פסח א׳", (
            "ערב פסח", "ערב פסח מוקדם", "שבת ערב פסח", "פסח א׳", "פסח ב׳", "פסח א׳ וב׳",
            "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח", "ג׳ דחול המועד פסח",
            "ד׳ דחול המועד פסח", "ה׳ דחול המועד פסח", "חול המועד פסח",
            "שביעי של פסח", "אחרון של פסח", "שביעי/אחרון של פסח", "פסח (כל חג)",
            "אסרו חג פסח", "מוצאי פסח", "א׳ דיום טוב", "ב׳ דיום טוב",
        ), (-2, 9)),
        "שבועות": ("שבועות א׳", (
            "ערב שבועות", "שבת ערב שבועות", "שבועות א׳", "שבועות ב׳", "שבועות א׳ וב׳",
            "אסרו חג שבועות", "מוצאי שבועות", "א׳ דיום טוב", "ב׳ דיום טוב",
        ), (-2, 3)),
        "ראש השנה": ("ראש השנה א׳", (
            "ערב ראש השנה", "ראש השנה א׳", "ראש השנה ב׳", "ראש השנה א׳ וב׳",
            "מוצאי ראש השנה", "א׳ דיום טוב", "ב׳ דיום טוב",
        ), (-1, 3)),
        "יום הכיפורים": ("יום הכיפורים", ("ערב יום כיפור", "יום הכיפורים", "מוצאי יום הכיפורים"), (-1, 1)),
        "חנוכה": ("א׳ דחנוכה", (
            "ערב חנוכה", "חנוכה", "א׳ דחנוכה", "ב׳ דחנוכה", "ג׳ דחנוכה", "ד׳ דחנוכה",
            "ה׳ דחנוכה", "ו׳ דחנוכה", "ז׳ דחנוכה", "זאת חנוכה", "מוצאי חנוכה",
        ), (-1, 8)),
        "תשעה באב": ("תשעה באב", ("ערב תשעה באב", "תשעה באב", "תשעה באב נדחה", "מוצאי תשעה באב"), (-1, 1)),
        "צום שבעה עשר בתמוז": ("צום שבעה עשר בתמוז", ("צום שבעה עשר בתמוז", "מוצאי צום שבעה עשר בתמוז"), (0, 1)),
        "פורים": ("פורים", ("תענית אסתר", "פורים", "מוצאי שושן פורים"), (-1, 3)),
        "ל\"ג בעומר": ("ל\"ג בעומר", ("ל\"ג בעומר", "מוצאי ל\"ג בעומר"), (0, 1)),
        "שושן פורים": ("שושן פורים", ("שושן פורים",), (0, 0)),
        "צום גדליה": ("צום גדליה", ("צום גדליה",), (0, 0)),
        "צום עשרה בטבת": ("צום עשרה בטבת", ("צום עשרה בטבת",), (0, 0)),
        "תענית אסתר": ("תענית אסתר", ("תענית אסתר",), (0, 0)),
        "חמשה עשר בשבט": ("חמשה עשר בשבט", ("חמשה עשר בשבט",), (0, 0)),
        "פסח שני": ("פסח שני", ("פסח שני",), (0, 0)),
    }
    # Inside a block (its מוצאי / אסרו חג days included), checked in this order
    _IN_BLOCK_ORDER = (
        "חנוכה", "פסח", "שמיני עצרת", "שבועות", "ראש השנה", "יום הכיפורים",
        "צום שבעה עשר בתמוז", "תשעה באב", "ל\"ג בעומר", "פורים", "סוכות",
    )
    # The next block: the first day carrying a block's first-day flag, in this order
    _FORWARD_ORDER = (
        "סוכות", "פסח", "שבועות", "ראש השנה", "שמיני עצרת", "יום הכיפורים", "חנוכה",
        "פורים", "שושן פורים", "צום גדליה", "צום עשרה בטבת", "צום שבעה עשר בתמוז",
        "תשעה באב", "תענית אסתר", "חמשה עשר בשבט", "פסח שני", "ל\"ג בעומר",
    )

    def _spec_on_at(self, moment: dt.datetime) -> set:
        """Flags on at `moment` (aggregates included, before pruning to the mode)."""
        on = set(he.evaluate_flag_specs(
            now=moment, tz=moment.tzinfo, geo=self._geo, diaspora=self._diaspora,
            candle_offset=self._candle_offset, havdalah_offset=self._havdalah_offset,
            options={"every_month": self._ykk_every_month},
        ))
        return on | {agg for agg, members in he.FLAG_AGGREGATES.items() if on.intersection(members)}

    def _day_flags(self, day: dt.date) -> set:
        """Flags whose spec day is `day` (aggregates included, before pruning to the mode)."""
        flags = self._day_cache.get(day)
        if flags is None:
            options = {"every_month": self._ykk_every_month}
            flags = set()
            for name, (rule, _shape) in he.FLAG_SPECS.items():
                option = he.FLAG_RULE_OPTIONS.get(name)
                kwargs = {option: options[option]} if option else {}
                if rule(day, self._diaspora, **kwargs):
                    flags.add(name)
                    continue
                prev = day - timedelta(days=1)
                if rule(prev, self._diaspora, **kwargs):
                    shape_name = _shape(prev, self._diaspora) if callable(_shape) else _shape
                    if he.FLAG_SHAPES[shape_name][1][1] >= 1:  # the window runs into `day`
                        flags.add(name)
            flags |= {agg for agg, members in he.FLAG_AGGREGATES.items() if flags.intersection(members)}
            self._day_cache[day] = flags
        return flags

    async def _simulate_attrs_at_midnight(self, date_: dt.date, tz: ZoneInfo) -> Dict[str, object]:
        """The holiday flags as sensor.yidcal_holiday reads them at 12:02 AM on `date_`."""
        return await self._simulate_attrs_at(date_, tz, hour=0, minute=2)

    async def _simulate_attrs_at(self, date_: dt.date, tz: ZoneInfo, *, hour: int, minute: int) -> Dict[str, object]:
        """The holiday flags as sensor.yidcal_holiday reads them at that moment (mode-pruned)."""
        on = self._spec_on_at(dt.datetime.combine(date_, dt.time(hour, minute, 0, tzinfo=tz)))
        return {name: name in on for name in self._all_flags_template}

    async def _find_block_start(self, start_hal_day: dt.date, tz: ZoneInfo) -> Optional[Tuple[str, dt.date, "PHebrewDate"]]:
        # 0) Inside a block: today carries one of its labels and its first day is
        #    within the block's span behind us
        today = self._day_flags(start_hal_day)
        for base in self._IN_BLOCK_ORDER:
            anchor, labels, (_first, last) = self._BLOCKS[base]
            if not today.intersection(labels):
                continue
            for back in range(0, last + 1):
                d0 = start_hal_day - timedelta(days=back)
                if anchor in self._day_flags(d0):
                    return base, d0, PHebrewDate.from_pydate(d0)

        # 1) Otherwise, scan forward for the next block
        for k in range(0, self._horizon_days + 1):
            d = start_hal_day + timedelta(days=k)
            flags = self._day_flags(d)
            for base in self._FORWARD_ORDER:
                if self._BLOCKS[base][0] in flags:
                    return base, d, PHebrewDate.from_pydate(d)
        return None

    # ───────────────────── Helpers: buckets + phasing ─────────────────────

    async def _buckets_for_block(self, base: str, d0: dt.date, tz: ZoneInfo) -> List[Tuple[int, List[str]]]:
        """
        Return an ordered list of (offset, [labels]) describing the block, relative
        to D0 (Day1). Each label sits on the day its spec puts it on.
        """
        _anchor, block_labels, (first_off, last_off) = self._BLOCKS.get(base, (base, (base,), (0, 0)))
        buckets: List[Tuple[int, List[str]]] = []
        for off in range(first_off, last_off + 1):
            day_flags = self._day_flags(d0 + timedelta(days=off))
            on_day = [lbl for lbl in block_labels if lbl in day_flags]
            if on_day:
                buckets.append((off, on_day))

        # Keep only labels known to HolidaySensor AND allowed in the current mode
        buckets = [(off, self._filter_labels_for_mode(labels)) for off, labels in buckets]

        # ---------------- Spill-forward into subsequent holidays ----------------
        # Spill from where the block normally ends (its last day, or its מוצאי
        # right after it). A מוצאי moved to Motzei Shabbos must not push the
        # spill past what else falls on those days (צום גדליה, שושן פורים, ...).
        holiday_offs = [off for off, labels in buckets if any(not lbl.startswith("מוצאי ") for lbl in labels)]
        motzei_offs = [off for off, labels in buckets if any(lbl.startswith("מוצאי ") for lbl in labels)]
        if holiday_offs:
            last = max(holiday_offs)
            has_asru = any(lbl.startswith("אסרו חג") for _off, labels in buckets for lbl in labels)
            normal_motzei = last if has_asru else last + 1
            max_off = normal_motzei if normal_motzei in motzei_offs else last
        else:
            max_off = max(motzei_offs) if motzei_offs else 0

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

        # merged, not overwritten: spill-forward can land on a day the block already has
        off_to_labels: Dict[int, List[str]] = {}
        for off, labels in buckets:
            bucket = off_to_labels.setdefault(off, [])
            bucket.extend(lbl for lbl in labels if lbl not in bucket)

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
