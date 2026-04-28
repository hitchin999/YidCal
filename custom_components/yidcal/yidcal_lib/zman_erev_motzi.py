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
from zmanim.zmanim_calendar import ZmanimCalendar

from ..zman_sensors import lighting_event_for_day, _no_melacha_block


def _half_up_minute(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt = dt + timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _ceil_minute(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


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
      • ``"הדלקת נרות"`` — when ``target`` is an Erev with a
        before-sunset candle-lighting event. Covers Erev Shabbos, Erev
        Yom Tov on a weekday, and a Yom Tov day when the next day is
        Shabbos ("Shabbos as the 2nd/3rd day"). YT-to-YT and
        Motzei-Shabbos-into-YT lightings (at Tzeis) are intentionally
        skipped — those times are already on the daily zmanim list.
      • ``"מוצאי שבת"`` or ``"מוצאי יום טוב"`` — havdalah time of the
        no-melucha block ``target`` belongs to (or the block it is the
        Erev of). On multi-day spans this is always the FINAL day's
        havdalah. Label follows the block's last day: YT wins over
        Shabbos when both apply, matching the Zman-app convention.

    Values are aware ``datetime`` objects in ``tz``. Caller formats them.
    """
    out: dict[str, datetime] = {}

    # ── Candle lighting ──
    event_dt, kind = lighting_event_for_day(
        target,
        diaspora=diaspora,
        tz=tz,
        geo=geo,
        candle_offset=candle_offset,
        havdalah_offset=havdalah_offset,
    )
    if event_dt is not None and kind == "erev_before_sunset":
        out["הדלקת נרות"] = _half_up_minute(event_dt)

    # ── Motzi (block end) ──
    block = _no_melacha_block(target, diaspora=diaspora)
    if block is None:
        # `target` itself isn't in a block; check if tomorrow starts one,
        # making `target` the Erev of that block.
        tomorrow = target + timedelta(days=1)
        if (
            tomorrow.weekday() == 5
            or HDateInfo(tomorrow, diaspora=diaspora).is_yom_tov
        ):
            block = _no_melacha_block(tomorrow, diaspora=diaspora)

    if block is not None:
        _start, end = block
        sunset_end = (
            ZmanimCalendar(geo_location=geo, date=end)
            .sunset()
            .astimezone(tz)
        )
        motzi_dt = _ceil_minute(sunset_end + timedelta(minutes=havdalah_offset))

        last_is_yt = HDateInfo(end, diaspora=diaspora).is_yom_tov
        label = "מוצאי יום טוב" if last_is_yt else "מוצאי שבת"
        out[label] = motzi_dt

    return out
