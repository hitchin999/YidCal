# erev_motzei_extra.py
"""The Erev / Motzei flags: ערב שבת, ערב יום טוב, מוצאי שבת, מוצאי יום טוב,
the שחל ביום טוב / שחל בשבת variants, and שבת ערב פורים.

Their rules and windows now live in halacha_events.FLAG_SPECS with every other
holiday flag. These functions keep this module's call signatures working on top
of that single source.
"""
from __future__ import annotations

import datetime
from typing import Dict
from zoneinfo import ZoneInfo

from zmanim.util.geo_location import GeoLocation

from .yidcal_lib import halacha_events as he

EXTRA_ATTRS = [
    "ערב שבת",
    "ערב יום טוב",
    "מוצאי שבת",
    "מוצאי יום טוב",
    "ערב שבת שחל ביום טוב",
    "ערב יום טוב שחל בשבת",
    "מוצאי שבת שחל ביום טוב",
    "מוצאי יום טוב שחל בשבת",
    "שבת ערב פורים",
]


def compute_erev_motzei_flags_and_windows(
    *,
    now: datetime.datetime,
    tz: ZoneInfo,
    geo: GeoLocation,
    diaspora: bool,
    candle_offset: int,
    havdalah_offset: int,
) -> tuple[Dict[str, bool], Dict[str, tuple[datetime.datetime, datetime.datetime]]]:
    """The flags keyed by their Hebrew names, and the window of each flag that is on."""
    on = he.evaluate_flag_specs(
        now=now, tz=tz, geo=geo, diaspora=diaspora,
        candle_offset=candle_offset, havdalah_offset=havdalah_offset,
        names=EXTRA_ATTRS,
    )
    return {name: name in on for name in EXTRA_ATTRS}, on


def compute_erev_motzei_flags(
    *,
    now: datetime.datetime,
    tz: ZoneInfo,
    geo: GeoLocation,
    diaspora: bool,
    candle_offset: int,
    havdalah_offset: int,
) -> Dict[str, bool]:
    """The flags on their own - the signature this module has always had."""
    flags, _windows = compute_erev_motzei_flags_and_windows(
        now=now,
        tz=tz,
        geo=geo,
        diaspora=diaspora,
        candle_offset=candle_offset,
        havdalah_offset=havdalah_offset,
    )
    return flags
