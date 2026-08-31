"""
custom_components/yidcal/yidcal_lib/tzeis_options.py

The Tzeis HaKochavim opinions YidCal can compute, as a table.

The exact counterpart of ``alos_options`` on the other side of the day,
and deliberately the same shape (same dataclass fields, same key /
attr / slug conventions) so anything already generated from the Alos
table can be generated from this one without special-casing.

Three families:

  fixed    A flat number of clock minutes after sunset. 72 is the
           Rabbeinu-Tam figure and YidCal's historical default for the
           havdalah offset; 40/50/56/60/90/120 are the other printed
           ones.

  zmanis   The same numbers as *proportional* minutes — one "zmanis
           minute" is 1/60 of a GRA sha'ah zmanis ((sunset − sunrise)
           / 12). No row uses this yet; the kind is carried so a
           zmanis opinion can be added as one line later.

  degrees  The sun a given number of degrees below the horizon after
           sunset. This family is much larger than the Alos one
           because the printed tzeis opinions are: the small
           depressions (3.65°–4.8°) are the Geonim's 13½–18 minute
           figures, the middle band (5.95°–9.75°) the various
           three-stars opinions, and 16.1° / 18° / 19.8° / 26° the
           degree equivalents of 72 / 90 / 120 minutes.

IMPORTANT — these are ATTRIBUTES, not the state. ``sensor.yidcal_
tzies_hakochavim`` still takes its state from the configured
``havdalah_offset`` (sunset + N) via the coordinator. This table only
feeds the per-opinion attribute block, exactly as ALOS_OPTIONS feeds
the one on ``sensor.yidcal_alos``.

DATA ONLY — no astronomy, no Home Assistant. Computation lives in
``zman_compute.tzeis_for_date`` / ``all_tzeis_for_date``, which already
own the cached sun-event machinery. Keeping the table separate is what
lets zman_compute import it without a cycle.

Adding an opinion means adding one row.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Table default for an unknown/removed key, mirroring
#: ``alos_options.DEFAULT_ALOS_OPTION``. This is a KEY fallback only —
#: it picks which row ``get_tzeis_option`` returns when handed garbage.
#: It is NOT the fallback time: when a degree opinion cannot be
#: computed, ``zman_compute.tzeis_for_date`` falls back to the caller's
#: configured ``havdalah_offset``, not to this row.
DEFAULT_TZEIS_OPTION = "fixed_72"


@dataclass(frozen=True)
class TzeisOption:
    key: str        # option value stored in the config entry
    kind: str       # "fixed" | "zmanis" | "degrees"
    value: float    # minutes (fixed/zmanis) or degrees below horizon
    hebrew: str     # label for the yi/he config-flow selector
    english: str    # label for the en config-flow selector
    attr: str       # attribute name on sensor.yidcal_tzies_hakochavim
    slug: str       # entity-id suffix for an optional extra sensor


TZEIS_OPTIONS: list[TzeisOption] = [
    # ── flat clock minutes after sunset ──
    TzeisOption("fixed_40",  "fixed",  40, "40 מינוט נאך שקיעה",  "40 minutes after sunset",  "Tzies_40_Minutes",  "40_minutes"),
    TzeisOption("fixed_50",  "fixed",  50, "50 מינוט נאך שקיעה",  "50 minutes after sunset",  "Tzies_50_Minutes",  "50_minutes"),
    TzeisOption("fixed_56",  "fixed",  56, "56 מינוט נאך שקיעה",  "56 minutes after sunset",  "Tzies_56_Minutes",  "56_minutes"),
    TzeisOption("fixed_60",  "fixed",  60, "60 מינוט נאך שקיעה",  "60 minutes after sunset",  "Tzies_60_Minutes",  "60_minutes"),
    TzeisOption("fixed_72",  "fixed",  72, "72 מינוט נאך שקיעה",  "72 minutes after sunset (default)", "Tzies_72_Minutes", "72_minutes"),
    TzeisOption("fixed_90",  "fixed",  90, "90 מינוט נאך שקיעה",  "90 minutes after sunset",  "Tzies_90_Minutes",  "90_minutes"),
    TzeisOption("fixed_120", "fixed", 120, "120 מינוט נאך שקיעה", "120 minutes after sunset", "Tzies_120_Minutes", "120_minutes"),
    # ── degrees below the horizon ──
    TzeisOption("deg_3_65",  "degrees",  3.65, "3.65° אונטערן האריזאנט",  "3.65 degrees below the horizon",  "Tzies_3_65_Degrees",  "3_65_degrees"),
    TzeisOption("deg_3_7",   "degrees",  3.7,  "3.7° אונטערן האריזאנט",   "3.7 degrees below the horizon",   "Tzies_3_7_Degrees",   "3_7_degrees"),
    TzeisOption("deg_3_8",   "degrees",  3.8,  "3.8° אונטערן האריזאנט",   "3.8 degrees below the horizon",   "Tzies_3_8_Degrees",   "3_8_degrees"),
    TzeisOption("deg_4_37",  "degrees",  4.37, "4.37° אונטערן האריזאנט",  "4.37 degrees below the horizon",  "Tzies_4_37_Degrees",  "4_37_degrees"),
    TzeisOption("deg_4_61",  "degrees",  4.61, "4.61° אונטערן האריזאנט",  "4.61 degrees below the horizon",  "Tzies_4_61_Degrees",  "4_61_degrees"),
    TzeisOption("deg_4_8",   "degrees",  4.8,  "4.8° אונטערן האריזאנט",   "4.8 degrees below the horizon",   "Tzies_4_8_Degrees",   "4_8_degrees"),
    TzeisOption("deg_5_95",  "degrees",  5.95, "5.95° אונטערן האריזאנט",  "5.95 degrees below the horizon",  "Tzies_5_95_Degrees",  "5_95_degrees"),
    TzeisOption("deg_6",     "degrees",  6.0,  "6° אונטערן האריזאנט",     "6 degrees below the horizon",     "Tzies_6_Degrees",     "6_degrees"),
    TzeisOption("deg_6_45",  "degrees",  6.45, "6.45° אונטערן האריזאנט",  "6.45 degrees below the horizon",  "Tzies_6_45_Degrees",  "6_45_degrees"),
    TzeisOption("deg_7_083", "degrees",  7.083, "7.083° אונטערן האריזאנט", "7.083 degrees below the horizon", "Tzies_7_083_Degrees", "7_083_degrees"),
    TzeisOption("deg_7_1",   "degrees",  7.1,  "7.1° אונטערן האריזאנט",   "7.1 degrees below the horizon",   "Tzies_7_1_Degrees",   "7_1_degrees"),
    TzeisOption("deg_7_67",  "degrees",  7.67, "7.67° אונטערן האריזאנט",  "7.67 degrees below the horizon",  "Tzies_7_67_Degrees",  "7_67_degrees"),
    TzeisOption("deg_8",     "degrees",  8.0,  "8° אונטערן האריזאנט",     "8 degrees below the horizon",     "Tzies_8_Degrees",     "8_degrees"),
    TzeisOption("deg_8_5",   "degrees",  8.5,  "8.5° אונטערן האריזאנט",   "8.5 degrees below the horizon",   "Tzies_8_5_Degrees",   "8_5_degrees"),
    TzeisOption("deg_9_3",   "degrees",  9.3,  "9.3° אונטערן האריזאנט",   "9.3 degrees below the horizon",   "Tzies_9_3_Degrees",   "9_3_degrees"),
    TzeisOption("deg_9_75",  "degrees",  9.75, "9.75° אונטערן האריזאנט",  "9.75 degrees below the horizon",  "Tzies_9_75_Degrees",  "9_75_degrees"),
    TzeisOption("deg_16_1",  "degrees", 16.1,  "16.1° אונטערן האריזאנט",  "16.1 degrees below the horizon",  "Tzies_16_1_Degrees",  "16_1_degrees"),
    TzeisOption("deg_18",    "degrees", 18.0,  "18° אונטערן האריזאנט",    "18 degrees below the horizon",    "Tzies_18_Degrees",    "18_degrees"),
    TzeisOption("deg_19_8",  "degrees", 19.8,  "19.8° אונטערן האריזאנט",  "19.8 degrees below the horizon",  "Tzies_19_8_Degrees",  "19_8_degrees"),
    TzeisOption("deg_26",    "degrees", 26.0,  "26° אונטערן האריזאנט",    "26 degrees below the horizon",    "Tzies_26_Degrees",    "26_degrees"),
]

TZEIS_BY_KEY: dict[str, TzeisOption] = {opt.key: opt for opt in TZEIS_OPTIONS}


def get_tzeis_option(key: str | None) -> TzeisOption:
    """The option for ``key``, falling back to the default.

    Total by design, same as ``alos_options.get_option``: an unknown key
    (a hand-edited config entry, or an option removed in a later
    release) must degrade to 72 minutes rather than take a sensor down.
    """
    return TZEIS_BY_KEY.get(key or "", TZEIS_BY_KEY[DEFAULT_TZEIS_OPTION])
