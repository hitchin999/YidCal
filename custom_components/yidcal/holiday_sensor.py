# holiday_sensor.py
"""
Separate HolidaySensor for YidCal integration.
Handles Jewish holidays, fast days, and custom periods with time-aware logic,
restores its last state across reboots, and filters the visible state
through a whitelist while still exposing all flags.
"""

from __future__ import annotations
import datetime
import math
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import logging
from .device import YidCalDevice
from .const import DOMAIN

from zmanim.util.geo_location import GeoLocation
from hdate import HDateInfo
from hdate.translator import set_language
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from .zman_sensors import get_geo
from .yidcal_lib import halacha_events as he

# Shared zmanim primitives (cached; single source of truth) — replace the
# local _round_half_up / _round_ceil / _compute_chatzos_hayom copies this
# module used to carry.
from .yidcal_lib.zman_compute import (
    round_half_up as _round_half_up,
    round_ceil as _round_ceil,
    round_floor as _round_floor,
    sunset_for_date,
    dawn_for_date,
    chatzos_hayom_for_date,
)


def _compute_chatzos_hayom(geo: GeoLocation, base_date: datetime.date, tz: ZoneInfo) -> datetime.datetime:
    """Chatzos Hayom (halachic midday), half-up rounded.

    Now sourced from the shared Grossman true-solar-transit helper so the
    fast-countdown timers agree to the minute with sensor.yidcal_chatzos_hayom
    (the old local copy used the sunrise/sunset midpoint, which drifts
    15–30 s off true noon and could land one display-minute away).
    """
    return _round_half_up(chatzos_hayom_for_date(geo=geo, tz=tz, base_date=base_date))

_LOGGER = logging.getLogger(__name__)

class HolidaySensor(YidCalDevice, RestoreEntity, SensorEntity):
    """
    Tracks Jewish holidays, fasts, and custom periods with time-aware logic.
    - Restores its last visible state on reboot
    - Exposes ALL holiday flags as attributes
    - Uses ALLOWED_HOLIDAYS to pick exactly one for its state
    """
    _attr_name = "Holiday"
    _attr_icon = "mdi:calendar-star"
    # The two Yiddish countdown texts tick every minute during fast
    # windows; excluding them from the recorder keeps the ticking live
    # in the UI without writing a database row per minute (state and
    # all other attributes still record normally).
    _unrecorded_attributes = frozenset({"מען פאַסט אַן און", "מען פאַסט אויס און"})
    _attr_device_class = SensorDeviceClass.ENUM
    
    FAST_FLAGS = [
        "יום הכיפורים",
        "צום גדליה",
        "תענית אסתר",
        "צום עשרה בטבת",
        "צום שבעה עשר בתמוז",
        "תשעה באב",
        "תשעה באב נדחה",
    ]

    # ─── THE FULL SET of every holiday you detect (for attributes) ───
    ALL_HOLIDAYS: list[str] = [
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",
        "ראש השנה ב׳",
        "ראש השנה א׳ וב׳",
        "מוצאי ראש השנה",
        "עשרת ימי תשובה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות (כל חג)",
        "סוכות א׳",
        "סוכות ב׳",
        "סוכות א׳ וב׳",
        "מוצאי סוכות ימים ראשונים",
        "א׳ דחול המועד סוכות",
        "ב׳ דחול המועד סוכות",
        "ג׳ דחול המועד סוכות",
        "ד׳ דחול המועד סוכות",
        "ה׳ דחול המועד סוכות",
        "חול המועד סוכות",
        "שבת חול המועד סוכות",
        "הושענא רבה",
        "שמיני עצרת",
        "שמחת תורה",
        "שמיני עצרת/שמחת תורה",
        "מוצאי סוכות",
        "אסרו חג סוכות",
        "ערב חנוכה",
        "חנוכה",
        "ערב שבת חנוכה",
        "שבת חנוכה",
        "שבת חנוכה ראש חודש",
        "א׳ דחנוכה",
        "ב׳ דחנוכה",
        "ג׳ דחנוכה",
        "ד׳ דחנוכה",
        "ה׳ דחנוכה",
        "ו׳ דחנוכה",
        "ז׳ דחנוכה",
        "זאת חנוכה",
        "מוצאי חנוכה",
        "שובבים",
        "שובבים ת\"ת",
        "צום עשרה בטבת",
        "חמשה עשר בשבט",
        "פורים קטן",
        "שושן פורים קטן",
        "תענית אסתר מוקדם",
        "שבת ערב פורים",
        "תענית אסתר",
        "פורים",
        "שושן פורים",
        "מוצאי שושן פורים",
        "ערב בדיקת חמץ",
        "ליל בדיקת חמץ",
        "ערב פסח מוקדם",
        "שבת ערב פסח",
        "ערב פסח",
        "פסח (כל חג)",
        "פסח א׳",
        "פסח ב׳",
        "פסח א׳ וב׳",
        "מוצאי פסח ימים ראשונים",
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "ה׳ דחול המועד פסח",
        "חול המועד פסח",
        "שבת חול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "שביעי/אחרון של פסח",
        "מוצאי פסח",
        "אסרו חג פסח",
        "פסח שני",
        "ל\"ג בעומר",
        "מוצאי ל\"ג בעומר",
        "ערב שבועות",
        "שבת ערב שבועות",
        "שבועות א׳",
        "שבועות ב׳",
        "שבועות א׳ וב׳",
        "מוצאי שבועות",
        "אסרו חג שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "ערב תשעה באב",
        "ערב תשעה באב שחל בשבת",
        "תשעה באב",
        "תשעה באב נדחה",
        "מוצאי תשעה באב",
        "ט\"ו באב",
        "יום כיפור קטן",
        "ראש חודש",
        "שבת ראש חודש",
        "ערב שבת",
        "ערב יום טוב",
        "מוצאי שבת",
        "מוצאי יום טוב",
        "א׳ דיום טוב",
        "ב׳ דיום טוב",
        "ערב שבת שחל ביום טוב",
        "ערב יום טוב שחל בשבת",
        "מוצאי שבת שחל ביום טוב",
        "מוצאי יום טוב שחל בשבת",
    ]

    # ─── Only these may become the sensor.state ───
    ALLOWED_HOLIDAYS: list[str] = [
        "א׳ סליחות",
        "ערב ראש השנה",
        "ראש השנה א׳",   # displayed as: א׳ דראש השנה
        "ראש השנה ב׳",   # displayed as: ב׳ דראש השנה
        "מוצאי ראש השנה",
        "צום גדליה",
        "שלוש עשרה מדות",
        "ערב יום כיפור",
        "יום הכיפורים",
        "מוצאי יום הכיפורים",
        "ערב סוכות",
        "סוכות א׳",      # displayed as: א׳ דסוכות
        "סוכות ב׳",      # displayed as: ב׳ דסוכות
        "א׳ דחול המועד סוכות",
        "ב׳ דחול המועד סוכות",
        "ג׳ דחול המועד סוכות",
        "ד׳ דחול המועד סוכות",
        "ה׳ דחול המועד סוכות",
        "הושענא רבה",
        "שמיני עצרת",
        "שמחת תורה",
        "מוצאי סוכות",
        "אסרו חג סוכות",
        "ערב חנוכה",
        "א׳ דחנוכה",
        "ב׳ דחנוכה",
        "ג׳ דחנוכה",
        "ד׳ דחנוכה",
        "ה׳ דחנוכה",
        "ו׳ דחנוכה",
        "ז׳ דחנוכה",
        "חנוכה",
        "זאת חנוכה",
        "צום עשרה בטבת",
        "חמשה עשר בשבט",
        "פורים קטן",
        "תענית אסתר",
        "פורים",
        "שושן פורים",
        "ליל בדיקת חמץ",
        "ערב פסח",
        "פסח א׳",        # displayed as: א׳ דפסח
        "פסח ב׳",        # displayed as: ב׳ דפסח
        "א׳ דחול המועד פסח",
        "ב׳ דחול המועד פסח",
        "ג׳ דחול המועד פסח",
        "ד׳ דחול המועד פסח",
        "ה׳ דחול המועד פסח",
        "שביעי של פסח",
        "אחרון של פסח",
        "מוצאי פסח",
        "אסרו חג פסח",
        "פסח שני",
        "ל\"ג בעומר",
        "ערב שבועות",
        "שבועות א׳",     # displayed as: א׳ דשבועות
        "שבועות ב׳",     # displayed as: ב׳ דשבועות
        "מוצאי שבועות",
        "אסרו חג שבועות",
        "צום שבעה עשר בתמוז",
        "מוצאי צום שבעה עשר בתמוז",
        "ערב תשעה באב",
        "תשעה באב",
        "מוצאי תשעה באב",
        "ט\"ו באב",
        "יום כיפור קטן",
    ]

    
    # Attributes that should only exist in one mode to avoid confusion
    EY_ONLY_ATTRS = {
        "ה׳ דחול המועד פסח",
        "ה׳ דחול המועד סוכות",
    }
    DIASPORA_ONLY_ATTRS = {
        "סוכות ב׳",
        "פסח ב׳",
        "שבועות ב׳",
        "סוכות א׳ וב׳",
        "פסח א׳ וב׳",
        "שבועות א׳ וב׳",
        "אחרון של פסח",
        "שביעי/אחרון של פסח",
        "שמיני עצרת",
        "שמחת תורה",
        "א׳ דיום טוב",
        "ב׳ דיום טוב",
    }

    def _possible_states_for_mode(self) -> list[str]:
        """
        Build the UI options in chronological order.
        - Base order = ALLOWED_HOLIDAYS (already chronological), with 2-day forms flipped.
        - In Israel mode:
            • Replace separate שמיני עצרת/שמחת תורה with a single combined label,
              inserted immediately after הושענא רבה.
            • Insert base names סוכות/פסח/שבועות immediately after א׳ ד<חג>.
        """
        seq = [
            self._flip_two_day_format(n)
            for n in self.ALLOWED_HOLIDAYS
            if self._attr_allowed_in_mode(n)
        ]
        if self._diaspora:
            return seq

        ey_seq: list[str] = []
        seen = set()
        inserted_combined = False
        for label in seq:
            # Skip the split שמיני/שמחת in EY; we'll insert the combined one once.
            if label in ("שמיני עצרת", "שמחת תורה"):
                continue
            ey_seq.append(label); seen.add(label)

            # Right after הושענא רבה, insert the combined label.
            if label == "הושענא רבה" and not inserted_combined:
                ey_seq.append("שמיני עצרת/שמחת תורה")
                seen.add("שמיני עצרת/שמחת תורה")
                inserted_combined = True

            # Insert base names right after Day-1 labels.
            if label == "א׳ דסוכות" and "סוכות" not in seen:
                ey_seq.append("סוכות"); seen.add("סוכות")
            if label == "א׳ דפסח" and "פסח" not in seen:
                ey_seq.append("פסח"); seen.add("פסח")
            if label == "א׳ דשבועות" and "שבועות" not in seen:
                ey_seq.append("שבועות"); seen.add("שבועות")

        return ey_seq

    def _attr_allowed_in_mode(self, name: str) -> bool:
        if self._diaspora:
            return name not in self.EY_ONLY_ATTRS
        return name not in self.DIASPORA_ONLY_ATTRS

    def _empty_attrs_for_mode(self) -> dict[str, bool | str | list[str]]:
        names = [n for n in self.ALL_HOLIDAYS if self._attr_allowed_in_mode(n)]
        attrs = {name: False for name in names}
        attrs["מען פאַסט אויס און"] = ""
        attrs["מען פאַסט אַן און"] = ""
        # Machine-readable fast boundaries (ISO datetimes, stable per
        # fast — they do NOT tick). These drive the timer.yidcal_fast_*
        # entities (see fast_timers.py).
        attrs["fast_starts_at"] = ""
        attrs["fast_ends_at"] = ""
        return attrs

    def _prune_attrs_for_mode(self, attrs: dict[str, bool | str]) -> dict[str, bool | str]:
        allowed = set(self._empty_attrs_for_mode().keys())
        # keep special meta keys
        keep_always = {"מען פאַסט אויס און", "מען פאַסט אַן און", "fast_starts_at", "fast_ends_at"}
        return {k: v for k, v in attrs.items() if (k in allowed or k in keep_always)}
    
    @staticmethod
    def _base_attrs() -> dict[str, bool | str | list[str]]:
        """Fresh attributes dict with all flags False + countdowns. (No 'Possible states' here.)"""
        attrs = {name: False for name in HolidaySensor.ALL_HOLIDAYS}
        attrs["מען פאַסט אויס און"] = ""  # fast ends in
        attrs["מען פאַסט אַן און"] = ""   # fast starts in
        attrs["fast_starts_at"] = ""      # ISO ts, stable (no ticking)
        attrs["fast_ends_at"] = ""        # ISO ts, stable (no ticking)
        return attrs

    # Two-day chagim where we want "א׳ ד<שם>" instead of "<שם> א׳"
    _TWO_DAY_BASES = {"ראש השנה", "סוכות", "פסח", "שבועות"}
    
    @staticmethod
    def _flip_two_day_format(name: str) -> str:
        """
        Turn '<base> א׳/ב׳' into 'א׳/ב׳ ד<base>' but only for bases in _TWO_DAY_BASES.
        Leaves anything else unchanged.
        """
        # Hebrew geresh is U+05F3 (׳). Your strings already use it, so match that.
        if name.endswith(" א׳") or name.endswith(" ב׳"):
            base = name[:-3]           # strip space + letter + geresh
            day_letter = name[-2]      # 'א' or 'ב'
            if base in HolidaySensor._TWO_DAY_BASES:
                return f"{day_letter}׳ ד{base}"
        return name

    # For Israel-only display: collapse day-1 labels to base name
    _EY_DAY1_ALIAS = {
        "א׳ דסוכות": "סוכות",
        "א׳ דפסח": "פסח",
        "א׳ דשבועות": "שבועות",
        # deliberately NOT collapsing R"H:
        # "א׳ דראש השנה": "ראש השנה",
    }

    @staticmethod
    def _ey_collapse_day1_label(name: str) -> str:
        return HolidaySensor._EY_DAY1_ALIAS.get(name, name)

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "holiday"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        # initial state + full attrs
        self._attr_native_value: str = ""
        self._attr_extra_state_attributes: dict[str, bool | str] = {}
        # flag -> (start, end) for whatever is on, filled in by async_update
        self._flag_windows: dict[str, tuple[datetime.datetime, datetime.datetime]] = {}
        cfg = hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora = cfg.get("diaspora", True)
        # יום כיפור קטן: Erev RC Elul only (historical behaviour) or every
        # Erev RC it is said. Read once - changing it reloads the entry.
        self._ykk_every_month = (
            cfg.get("yom_kippur_katan_scope", "elul") == "all"
        )

        # Hebrew names
        set_language("he")
        # cache tz/geo after add; placeholders now
        self._tz: ZoneInfo | None = ZoneInfo(hass.config.time_zone)
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        # Restore last state/attributes on startup
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
    
        # Always start from a clean base
        attrs = self._empty_attrs_for_mode()
    
        if last:
            for k, v in (last.attributes or {}).items():
                if k in attrs or k in ("מען פאַסט אויס און", "מען פאַסט אַן און"):
                    attrs[k] = v
            self._attr_native_value = last.state or ""
        else:
            self._attr_native_value = ""
            
        self._attr_extra_state_attributes = attrs
    
        # cache geo once
        self._geo = await get_geo(self.hass)

        # immediate first calculation so UI isn’t stale after restore
        await self.async_update()

        # schedule minute‐interval updates via base‐class wrapper
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, bool | str]:
        return self._attr_extra_state_attributes
        
    @property
    def options(self) -> list[str]:
        """Values visible in the HA UI select; filtered by mode."""
        return self._possible_states_for_mode() + [""]

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None:
            return

        # require cached geo; if missing (very early), bail gracefully
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz or ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        actual_date = now.date()
        wd = now.weekday()
        actual_sunset = sunset_for_date(geo=self._geo, tz=tz, base_date=actual_date)

        # Compute roll‐points using rounded candle/havdalah (aligned with other sensors)
        raw_candle_cut = actual_sunset - timedelta(minutes=self._candle_offset)
        raw_havdalah_cut = actual_sunset + timedelta(minutes=self._havdalah_offset)

        candle_cut = _round_half_up(raw_candle_cut)
        havdalah_cut = _round_ceil(raw_havdalah_cut)

        # festival_date rolls at havdalah
        if now >= havdalah_cut:
            festival_date = actual_date + timedelta(days=1)
        else:
            festival_date = actual_date

        wd_fest = wd if festival_date == actual_date else (wd + 1) % 7

        # detect_date rolls at candle‐lighting
        if now >= candle_cut:
            detect_date = actual_date + timedelta(days=1)
        else:
            detect_date = actual_date

        # Anchor sunsets around festival_date (shared cached zmanim)
        prev_sunset_raw = sunset_for_date(geo=self._geo, tz=tz, base_date=festival_date - timedelta(days=1))
        next_sunset_raw = sunset_for_date(geo=self._geo, tz=tz, base_date=festival_date + timedelta(days=1))

        tomorrow_sunset_raw = sunset_for_date(geo=self._geo, tz=tz, base_date=actual_date + timedelta(days=1))
        
        # Convenience aliases (raw sunsets)
        prev_sunset = prev_sunset_raw
        next_sunset = next_sunset_raw
        tomorrow_sunset = tomorrow_sunset_raw

        # Floored copies for use as Tisha B'Av fast-start anchors. The
        # fast must begin BEFORE astronomical shkia, never after — so
        # the displayed and countdown-target minutes are floored
        # (truncate seconds). Raw `actual_sunset` / `prev_sunset` are
        # still used for non-fast-start purposes (havdalah windows,
        # end-times, etc.) where seconds-precision is fine.
        actual_sunset_floor = _round_floor(actual_sunset)
        prev_sunset_floor = _round_floor(prev_sunset)

        # Align dawn with the festival day for consistent daytime windows.
        # Floor (truncate seconds) instead of half-up, because `dawn` is
        # used as the START anchor for minor fasts (Tzom Gedaliah, 10
        # Teves, Ta'anis Esther, 17 Tammuz) — see `start_time_fast =
        # dawn` below. Fast start is a chumra zman: must begin BEFORE
        # astronomical alos, never after. The general-purpose alos
        # sensor (zman_alos.py) keeps half-up — that's correct for
        # positive uses like sof zman krias shma, tefilas, etc.
        dawn = _round_floor(dawn_for_date(geo=self._geo, tz=tz, base_date=festival_date))
        #_LOGGER.debug(f"Dawn: {dawn}, now: {now}, festival_date: {festival_date}")
        
        # Hebrew dates
        hd_py = PHebrewDate.from_pydate(detect_date)
        hd_fest = PHebrewDate.from_pydate(festival_date)
        hd_py_fast = PHebrewDate.from_pydate(actual_date)
        

        # Debug Hebrew date
        #_LOGGER.debug(f"Current time: {now}, Hebrew date (hd_py): {hd_py.month}/{hd_py.day}, "
        #              f"hd_fest: {hd_fest.month}/{hd_fest.day}, hd_py_fast: {hd_py_fast.month}/{hd_py_fast.day}")


        # flag -> (start, end) for every flag this update switches on: the spec
        # evaluator below records each flag's own window (aggregates span their
        # members).
        _flag_windows: dict[str, tuple[datetime.datetime, datetime.datetime]] = {}

        year = hd_py.year

        adar_month = he.real_adar_month(year)

        # Observed fast dates — canonical rules from halacha_events.
        h_year = year if hd_py.month >= 7 else year + 1
        gedaliah_day = PHebrewDate.from_pydate(he.tzom_gedaliah_observed(h_year)).day


        # 17 Tammuz: 18 when 17 falls on Shabbos (canonical)
        tammuz_17_day = PHebrewDate.from_pydate(
            he.shiva_asar_btamuz_observed(hd_fest.year)
        ).day

        # ─── Fast start/end times
        # Default for regular fasts
        start_time_fast = dawn
        end_time = actual_sunset + timedelta(minutes=self._havdalah_offset)

        # Force default for minor fasts to prevent extension
        if (hd_py_fast.month == 7 and hd_py_fast.day == gedaliah_day) or \
           (hd_py_fast.month == 10 and hd_py_fast.day == 10) or \
           (hd_py_fast.month == 4 and hd_py_fast.day == tammuz_17_day) or \
           (hd_py.month == adar_month and hd_py.day == 13):
            start_time_fast = dawn
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset)
        # Override for 25-hour fasts and their Erev
        elif hd_py.month == 7 and hd_py.day == 9:  # Erev Yom Kippur
            start_time_fast = candle_cut
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_py.month == 7 and hd_py.day == 10:  # Yom Kippur
            start_time_fast = prev_sunset - timedelta(minutes=self._candle_offset)
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if detect_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 8 and now < actual_sunset_floor:  # Erev Tisha B'Av
            start_time_fast = actual_sunset_floor
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and (hd_fest.day == 8 and now >= actual_sunset_floor or hd_fest.day == 9):  # Tisha B'Av
            start_time_fast = prev_sunset_floor if hd_fest.day == 9 else actual_sunset_floor
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if hd_fest.day == 9 and festival_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 10 and wd_fest == 6:  # Deferred Tisha B'Av day
            start_time_fast = prev_sunset_floor
            end_time = actual_sunset + timedelta(minutes=self._havdalah_offset) if festival_date == actual_date else tomorrow_sunset + timedelta(minutes=self._havdalah_offset)
        elif hd_fest.month == 5 and hd_fest.day == 9 and wd_fest == 5:  # Erev for Deferred Tisha B'Av (Av 9 on Shabbat)
            start_time_fast = actual_sunset_floor
            end_time = tomorrow_sunset + timedelta(minutes=self._havdalah_offset)

        #_LOGGER.debug(f"Fast times: start_time_fast={start_time_fast}, end_time={end_time}, now={now}")

        # Build raw attrs
        attrs = self._empty_attrs_for_mode()

        # א׳ סליחות: he.FLAG_SPECS
        # Single-source flags (he.FLAG_SPECS): on while now is inside the
        # shape's window for a day the rule accepts; that same window is the
        # one published.
        for _name, _window in he.evaluate_flag_specs(
            now=now, tz=tz, geo=self._geo, diaspora=self._diaspora,
            candle_offset=self._candle_offset, havdalah_offset=self._havdalah_offset,
            options={"every_month": self._ykk_every_month},
        ).items():
            attrs[_name] = True
            _flag_windows[_name] = _window

        # ערב ראש השנה, ראש השנה א׳ / ב׳ / א׳ וב׳: he.FLAG_SPECS
        # צום גדליה, שלוש עשרה מדות: he.FLAG_SPECS

        # Yom Kippur
        # ערב יום כיפור, יום הכיפורים: he.FLAG_SPECS

        # Sukkos — ערב סוכות, סוכות, חול המועד, הושענא רבה, שמיני עצרת, שמחת תורה,
        # אסרו חג: he.FLAG_SPECS

        # ─── Chanukah: ערב חנוכה, חנוכה, the day flags, זאת חנוכה, ערב שבת חנוכה: he.FLAG_SPECS

        # שובבים, שובבים ת"ת: he.FLAG_SPECS

        # צום עשרה בטבת: he.FLAG_SPECS

        # Tu BiShvat: he.FLAG_SPECS

        # פורים קטן, שושן פורים קטן, תענית אסתר (מוקדם), פורים, שושן פורים: he.FLAG_SPECS

        # ערב בדיקת חמץ, ליל בדיקת חמץ: he.FLAG_SPECS

        # Pesach — ערב פסח (מוקדם), שבת ערב פסח, פסח, חול המועד, שביעי / אחרון,
        # אסרו חג: he.FLAG_SPECS

        # Pesach Sheini & Lag BaOmer: he.FLAG_SPECS

        # Shavuos — ערב שבועות, שבת ערב שבועות, שבועות, אסרו חג: he.FLAG_SPECS

        # Rosh Chodesh: he.FLAG_SPECS

        # צום שבעה עשר בתמוז: he.FLAG_SPECS

        # Tisha B'Av — ערב תשעה באב (שחל בשבת), תשעה באב, תשעה באב נדחה: he.FLAG_SPECS
            
         # Tu B'Av (15 Av)
        # ט"ו באב: he.FLAG_SPECS
            
        # ─── יום כיפור קטן: he.FLAG_SPECS (its scope option via he.FLAG_RULE_OPTIONS)

        # ─── Countdown for fast starts in ───────────────────────────────────
        # Minor fasts: timer starts at tzeis (havdalah) the evening before
        # Major fasts (YK, Tisha B'Av): timer starts at Chatzos HaYom of Erev
        
        minor_fast_dates = [
            (7, gedaliah_day),                    # Tzom Gedaliah
            (10, 10),                             # 10 Tevet
            (4, 17),                              # 17 Tammuz
            (adar_month, 13),                     # Ta'anit Esther (real Adar)
        ]
        
        # Check if tomorrow (by halachic day) is a minor fast
        hd_tomorrow = PHebrewDate.from_pydate(festival_date)
        is_tomorrow_minor_fast = any(
            hd_tomorrow.month == m and hd_tomorrow.day == d 
            for m, d in minor_fast_dates
        )
        
        # Check if today (actual date) is a minor fast day (for during-fast display)
        is_today_minor_fast = any(
            hd_py_fast.month == m and hd_py_fast.day == d 
            for m, d in minor_fast_dates
        )
        
        # Compute tomorrow's dawn for minor fasts.
        # Floored (truncate seconds) — same chumra reason as `dawn`
        # above: this is the countdown TARGET for "fast starts in",
        # and the countdown should reach 0 at the floor minute, not
        # the half-up minute (which could land after astronomical
        # alos when alos seconds ≥ 30).
        next_dawn = _round_floor(dawn_for_date(geo=self._geo, tz=tz, base_date=festival_date))
        
        # ─── MINOR FASTS: Pre-fast countdown (tzeis evening before → alos) ───
        no_fast_active_now = not any(attrs.get(f) for f in self.FAST_FLAGS)
        
        # For the window check: handle midnight rollover
        # If we're past midnight but before dawn, we're definitely past tzeis (which was hours ago)
        # so we just need to check now < next_dawn
        past_midnight_before_dawn = (now.hour < 12 and now < next_dawn)
        
        # Normal evening check (same calendar day): after tonight's tzeis
        tonight_tzeis = _round_ceil(actual_sunset + timedelta(minutes=self._havdalah_offset))
        evening_window = (tonight_tzeis <= now)
        
        # Combined: either past midnight before dawn, OR evening after tzeis but before dawn
        in_minor_fast_prewindow = (past_midnight_before_dawn or evening_window) and now < next_dawn
        
        if no_fast_active_now and is_tomorrow_minor_fast and in_minor_fast_prewindow:
            remaining_sec = max(0, (next_dawn - now).total_seconds())
            minutes_remaining = math.ceil(remaining_sec / 60)
            h = minutes_remaining // 60
            m = minutes_remaining % 60
            attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
            attrs["fast_starts_at"] = next_dawn.isoformat() if minutes_remaining > 0 else ""
        
        # ─── MAJOR FASTS: Pre-fast countdown (Chatzos HaYom of Erev → fast start) ───
        # Erev Yom Kippur: countdown from Chatzos until candle lighting
        elif hd_py.month == 7 and hd_py.day == 9:
            chatzos_erev_yk = _compute_chatzos_hayom(self._geo, actual_date, tz)
            if chatzos_erev_yk <= now < candle_cut:
                remaining_sec = max(0, (candle_cut - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
                attrs["fast_starts_at"] = candle_cut.isoformat() if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # Erev Tisha B'Av: countdown from Chatzos until sunset.
        # Compare against the floored sunset (chumra) — same as fast
        # activation above. The countdown reaches 0 at the floor
        # minute, matching what the user expects after seeing the
        # displayed start time.
        elif hd_fest.month == 5 and hd_fest.day == 8 and now < actual_sunset_floor:
            chatzos_erev_9av = _compute_chatzos_hayom(self._geo, actual_date, tz)
            if chatzos_erev_9av <= now < actual_sunset_floor:
                remaining_sec = max(0, (actual_sunset_floor - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
                attrs["fast_starts_at"] = actual_sunset_floor.isoformat() if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # Erev Tisha B'Av Nidche (when 9 Av is Shabbos, fast is Sunday): 
        # countdown from Chatzos Shabbos until Motzei Shabbos
        elif hd_fest.month == 5 and hd_fest.day == 9 and wd_fest == 5:
            chatzos_shabbos = _compute_chatzos_hayom(self._geo, actual_date, tz)
            motzei_shabbos = _round_ceil(actual_sunset + timedelta(minutes=self._havdalah_offset))
            if chatzos_shabbos <= now < motzei_shabbos:
                remaining_sec = max(0, (motzei_shabbos - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אַן און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
                attrs["fast_starts_at"] = motzei_shabbos.isoformat() if minutes_remaining > 0 else ""
            else:
                attrs["מען פאַסט אַן און"] = ""
        
        # ─── DURING FAST: countdown to end ───
        # Minor fasts during the fast day itself
        elif is_today_minor_fast and dawn <= now < end_time:
            # Fast is active - no "starts in" countdown needed
            attrs["מען פאַסט אַן און"] = ""
        
        # Yom Kippur during the fast
        elif (hd_py.month == 7 and hd_py.day == 10) or (hd_fest.month == 7 and hd_fest.day == 10):
            attrs["מען פאַסט אַן און"] = ""
        
        # Tisha B'Av during the fast
        elif (hd_fest.month == 5 and hd_fest.day == 9) or \
             (hd_fest.month == 5 and hd_fest.day == 10 and wd_fest == 6):
            attrs["מען פאַסט אַן און"] = ""
        
        else:
            attrs["מען פאַסט אַן און"] = ""
            
        # עשרת ימי תשובה: he.FLAG_SPECS


        def _span_from(name: str, sources) -> None:
            """An aggregate's window for this day: the span of what it covers.

            An aggregate is `any(...)` of other flags, so it has no window type
            of its own and its real run is not one of the nine shapes either -
            Sukkos as a whole is eight days. Recording one day's span is enough,
            because a consumer walking day by day unions them back into the
            whole run, exactly as it does for Rosh Chodesh or Chanukah.
            """
            spans = [
                _flag_windows[n]
                for n in sources
                if attrs.get(n) and n in _flag_windows
            ]
            if spans:
                _flag_windows[name] = (
                    min(start for start, _ in spans),
                    max(end for _, end in spans),
                )

        # ─── Aggregate flags & Shabbos Chol HaMoed (attributes only) ────────
        # Aggregate flags (he.FLAG_AGGREGATES): on while any member is on.
        # שביעי/אחרון של פסח is diaspora only.
        for _agg in ("סוכות (כל חג)", "שמיני עצרת/שמחת תורה", "פסח (כל חג)", "שביעי/אחרון של פסח"):
            if _agg == "שביעי/אחרון של פסח" and not self._diaspora:
                continue
            attrs[_agg] = any(attrs.get(n, False) for n in he.FLAG_AGGREGATES[_agg])
            _span_from(_agg, he.FLAG_AGGREGATES[_agg])

        # ─── שבת חנוכה (ראש חודש), שבת חול המועד, שבת ראש חודש: he.FLAG_SPECS

        # ─── Countdown for fast ends in
        if any(attrs.get(f) for f in self.FAST_FLAGS):
            # during fast: show time until end
            if start_time_fast <= now < end_time:
                remaining_sec = max(0, (end_time - now).total_seconds())
                minutes_remaining = math.ceil(remaining_sec / 60)
                h = minutes_remaining // 60
                m = minutes_remaining % 60
                attrs["מען פאַסט אויס און"] = f"{h:02d}:{m:02d}" if minutes_remaining > 0 else ""
                attrs["fast_ends_at"] = _round_ceil(end_time).isoformat() if minutes_remaining > 0 else ""
                #_LOGGER.debug(f"Fast ends in set: now={now}, end_time={end_time}, "
                #              f"countdown={attrs['מען פאַסט אויס און']}")
            # before fast: show total duration
            elif now < start_time_fast:
                duration_sec = (end_time - start_time_fast).total_seconds()
                minutes_duration = math.ceil(duration_sec / 60)
                h = minutes_duration // 60
                m = minutes_duration % 60
                attrs["מען פאַסט אויס און"] = f"{h:02d}:{m:02d}"
                attrs["fast_ends_at"] = _round_ceil(end_time).isoformat()
                #_LOGGER.debug(f"Fast duration set: start_time_fast={start_time_fast}, "
                #              f"end_time={end_time}, countdown={attrs['מען פאַסט אויס און']}")
            # after fast: clear countdown
            else:
                attrs["מען פאַסט אויס און"] = ""
                #_LOGGER.debug(f"Fast ended, countdown cleared: now={now}, end_time={end_time}")
        else:
            attrs["מען פאַסט אויס און"] = ""
            #_LOGGER.debug(f"No fast flag active, countdown cleared")

        # מוצאי flags: he.FLAG_SPECS

        # Erev / Motzei flags (ערב שבת, מוצאי יום טוב, …): he.FLAG_SPECS
                
        # --- Motzei should DISPLAY only until 2:00 AM after havdalah ---
        motzei_cutoff_2am = datetime.datetime.combine(
            havdalah_cut.date(), time(2, 0), tz
        )
        # if 2am is earlier than havdalah (normal case), push cutoff to next day
        if motzei_cutoff_2am <= havdalah_cut:
            motzei_cutoff_2am += timedelta(days=1)

        motzei_display_allowed = now < motzei_cutoff_2am

        # ─── pick exactly one allowed holiday for .state ────────────────────
        combined = next((n for n, on in attrs.items() if n.endswith(" א׳ וב׳") and on), None)
        if combined:
            base = combined[:-len(" א׳ וב׳")]
            # Decide which day is active
            is_day1 = attrs.get(f"{base} א׳", False)
            # Build as "א׳ ד<base>" or "ב׳ ד<base>" but only for the two-day bases
            if base in self._TWO_DAY_BASES:
                picked = f"{'א' if is_day1 else 'ב'}׳ ד{base}"
            else:
                picked = f"{base} {'א׳' if is_day1 else 'ב׳'}"

        elif attrs.get("זאת חנוכה"):
            picked = "זאת חנוכה"

        elif motzei_display_allowed and any(attrs.get(name) for name in [
            "מוצאי ראש השנה",
            "מוצאי יום הכיפורים",
            "מוצאי פסח",
            "מוצאי שבועות",
            "מוצאי סוכות",
            "מוצאי צום שבעה עשר בתמוז",
            "מוצאי תשעה באב",
        ]):
            motzei_list = [
                "מוצאי ראש השנה",
                "מוצאי יום הכיפורים",
                "מוצאי פסח",
                "מוצאי שבועות",
                "מוצאי סוכות",
                "מוצאי צום שבעה עשר בתמוז",
                "מוצאי תשעה באב",
            ]
            picked = next(n for n in motzei_list if attrs.get(n))

        elif any(attrs.get(name) for name in [
            "אסרו חג פסח",
            "אסרו חג שבועות",
            "אסרו חג סוכות",
        ]):
            asru_list = [
                "אסרו חג פסח",
                "אסרו חג שבועות",
                "אסרו חג סוכות",
            ]
            picked = next(n for n in asru_list if attrs.get(n))

        elif attrs.get("שביעי של פסח"):
            picked = "שביעי של פסח"

        else:
            picked = next((n for n in self.ALLOWED_HOLIDAYS if attrs.get(n)), "")
            # Flip single-day forms like "סוכות א׳" → "א׳ דסוכות" when applicable
            picked = self._flip_two_day_format(picked)

        # ---------- Israel post-filter (no logic rewrites, only outcome tweaks) ----------
        if not self._diaspora:
            # (flags: he.FLAG_SPECS is mode-aware; only the state label is adjusted here)
            if picked in ("שמיני עצרת", "שמחת תורה"):
                picked = "שמיני עצרת/שמחת תורה"
            picked = self._ey_collapse_day1_label(picked)


        # ─── א׳/ב׳ דיום טוב aggregates (diaspora-only attrs) ───
        # First/second day of ANY two-day Yom Tov pair. Derived AFTER
        # the window filter and the Israel post-filter as a plain OR
        # of the finalized per-day flags, so each aggregate flips at
        # exactly the same moments as its source sensors. Not in
        # ALLOWED_HOLIDAYS — attribute/binary-sensor only, never the
        # state. Excluded in EY mode (DIASPORA_ONLY_ATTRS): the only
        # two-day YT there is ראש השנה, and שמיני עצרת/שמחת תורה
        # share one day, which would raise both flags at once.
        if self._diaspora:
            for _agg in ("א׳ דיום טוב", "ב׳ דיום טוב"):
                attrs[_agg] = any(attrs.get(n, False) for n in he.FLAG_AGGREGATES[_agg])
                _span_from(_agg, he.FLAG_AGGREGATES[_agg])

        # Prune to mode after all flags are computed
        attrs = self._prune_attrs_for_mode(attrs)

        # Keep raw bools for internal consumers (e.g. upcoming_holiday_sensor)
        self._bool_attrs = dict(attrs)
        # ... and, beside them, the window each of those flags is on for. Every
        # entry came from the code that decided the flag - the window filter,
        # the aggregate spans, or the erev/motzei extras - so nothing
        # downstream has to re-derive a zman to know when a flag starts or ends.
        self._flag_windows = {
            name: window
            for name, window in _flag_windows.items()
            if attrs.get(name)
        }

        # Convert bool attrs to lowercase strings for HA state condition compatibility
        attrs = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in attrs.items()}

        self._attr_native_value = picked
        self._attr_extra_state_attributes = attrs

        # Publish immediately, on the aligned :00 tick. Without this the
        # method only mutates in-memory attributes -- nothing reaches HA
        # until the entity platform's OWN poll (should_poll defaults to
        # True, ~30s cadence anchored to platform setup) calls async_update
        # again and writes. That poll is why flips were logged at :41 /
        # :44 / :48 instead of the rounded minute. It still runs, harmlessly:
        # it recomputes the same values and writes no change.
        # `platform` is set by add_to_platform_start() BEFORE
        # async_added_to_hass, so real platform-managed entities always have
        # it. Throwaway instances do NOT -- upcoming_holiday_sensor builds
        # bare HolidaySensor objects and calls async_update(fake_now) to
        # simulate a FUTURE date. Those carry the real entity_id, so without
        # this check they would publish simulated attributes onto the live
        # sensor.yidcal_holiday (and HA logs 'does not have a platform').
        if (
            self.hass is not None
            and self.entity_id
            and getattr(self, "platform", None) is not None
        ):
            self.async_write_ha_state()
