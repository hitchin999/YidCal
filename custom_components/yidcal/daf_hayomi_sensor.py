# custom_components/yidcal/daf_hayomi_sensor.py
"""
Sensor: Daf HaYomi (דף היומי)

Computes today's Daf Yomi page based on the standard 2,711-day cycle.

State: "מסכת דף ד׳" (masechta + daf in Hebrew numerals)

Attributes:
  Masechta           – Hebrew masechta name
  Masechta_English   – Transliterated masechta name
  Daf                – Daf number (integer)
  Daf_Hebrew         – Daf in Hebrew numerals (e.g. ב׳, ק״ע)
  Cycle_Number       – Which Daf Yomi cycle we're in
  Day_In_Cycle       – Day number within the current cycle (1-based)
"""

from __future__ import annotations

from datetime import date, timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .device import YidCalDisplayDevice
from .const import DOMAIN

# ── Cycle epoch ──
# The 14th Daf Yomi cycle began on 7 Teves 5780 = January 5, 2020.
_CYCLE_14_START = date(2020, 1, 5)
_CYCLE_14_NUMBER = 14

# ── Masechtos table ──
# (Hebrew name, English name, last_daf)
# Dapim studied = last_daf - 1  (each masechta starts at daf 2).
# "Meilah" (last_daf=37) includes Kinnim, Tamid & Middos continuation pages.
_MASECHTOS: list[tuple[str, str, int]] = [
    ("ברכות",      "Berachos",       64),
    ("שבת",        "Shabbos",       157),
    ("עירובין",     "Eruvin",        105),
    ("פסחים",      "Pesachim",      121),
    ("שקלים",      "Shekalim",       22),
    ("יומא",       "Yoma",           88),
    ("סוכה",       "Sukkah",         56),
    ("ביצה",       "Beitzah",        40),
    ("ראש השנה",   "Rosh Hashanah",  35),
    ("תענית",      "Taanis",         31),
    ("מגילה",      "Megillah",       32),
    ("מועד קטן",   "Moed Katan",     29),
    ("חגיגה",      "Chagigah",       27),
    ("יבמות",      "Yevamos",       122),
    ("כתובות",     "Kesubos",       112),
    ("נדרים",      "Nedarim",        91),
    ("נזיר",       "Nazir",          66),
    ("סוטה",       "Sotah",          49),
    ("גיטין",      "Gittin",         90),
    ("קידושין",    "Kiddushin",      82),
    ("בבא קמא",    "Bava Kamma",    119),
    ("בבא מציעא",   "Bava Metzia",   119),
    ("בבא בתרא",    "Bava Basra",    176),
    ("סנהדרין",    "Sanhedrin",     113),
    ("מכות",       "Makkos",         24),
    ("שבועות",     "Shevuos",        49),
    ("עבודה זרה",   "Avodah Zarah",   76),
    ("הוריות",     "Horayos",        14),
    ("זבחים",      "Zevachim",      120),
    ("מנחות",      "Menachos",      110),
    ("חולין",      "Chullin",       142),
    ("בכורות",     "Bechoros",       61),
    ("ערכין",      "Arachin",        34),
    ("תמורה",      "Temurah",        34),
    ("כריתות",     "Kerisus",        28),
    ("מעילה",      "Meilah",         37),   # includes Kinnim, Tamid, Middos
    ("נדה",        "Niddah",         73),
]

# Total dapim in one cycle
_CYCLE_LENGTH = sum(last - 1 for _, _, last in _MASECHTOS)  # = 2711


# ── Hebrew numeral helper ──

def _to_hebrew_numeral(n: int) -> str:
    """Convert an integer (2–999) to Hebrew numerals with geresh/gershayim."""
    hundreds = [
        "", "ק", "ר", "ש", "ת", "תק", "תר", "תש", "תת", "תתק",
    ]
    tens = [
        "", "י", "כ", "ל", "מ", "נ", "ס", "ע", "פ", "צ",
    ]
    ones = [
        "", "א", "ב", "ג", "ד", "ה", "ו", "ז", "ח", "ט",
    ]

    h = n // 100
    t = (n % 100) // 10
    u = n % 10

    # Special cases for 15 and 16 (avoid spelling divine names)
    if t == 1 and u == 5:
        t, u = 0, 0
        parts = hundreds[h] + "טו"
    elif t == 1 and u == 6:
        t, u = 0, 0
        parts = hundreds[h] + "טז"
    else:
        parts = hundreds[h] + tens[t] + ones[u]

    if not parts:
        return ""

    # Add geresh (single letter) or gershayim (before last letter)
    if len(parts) == 1:
        return parts + "׳"
    else:
        return parts[:-1] + "״" + parts[-1]


def compute_daf_yomi(today: date) -> tuple[str, str, int, str, int, int]:
    """Return (hebrew_name, english_name, daf_number, daf_hebrew, cycle_number, day_in_cycle).

    day_in_cycle is 1-based.
    """
    days_since = (today - _CYCLE_14_START).days
    cycle_offset = days_since % _CYCLE_LENGTH
    if cycle_offset < 0:
        cycle_offset += _CYCLE_LENGTH

    cycle_number = _CYCLE_14_NUMBER + days_since // _CYCLE_LENGTH

    # Walk through masechtos
    remaining = cycle_offset
    for heb_name, eng_name, last_daf in _MASECHTOS:
        dapim = last_daf - 1  # pages in this masechta
        if remaining < dapim:
            daf = remaining + 2  # masechtos start at daf 2
            daf_heb = _to_hebrew_numeral(daf)
            return heb_name, eng_name, daf, daf_heb, cycle_number, cycle_offset + 1
        remaining -= dapim

    # Shouldn't reach here, but safety fallback to first daf
    heb, eng, last = _MASECHTOS[0]
    return heb, eng, 2, _to_hebrew_numeral(2), cycle_number, cycle_offset + 1


class DafHaYomiSensor(YidCalDisplayDevice, SensorEntity):
    """Today's Daf HaYomi."""

    _attr_name = "Daf HaYomi"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "daf_hayomi"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._state: str | None = None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self.async_update()
        # Recalculate at midnight
        self._register_listener(
            async_track_time_change(
                self.hass, self.async_update, hour=0, minute=0, second=5
            )
        )

    async def async_update(self, now=None) -> None:
        today = date.today()
        heb_name, eng_name, daf, daf_heb, cycle_num, day_in_cycle = compute_daf_yomi(today)

        self._state = f"{heb_name} דף {daf_heb}"

        self._attr_extra_state_attributes = {
            "Masechta": heb_name,
            "Masechta_English": eng_name,
            "Daf": daf,
            "Daf_Hebrew": daf_heb,
            "Cycle_Number": cycle_num,
            "Day_In_Cycle": day_in_cycle,
        }

        self.async_write_ha_state()
