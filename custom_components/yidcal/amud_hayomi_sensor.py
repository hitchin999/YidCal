# custom_components/yidcal/amud_hayomi_sensor.py
"""
Sensor: Amud HaYomi (עמוד היומי) — Dirshu Cycle

Computes today's Amud HaYomi (one amud per day, 7 days/week)
through the same Shas masechtos as Daf Yomi, at half pace.

State: "מסכת דף ד׳ עמוד א/ב" (masechta + daf + side)

Attributes:
  Masechta           – Hebrew masechta name
  Masechta_English   – Transliterated masechta name
  Daf                – Daf number (integer)
  Daf_Hebrew         – Daf in Hebrew numerals
  Amud               – "א" or "ב"
  Amud_English       – "a" or "b"
  Cycle_Number       – Which Amud HaYomi cycle we're in
  Day_In_Cycle       – Day number within the current cycle (1-based)
"""

from __future__ import annotations

from datetime import date

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .device import YidCalDisplayDevice
from .const import DOMAIN
from .daf_hayomi_sensor import _to_hebrew_numeral

# ── Dirshu cycle 1 epoch ──
# Launched 1 Cheshvan 5784 = October 15, 2023 with Berachos 2a.
_CYCLE_1_EPOCH = date(2023, 10, 15)  # day 0 = Berachos 2a
_CYCLE_1_NUMBER = 1

# ── Masechtos table ──
# Same order as Daf Yomi Bavli cycle.
# (Hebrew name, English name, amud_count)
# Standard formula: amudim = (last_daf - 1) * 2
# Eruvin adjusted to 207 (105b is hadran-only in the Dirshu schedule),
# calibrated against verified dates: Pesachim 2a = Jul 21, 2025;
# Arvei Pesachim 99b = Feb 1, 2026.
_MASECHTOS: list[tuple[str, str, int]] = [
    ("ברכות",      "Berachos",      126),  # 64-1=63 dapim × 2
    ("שבת",        "Shabbos",       312),  # 157-1=156 × 2
    ("עירובין",     "Eruvin",        207),  # calibrated (standard=208)
    ("פסחים",      "Pesachim",      240),  # 121-1=120 × 2
    ("שקלים",      "Shekalim",       42),  # 22-1=21 × 2
    ("יומא",       "Yoma",          174),  # 88-1=87 × 2
    ("סוכה",       "Sukkah",        110),  # 56-1=55 × 2
    ("ביצה",       "Beitzah",        78),  # 40-1=39 × 2
    ("ראש השנה",   "Rosh Hashanah",  68),  # 35-1=34 × 2
    ("תענית",      "Taanis",         60),  # 31-1=30 × 2
    ("מגילה",      "Megillah",       62),  # 32-1=31 × 2
    ("מועד קטן",   "Moed Katan",     56),  # 29-1=28 × 2
    ("חגיגה",      "Chagigah",       52),  # 27-1=26 × 2
    ("יבמות",      "Yevamos",       242),  # 122-1=121 × 2
    ("כתובות",     "Kesubos",       222),  # 112-1=111 × 2
    ("נדרים",      "Nedarim",       180),  # 91-1=90 × 2
    ("נזיר",       "Nazir",         130),  # 66-1=65 × 2
    ("סוטה",       "Sotah",          96),  # 49-1=48 × 2
    ("גיטין",      "Gittin",        178),  # 90-1=89 × 2
    ("קידושין",    "Kiddushin",     162),  # 82-1=81 × 2
    ("בבא קמא",    "Bava Kamma",    236),  # 119-1=118 × 2
    ("בבא מציעא",   "Bava Metzia",   236),  # 119-1=118 × 2
    ("בבא בתרא",    "Bava Basra",    350),  # 176-1=175 × 2
    ("סנהדרין",    "Sanhedrin",     224),  # 113-1=112 × 2
    ("מכות",       "Makkos",         46),  # 24-1=23 × 2
    ("שבועות",     "Shevuos",        96),  # 49-1=48 × 2
    ("עבודה זרה",   "Avodah Zarah",  150),  # 76-1=75 × 2
    ("הוריות",     "Horayos",        26),  # 14-1=13 × 2
    ("זבחים",      "Zevachim",      238),  # 120-1=119 × 2
    ("מנחות",      "Menachos",      218),  # 110-1=109 × 2
    ("חולין",      "Chullin",       282),  # 142-1=141 × 2
    ("בכורות",     "Bechoros",      120),  # 61-1=60 × 2
    ("ערכין",      "Arachin",        66),  # 34-1=33 × 2
    ("תמורה",      "Temurah",        66),  # 34-1=33 × 2
    ("כריתות",     "Kerisus",        54),  # 28-1=27 × 2
    ("מעילה",      "Meilah",         72),  # 37-1=36 × 2 (incl. Kinnim/Tamid/Middos)
    ("נדה",        "Niddah",        144),  # 73-1=72 × 2
]

# Total amudim in one cycle
_CYCLE_LENGTH = sum(a for _, _, a in _MASECHTOS)  # = 5421


def compute_amud_hayomi(today: date) -> tuple[str, str, int, str, str, str, int, int]:
    """Return (hebrew_name, english_name, daf, daf_hebrew, amud_heb, amud_eng,
              cycle_number, day_in_cycle).

    day_in_cycle is 1-based.
    """
    days_since = (today - _CYCLE_1_EPOCH).days  # Oct 15 = 0 = Berachos 2a
    amud_offset = days_since % _CYCLE_LENGTH
    if amud_offset < 0:
        amud_offset += _CYCLE_LENGTH

    cycle_number = _CYCLE_1_NUMBER + days_since // _CYCLE_LENGTH

    # Walk through masechtos
    remaining = amud_offset
    for heb_name, eng_name, amud_count in _MASECHTOS:
        if remaining < amud_count:
            daf = remaining // 2 + 2  # masechtos start at daf 2
            side = remaining % 2       # 0 = amud a, 1 = amud b
            daf_heb = _to_hebrew_numeral(daf)
            amud_heb = "א" if side == 0 else "ב"
            amud_eng = "a" if side == 0 else "b"
            return (
                heb_name, eng_name, daf, daf_heb,
                amud_heb, amud_eng, cycle_number,
                (amud_offset + 1),
            )
        remaining -= amud_count

    # Safety fallback
    heb, eng, _ = _MASECHTOS[0]
    return heb, eng, 2, _to_hebrew_numeral(2), "א", "a", cycle_number, 1


class AmudHaYomiSensor(YidCalDisplayDevice, SensorEntity):
    """Today's Amud HaYomi (Dirshu)."""

    _attr_name = "Amud HaYomi"
    _attr_icon = "mdi:book-open-page-variant-outline"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "amud_hayomi"
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
        (heb_name, eng_name, daf, daf_heb,
         amud_heb, amud_eng, cycle_num, day_in_cycle) = compute_amud_hayomi(today)

        self._state = f"{heb_name} דף {daf_heb} עמוד {amud_heb}"

        self._attr_extra_state_attributes = {
            "Masechta": heb_name,
            "Masechta_English": eng_name,
            "Daf": daf,
            "Daf_Hebrew": daf_heb,
            "Amud": amud_heb,
            "Amud_English": amud_eng,
            "Cycle_Number": cycle_num,
            "Day_In_Cycle": day_in_cycle,
        }

        self.async_write_ha_state()
