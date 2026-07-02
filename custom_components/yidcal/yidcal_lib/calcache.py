"""Small shared caches for pure calendar lookups.

``HDateInfo(...).is_yom_tov`` re-filters and re-sorts hdate's whole holiday
registry on every property access — measured at ~0.25 ms per call on a Pi.
Sensors that scan hundreds of days per update (three-day-yomtov,
no-melucha lookahead, upcoming sensors) pay that cost thousands of times a
minute. The answers are pure functions of (civil date, diaspora), so a
bounded LRU cache is always safe.
"""
from __future__ import annotations

import datetime
from functools import lru_cache

from hdate import HDateInfo


@lru_cache(maxsize=16384)
def is_yom_tov(d: datetime.date, diaspora: bool) -> bool:
    """Cached ``HDateInfo(d, diaspora=...).is_yom_tov``."""
    return HDateInfo(d, diaspora=diaspora).is_yom_tov
