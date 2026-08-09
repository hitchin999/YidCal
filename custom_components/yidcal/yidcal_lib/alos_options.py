"""
custom_components/yidcal/yidcal_lib/alos_options.py

The Alos HaShachar opinions YidCal can compute, as a table.

Three families, matching the ones KosherJava / python-zmanim expose and
that published luachs actually use:

  fixed    A flat number of clock minutes before sunrise. 72 is the
           common MGA figure and YidCal's historical (and default)
           behaviour; 60, 90, 96 and 120 are the other printed ones.

  zmanis   The same numbers, but as *proportional* minutes — one
           "zmanis minute" is 1/60 of a GRA sha'ah zmanis
           ((sunset − sunrise) / 12), so the interval stretches in
           summer and shrinks in winter.

  degrees  The sun a given number of degrees below the horizon. 16.1°
           is the classic equivalent of 72 minutes at the Jerusalem
           equinox; 19.8° corresponds to 120. These track the season
           the way the sky actually does, which is why they diverge
           sharply from the fixed figures at high latitude.

This module is DATA ONLY — no astronomy, no Home Assistant. The
computation lives in ``zman_compute.alos_for_date``, which already owns
the cached sun-event machinery every opinion here is built on. Keeping
the table separate is what lets zman_compute import it without a cycle.

Adding an opinion means adding one row. The config-flow selector, the
per-opinion attributes on ``sensor.yidcal_alos`` and the optional extra
Alos sensors are all generated from this list.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Historical behaviour, and what every existing install keeps unless
#: the user picks otherwise.
DEFAULT_ALOS_OPTION = "fixed_72"

#: Sentinel for "whatever the primary Alos is set to" — used by the
#: Talis & Tefilin base so that changing the primary opinion carries the
#: Talis time with it unless the user deliberately pinned it elsewhere.
FOLLOW_PRIMARY = "primary"


@dataclass(frozen=True)
class AlosOption:
    key: str        # option value stored in the config entry
    kind: str       # "fixed" | "zmanis" | "degrees"
    value: float    # minutes (fixed/zmanis) or degrees below horizon
    hebrew: str     # label for the yi/he config-flow selector
    english: str    # label for the en config-flow selector
    attr: str       # attribute name on sensor.yidcal_alos
    slug: str       # entity-id suffix for an optional extra sensor


ALOS_OPTIONS: list[AlosOption] = [
    # ── flat clock minutes before sunrise ──
    AlosOption("fixed_60",  "fixed",   60, "60 מינוט פאר נץ",  "60 minutes before sunrise",  "Alos_60_Minutes",  "60_minutes"),
    AlosOption("fixed_72",  "fixed",   72, "72 מינוט פאר נץ",  "72 minutes before sunrise (default)", "Alos_72_Minutes", "72_minutes"),
    AlosOption("fixed_90",  "fixed",   90, "90 מינוט פאר נץ",  "90 minutes before sunrise",  "Alos_90_Minutes",  "90_minutes"),
    AlosOption("fixed_96",  "fixed",   96, "96 מינוט פאר נץ",  "96 minutes before sunrise",  "Alos_96_Minutes",  "96_minutes"),
    AlosOption("fixed_120", "fixed",  120, "120 מינוט פאר נץ", "120 minutes before sunrise", "Alos_120_Minutes", "120_minutes"),
    # ── proportional (zmanis) minutes ──
    AlosOption("zmanis_72",  "zmanis",  72, "72 מינוט זמניות",  "72 zmanis minutes",  "Alos_72_Zmanis",  "72_zmanis"),
    AlosOption("zmanis_90",  "zmanis",  90, "90 מינוט זמניות",  "90 zmanis minutes",  "Alos_90_Zmanis",  "90_zmanis"),
    AlosOption("zmanis_96",  "zmanis",  96, "96 מינוט זמניות",  "96 zmanis minutes",  "Alos_96_Zmanis",  "96_zmanis"),
    AlosOption("zmanis_120", "zmanis", 120, "120 מינוט זמניות", "120 zmanis minutes", "Alos_120_Zmanis", "120_zmanis"),
    # ── degrees below the horizon ──
    AlosOption("deg_16_1", "degrees", 16.1, "16.1° אונטערן האריזאנט", "16.1 degrees below the horizon", "Alos_16_1_Degrees", "16_1_degrees"),
    AlosOption("deg_18",   "degrees", 18.0, "18° אונטערן האריזאנט",   "18 degrees below the horizon",   "Alos_18_Degrees",   "18_degrees"),
    AlosOption("deg_19",   "degrees", 19.0, "19° אונטערן האריזאנט",   "19 degrees below the horizon",   "Alos_19_Degrees",   "19_degrees"),
    AlosOption("deg_19_8", "degrees", 19.8, "19.8° אונטערן האריזאנט", "19.8 degrees below the horizon", "Alos_19_8_Degrees", "19_8_degrees"),
    AlosOption("deg_26",   "degrees", 26.0, "26° אונטערן האריזאנט",   "26 degrees below the horizon",   "Alos_26_Degrees",   "26_degrees"),
]

ALOS_BY_KEY: dict[str, AlosOption] = {opt.key: opt for opt in ALOS_OPTIONS}


def get_option(key: str | None) -> AlosOption:
    """The option for ``key``, falling back to the default.

    Total by design: an unknown key (a hand-edited config entry, or an
    option removed in a later release) must degrade to 72 minutes rather
    than take the Alos sensor down.
    """
    return ALOS_BY_KEY.get(key or "", ALOS_BY_KEY[DEFAULT_ALOS_OPTION])


def resolve_tallis_base(tallis_base: str | None, primary: str | None) -> str:
    """Which Alos opinion the Talis & Tefilin offset counts from."""
    if not tallis_base or tallis_base == FOLLOW_PRIMARY:
        return primary or DEFAULT_ALOS_OPTION
    return tallis_base if tallis_base in ALOS_BY_KEY else (
        primary or DEFAULT_ALOS_OPTION
    )
