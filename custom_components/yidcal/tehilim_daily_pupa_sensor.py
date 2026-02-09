# tehilim_daily_pupa.py

from __future__ import annotations
from datetime import date, timedelta
from typing import Any, Iterable

import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.event import async_track_state_change_event

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalDisplayDevice
from .yidcal_lib.helper import int_to_hebrew

# ──────────────────────────────────────────────────────────────────────────────
# Pupa divisions
# ──────────────────────────────────────────────────────────────────────────────

CHAPTER_COUNT = 150
BLOCK_SIZE = 5
CYCLE_LENGTH = (CHAPTER_COUNT + BLOCK_SIZE - 1) // BLOCK_SIZE  # = 30

# Elul: 12 blocks (finish twice through the month by cycling the 12-block list)
ELUL_BLOCKS: list[tuple[int, int]] = [
    (1, 15),     # א - טו
    (16, 30),    # טז - ל
    (31, 41),    # לא - מא
    (42, 52),    # מב - נב
    (53, 62),    # נג - סב
    (63, 72),    # סג - עב
    (73, 79),    # עג - עט
    (80, 89),    # פ - פט
    (90, 106),   # צ - קו
    (107, 120),  # קז - קכ
    (121, 138),  # קכא - קלח
    (139, 150),  # קלט - קנ
]

# Aseres Yemei Teshuvah: 5 blocks to complete the whole sefer once
AYT_BLOCKS: list[tuple[int, int]] = [
    (1, 41),     # א - מא
    (42, 72),    # מב - עב
    (73, 89),    # עג - פט
    (90, 120),   # צ - קכ
    (121, 150),  # קכא - קנ
]


def _clean_heb(s: str) -> str:
    """Strip geresh/gershayim to match your display style."""
    return s.replace("׳", "").replace("״", "")


def _label(start: int, end: int) -> str:
    return f"{_clean_heb(int_to_hebrew(start))} - {_clean_heb(int_to_hebrew(end))}"


def _normal_5_block_labels() -> list[str]:
    labels = []
    for i in range(CYCLE_LENGTH):
        s = i * BLOCK_SIZE + 1
        e = min(s + BLOCK_SIZE - 1, CHAPTER_COUNT)
        labels.append(_label(s, e))
    return labels

def _labels_from_ranges(ranges: Iterable[tuple[int, int]]) -> list[str]:
    return [_label(s, e) for (s, e) in ranges]

# Precompute label sets
NORMAL_LABELS = _normal_5_block_labels()
ELUL_LABELS = _labels_from_ranges(ELUL_BLOCKS)
AYT_LABELS = _labels_from_ranges(AYT_BLOCKS)

# Union (keep order: Regular → Elul → AYT; remove dups while preserving order)
ALL_LABELS = list(dict.fromkeys(NORMAL_LABELS + ELUL_LABELS + AYT_LABELS))


class TehilimDailyPupaSensor(YidCalDisplayDevice, SensorEntity):
    """
    Tehilim Daily — Pupa minhag.

    Rules:
      • Skip: Shabbos / Yom Tov / Hoshana Rabba / Rosh Chodesh /
              Chol HaMoed / Isru Chag (Pesach, Shavuos, Sukkos) /
              Chanukah / Purim & Shushan Purim / Erev Pesach / Erev Sukkos
      • Regular days: 5-per-day blocks (א-ה, ו-י, …) and cycle across permitted days only
      • In Elul: use the 12 special divisions (advance on permitted days, wrap as needed)
      • In Aseres Yemei Teshuvah: use the 5 special divisions (advance on permitted days)
      • Reset point: first permitted day AFTER Isru Chag Sukkos each (Hebrew) year → start from א-ה
    """

    _attr_icon = "mdi:book-open-variant"
    _attr_name = "Tehilim Daily – Pupa"
    _attr_unique_id = "yidcal_tehilim_daily_pupa"
    _attr_extra_state_attributes: dict[str, Any] = {}

    def __init__(self, hass: HomeAssistant, yidcal_helper) -> None:
        super().__init__(hass, yidcal_helper)
        self.hass = hass
        self.entity_id = "sensor.yidcal_tehilim_daily_pupa"
        self._state: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # initial compute
        self.update()

        # refresh after midnight (civil)
        self._register_listener(
            async_track_time_change(
                self.hass, self._handle_midnight, hour=0, minute=0, second=1
            )
        )

        self._register_listener(
            async_track_state_change_event(
                self.hass, "binary_sensor.yidcal_no_melucha", self._handle_midnight
            )
        )
        self._register_listener(
            async_track_state_change_event(
                self.hass, "sensor.yidcal_holiday", self._handle_midnight
            )
        )

    async def _handle_midnight(self, *_):
        self.async_schedule_update_ha_state(True)

    @property
    def state(self) -> str | None:
        return self._state

    # ──────────────────────────────────────────────────────────────────
    # Core helpers (pure-date logic; no HA I/O here)
    # ──────────────────────────────────────────────────────────────────
    def _is_permitted_date(self, d: date) -> bool:
        """Pupa skip rules evaluated on Gregorian date d."""
        # Shabbos (weekday: Monday=0 .. Sunday=6)
        if d.weekday() == 5:
            return False

        hd = PHebrewDate.from_pydate(d)

        # Hard Yom Tov / YK / RH via pyluach
        if hd.festival(include_working_days=False) is not None:
            return False

        # Hoshana Rabba
        if hd.month == 7 and hd.day == 21:
            return False

        # Rosh Chodesh (exclude RH 1 Tishrei)
        if (hd.day in (1, 30)) and not (hd.month == 7 and hd.day == 1):
            return False

        # Chol HaMoed Sukkos: 17–20 Tishrei (21 is HR above)
        if hd.month == 7 and 17 <= hd.day <= 20:
            return False

        # Chol HaMoed Pesach: 17–20 Nisan
        if hd.month == 1 and 17 <= hd.day <= 20:
            return False

        # Isru Chag (Pesach 23 Nisan, Shavuos 8 Sivan, Sukkos 24 Tishrei)
        if (hd.month, hd.day) in [(1, 23), (3, 8), (7, 24)]:
            return False

        # Chanukah (25–30 Kislev + 1–2 Teves)
        if (hd.month == 9 and 25 <= hd.day <= 30) or (hd.month == 10 and hd.day in (1, 2)):
            return False

        # Purim / Shushan Purim (Adar / Adar II)
        if hd.month in (12, 13) and hd.day in (14, 15):
            return False

        # Erev Sukkos (14 Tishrei)
        if hd.month == 7 and hd.day == 14:
            return False

        # Erev Pesach: 14 Nisan; if 14 Nisan is Shabbos → also skip 13 (מוקדם)
        if hd.month == 1 and hd.day == 14:
            return False
        nisan14 = PHebrewDate(hd.year, 1, 14).to_pydate()
        if nisan14.weekday() == 5 and hd.month == 1 and hd.day == 13:
            return False

        return True
        
    def _is_pupa_extra_skip(self, d: date) -> bool:
        """Skips independent of melacha (no Shabbos/Yom Tov here)."""
        hd = PHebrewDate.from_pydate(d)

        # Hoshana Rabba
        if hd.month == 7 and hd.day == 21:
            return True

        # Rosh Chodesh (exclude RH 1 Tishrei)
        if (hd.day in (1, 30)) and not (hd.month == 7 and hd.day == 1):
            return True

        # Chol HaMoed Sukkos/Pesach
        if (hd.month == 7 and 17 <= hd.day <= 20) or (hd.month == 1 and 17 <= hd.day == 20):
            return True

        # Isru Chag
        if (hd.month, hd.day) in [(1, 23), (3, 8), (7, 24)]:
            return True

        # Chanukah
        if (hd.month == 9 and 25 <= hd.day <= 30) or (hd.month == 10 and hd.day in (1, 2)):
            return True

        # Purim / Shushan Purim
        if hd.month in (12, 13) and hd.day in (14, 15):
            return True

        # Erev Sukkos / Erev Pesach (+ מוקדם if 14 Nisan is Shabbos)
        if (hd.month == 7 and hd.day == 14) or (hd.month == 1 and hd.day == 14):
            return True
        nisan14 = PHebrewDate(hd.year, 1, 14).to_pydate()
        if nisan14.weekday() == 5 and hd.month == 1 and hd.day == 13:
            return True

        return False

    def _first_permitted_on_or_after(self, start: date) -> date:
        d = start
        for _ in range(120):  # safety bound
            if self._is_permitted_date(d):
                return d
            d += timedelta(days=1)
        return start

    def _annual_anchor_after_isru_sukkos(self, today: date) -> date:
        """
        Return the first permitted date after Isru Chag Sukkos (24 Tishrei)
        for the correct Hebrew year anchor:
          • If today > 24 Tishrei (of this Hebrew year) → use this year's 24 Tishrei
          • Else (on/before 24 Tishrei) → use last year's 24 Tishrei
        """
        hyear = PHebrewDate.from_pydate(today).year
        isru_this = PHebrewDate(hyear, 7, 24).to_pydate()
        anchor_year = hyear if today > isru_this else hyear - 1
        isru = PHebrewDate(anchor_year, 7, 24).to_pydate()
        return self._first_permitted_on_or_after(isru + timedelta(days=1))

    def _count_permitted_days(self, start: date, end: date) -> int:
        """Inclusive count of permitted days in [start, end]."""
        if end < start:
            return 0
        days = 0
        d = start
        while d <= end:
            if self._is_permitted_date(d):
                days += 1
            d += timedelta(days=1)
        return days

    def _nth_permitted_since(self, start: date, end: date) -> int:
        """1-based index of the current permitted day since 'start' (inclusive)."""
        return self._count_permitted_days(start, end)

    @staticmethod
    def _elul_bounds_for_year(hyear: int) -> tuple[date, date]:
        # 1 Elul … 29 Elul  (Elul has 29 days)
        start = PHebrewDate(hyear, 6, 1).to_pydate()
        end = PHebrewDate(hyear, 6, 29).to_pydate()
        return (start, end)

    @staticmethod
    def _ayt_bounds_for_year(hyear: int) -> tuple[date, date]:
        # 3 Tishrei … 9 Tishrei (inclusive)
        start = PHebrewDate(hyear, 7, 3).to_pydate()
        end = PHebrewDate(hyear, 7, 9).to_pydate()
        return (start, end)

    # ──────────────────────────────────────────────────────────────────
    # Main recompute
    # ──────────────────────────────────────────────────────────────────
    def update(self) -> None:
        today = dt_util.now().date()
        hd_today = PHebrewDate.from_pydate(today)

        is_skip_today = self.hass.states.is_state("binary_sensor.yidcal_no_melucha", "on")
        hol = self.hass.states.get("sensor.yidcal_holiday")
        if hol and hol.attributes.get("hoshana_raba", False):
            is_skip_today = True
    
        # Only apply Pupa extras that are independent of melacha
        if not is_skip_today and self._is_pupa_extra_skip(today):
            is_skip_today = True

        in_elul = (hd_today.month == 6)
        in_ayt = (hd_today.month == 7 and 3 <= hd_today.day <= 9)

        # Start with ALL labels present (Regular + Elul + AYT), all False
        attrs: dict[str, bool | str] = {L: False for L in ALL_LABELS}
        scheme = "regular"

        if in_ayt:
            scheme = "aseres_yemei_teshuvah"
            if is_skip_today:
                self._state = ""
            else:
                ay_start, ay_end = self._ayt_bounds_for_year(hd_today.year)
                anchor = self._first_permitted_on_or_after(ay_start)
                if anchor > ay_end:
                    self._state = ""
                else:
                    n = self._nth_permitted_since(anchor, today)
                    idx = (n - 1) % len(AYT_BLOCKS)
                    s, e = AYT_BLOCKS[idx]
                    label = _label(s, e)
                    attrs[label] = True
                    self._state = label

        elif in_elul:
            scheme = "elul"
            if is_skip_today:
                self._state = ""
            else:
                elul_start, elul_end = self._elul_bounds_for_year(hd_today.year)
                anchor = self._first_permitted_on_or_after(elul_start)
                if anchor > elul_end:
                    self._state = ""
                else:
                    n = self._nth_permitted_since(anchor, today)
                    # Keep advancing across permitted days; wrap the 12-block list naturally.
                    idx = (n - 1) % len(ELUL_BLOCKS)
                    s, e = ELUL_BLOCKS[idx]
                    label = _label(s, e)
                    attrs[label] = True
                    self._state = label

        else:
            scheme = "regular"
            if is_skip_today:
                self._state = ""
            else:
                anchor = self._annual_anchor_after_isru_sukkos(today)
                n = self._nth_permitted_since(anchor, today)
                idx = (n - 1) % CYCLE_LENGTH
                start_ch = idx * BLOCK_SIZE + 1
                end_ch = min(start_ch + BLOCK_SIZE - 1, CHAPTER_COUNT)
                label = _label(start_ch, end_ch)
                attrs[label] = True
                self._state = label

        # Expose everything
        attrs["Scheme"] = scheme
        self._attr_extra_state_attributes = attrs
