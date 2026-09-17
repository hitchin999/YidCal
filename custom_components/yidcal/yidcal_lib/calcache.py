"""Small shared caches for pure calendar lookups.

``HDateInfo(...).is_yom_tov`` re-filters and re-sorts hdate's whole holiday
registry on every property access — measured at ~0.25 ms per call on a Pi,
and several milliseconds on a low-spec VM. Sensors that scan hundreds of
days per update (eruv tavshilin, no-melucha, three-day-yomtov, the flag
lookahead) pay that cost thousands of times. So ``is_yom_tov`` below answers
from the Hebrew date directly, using hdate's own Yom Tov table, and the
sensors' Yom Tov checks go through it. The answers are pure functions of
(civil date, diaspora), so the LRU cache is always safe.
"""
from __future__ import annotations

import datetime
from functools import lru_cache

from pyluach.dates import HebrewDate as _HebrewDate

# hdate 1.2.1's YOM_TOV holidays, by (pyluach month, day): Nisan = 1, Sivan = 3,
# Tishrei = 7. In Israel שמחת תורה is שמיני עצרת (22 Tishrei), already listed.
_YOM_TOV = frozenset({(7, 1), (7, 2), (7, 10), (7, 15), (7, 22), (1, 15), (1, 21), (3, 6)})
_YOM_TOV_DIASPORA = _YOM_TOV | {(7, 16), (7, 23), (1, 16), (1, 22), (3, 7)}


@lru_cache(maxsize=16384)
def is_yom_tov(d: datetime.date, diaspora: bool) -> bool:
    """Same answer as ``HDateInfo(d, diaspora=...).is_yom_tov`` (checked for
    every day 1900-2100 in both modes), without hdate's per-call cost."""
    h = _HebrewDate.from_pydate(d)
    return (h.month, h.day) in (_YOM_TOV_DIASPORA if diaspora else _YOM_TOV)


class _YomTovDay:
    """Stand-in for an ``HDateInfo`` that is only ever asked ``.is_yom_tov``."""

    __slots__ = ("is_yom_tov",)

    def __init__(self, d: datetime.date, diaspora: bool) -> None:
        self.is_yom_tov = is_yom_tov(d, diaspora)


def yom_tov_day(d: datetime.date, diaspora: bool) -> _YomTovDay:
    """``HDateInfo(d, diaspora=...)`` for code that only reads ``.is_yom_tov``."""
    return _YomTovDay(d, diaspora)
