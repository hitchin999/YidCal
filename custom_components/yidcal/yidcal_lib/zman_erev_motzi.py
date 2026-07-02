"""
custom_components/yidcal/yidcal_lib/zman_erev_motzi.py

Shared, pure-function helper that computes the Erev candle-lighting and
no-melucha-block-end (Motzi/Havdalah) times for any given date.

Used by:
  • UpcomingShabbosZmanimSensor       (target = upcoming Saturday)
  • UpcomingYomTovZmanimSensor        (target = first day of YT block)
  • ZmanimLookupSensor                (target = arbitrary user date)

Behavior matches the integration's existing Zman Erev / Zman Motzi
sensors (and follows the user's preference of skipping YT-to-YT and
Motzei-Shabbos-into-YT lightings, since those are at Tzeis and the
daily zmanim already surface Tzeis itself).
"""
from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from hdate import HDateInfo
from zmanim.util.geo_location import GeoLocation
from .zman_compute import (
    round_half_up as _half_up_minute,
    round_ceil as _ceil_minute,
    sunset_for_date,
)

from ..zman_sensors import lighting_event_for_day, _no_melacha_block


def compute_erev_motzi(
    target: date_cls,
    *,
    diaspora: bool,
    geo: GeoLocation,
    tz: ZoneInfo,
    candle_offset: int,
    havdalah_offset: int,
) -> dict[str, datetime]:
    """Return ordered dict of Erev/Motzi datetimes for ``target``.

    Keys (omitted when not applicable):
      • ``"הדלקת נרות"`` — the entry candle-lighting time for the
        no-melucha block ``target`` belongs to (or is the Erev of).
        Pulled from the day BEFORE the block starts, so this fires
        whether the user looks up the Erev day, Day 1, or any later
        day in the block. Covers Erev Shabbos, Erev Yom Tov on a
        weekday, and a Yom Tov day when the next day is Shabbos
        ("Shabbos as the 2nd/3rd day"). YT-to-YT and
        Motzei-Shabbos-into-YT lightings (at Tzeis) are intentionally
        skipped — those times are already on the daily zmanim list.
      • ``"מוצאי שבת"`` or ``"מוצאי יום טוב"`` — havdalah time of the
        no-melucha block ``target`` belongs to (or the block it is the
        Erev of). On multi-day spans this is always the FINAL day's
        havdalah. Label follows the block's last day: YT wins over
        Shabbos when both apply, matching the Zman-app convention.

    Both keys are anchored to the same no-melucha block, so they're
    either both present (target is in/Erev of a block) or both absent
    (regular weekday with no YT/Shabbos coming up).

    Values are aware ``datetime`` objects in ``tz``. Caller formats them.
    """
    out: dict[str, datetime] = {}

    # Identify the no-melucha block ``target`` is associated with — either
    # because target is *inside* the block (e.g. looking up Yom Kippur
    # itself) or because target is the Erev of the block (e.g. looking
    # up the Friday before Shabbos).
    block = _no_melacha_block(target, diaspora=diaspora)
    if block is None:
        tomorrow = target + timedelta(days=1)
        if (
            tomorrow.weekday() == 5
            or HDateInfo(tomorrow, diaspora=diaspora).is_yom_tov
        ):
            block = _no_melacha_block(tomorrow, diaspora=diaspora)

    # ── Candle lighting ──
    # Always pull from the day before the block starts (the actual Erev),
    # not from ``target`` itself. Without this, looking up Yom Kippur day
    # would miss the Erev YK candle lighting; looking up Pesach Day 1
    # would miss the Erev Pesach candle lighting; etc.
    #
    # When target is outside any block (a regular weekday with no YT/
    # Shabbos coming up), block is None — no candle lighting attribute
    # is emitted.
    erev_source = block[0] - timedelta(days=1) if block is not None else None
    if erev_source is not None:
        event_dt, kind = lighting_event_for_day(
            erev_source,
            diaspora=diaspora,
            tz=tz,
            geo=geo,
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )
        if event_dt is not None and kind == "erev_before_sunset":
            out["הדלקת נרות"] = _half_up_minute(event_dt)

    # ── Motzi (block end) ──
    if block is not None:
        _start, end = block
        sunset_end = sunset_for_date(geo=geo, tz=tz, base_date=end)
        motzi_dt = _ceil_minute(sunset_end + timedelta(minutes=havdalah_offset))

        last_is_yt = HDateInfo(end, diaspora=diaspora).is_yom_tov
        label = "מוצאי יום טוב" if last_is_yt else "מוצאי שבת"
        out[label] = motzi_dt

    return out
