"""
custom_components/yidcal/yidcal_lib/zman_compute.py

Shared, pure-function zmanim computation for YidCal.

Given a civil date + location + offsets, returns an ordered list of
(hebrew_label, local_aware_datetime) pairs for all daily zmanim, in
clock-time (chronological) order. Matches the rounding used by the
existing individual zman sensors:
  • Alos, Talis & Tefilin, Netz, Chatzos Hayom, Mincha Gedola,
    Mincha Ketana, Plag GRA, Plag MGA, Chatzos HaLaila → round half-up
  • Sof Zman Krias Shma (MGA/GRA), Sof Zman Tefilah (MGA/GRA) → floor
  • Shkia, Tzies, Zman Maariv 60 → ceil (chumra)

This is a pure helper with no Home Assistant dependency so it can be
reused by:
  • UpcomingShabbosZmanimSensor
  • UpcomingYomTovZmanimSensor
  • A future "check zmanim for a specific day" service call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation


# Default Talis & Tefilin offset (minutes after Alos) — matches
# existing zman_talis_tefilin.py default (Misheyakir style).
DEFAULT_TALLIS_TEFILIN_OFFSET = 22

# MGA Alos offset in minutes before sunrise (0°50′ ≈ 72 min).
_ALOS_OFFSET_MIN = 72


def _half_up(dt: datetime) -> datetime:
    """<30s floor, ≥30s ceil — matches Alos/Netz/Chatzos/Mincha/Plag style."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _floor(dt: datetime) -> datetime:
    """Floor to the minute — matches Krias Shma / Sof Zman Tefilah style."""
    return dt.replace(second=0, microsecond=0)


def _ceil(dt: datetime) -> datetime:
    """Always ceil to the next minute — matches Shkia / Tzies / Maariv 60 style."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


def format_simple_time(dt_local: datetime, fmt: str = "12") -> str:
    """Mirrors YidCalDevice._format_simple_time for use outside HA.

    fmt: "12" → '4:07 AM' style, "24" → '04:07' style.
    """
    if fmt == "24":
        return dt_local.strftime("%H:%M")
    hour = dt_local.hour % 12 or 12
    return f"{hour}:{dt_local.minute:02d} {'AM' if dt_local.hour < 12 else 'PM'}"


@dataclass(frozen=True)
class ZmanEntry:
    label: str             # Hebrew label (e.g. "עלות השחר")
    dt_local: datetime     # aware, local tz, rounded per that zman's rule


def compute_zmanim_for_date(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    tallis_offset: int = DEFAULT_TALLIS_TEFILIN_OFFSET,
    havdalah_offset: int = 72,
) -> list[ZmanEntry]:
    """Return all daily zmanim for `base_date` in chronological order.

    `havdalah_offset` is the user's Tzies offset in minutes (sunset + N).
    `tallis_offset` is minutes after Alos for Talis & Tefilin.

    Chatzos HaLaila uses the tzeis-R"T night window (sunset+72 → next
    day's dawn), per existing zman_chatzos_haleila.py.
    """
    cal = ZmanimCalendar(geo_location=geo, date=base_date)
    sunrise = cal.sunrise().astimezone(tz)
    sunset = cal.sunset().astimezone(tz)

    # MGA "day": dawn (sunrise-72) → nightfall (sunset+72)
    dawn = sunrise - timedelta(minutes=_ALOS_OFFSET_MIN)
    nightfall = sunset + timedelta(minutes=_ALOS_OFFSET_MIN)
    mga_hour = (nightfall - dawn) / 12

    # GRA "day": sunrise → sunset
    gra_hour = (sunset - sunrise) / 12

    # Talis & Tefilin = Alos + user offset
    talis = dawn + timedelta(minutes=tallis_offset)

    # Chatzos HaLaila: midpoint of (sunset+72) → next-day-dawn
    cal_next = ZmanimCalendar(geo_location=geo, date=base_date + timedelta(days=1))
    sunrise_next = cal_next.sunrise().astimezone(tz)
    dawn_next = sunrise_next - timedelta(minutes=_ALOS_OFFSET_MIN)
    night_start = sunset + timedelta(minutes=_ALOS_OFFSET_MIN)  # Tzeis R"T
    night_hour = (dawn_next - night_start) / 12
    chatzos_halaila = night_start + night_hour * 6

    # Build the list, then sort strictly by clock time. The MGA/GRA pair
    # orderings are algebraically invariant (e.g. MGA Shma always 36 min
    # before GRA Shma), but Tzies vs Maariv 60 can swap based on the user's
    # havdalah_offset (e.g. havdalah=72 → Tzies after Maariv 60;
    # havdalah=50 → Tzies before). Sorting by dt makes the display stable
    # regardless of config.
    items: list[ZmanEntry] = [
        ZmanEntry("עלות השחר",              _half_up(dawn)),
        ZmanEntry("זמן טלית ותפילין",        _half_up(talis)),
        ZmanEntry("הנץ החמה",               _half_up(sunrise)),
        ZmanEntry("סוף זמן קריאת שמע מג״א",  _floor(dawn + mga_hour * 3)),
        ZmanEntry("סוף זמן קריאת שמע גר״א",  _floor(sunrise + gra_hour * 3)),
        ZmanEntry("סוף זמן תפילה מג״א",      _floor(dawn + mga_hour * 4)),
        ZmanEntry("סוף זמן תפילה גר״א",      _floor(sunrise + gra_hour * 4)),
        ZmanEntry("חצות היום",              _half_up(dawn + mga_hour * 6)),
        ZmanEntry("מנחה גדולה",              _half_up(dawn + mga_hour * 6.5)),
        ZmanEntry("מנחה קטנה",               _half_up(dawn + mga_hour * 9.5)),
        ZmanEntry("פלג המנחה גר״א",          _half_up(sunrise + gra_hour * 10.75)),
        ZmanEntry("פלג המנחה מג״א",          _half_up(dawn + mga_hour * 10.75)),
        ZmanEntry("שקיעת החמה",              _ceil(sunset)),
        ZmanEntry("צאת הכוכבים",             _ceil(sunset + timedelta(minutes=havdalah_offset))),
        ZmanEntry("זמן מעריב 60",            _ceil(sunset + timedelta(minutes=60))),
        ZmanEntry("חצות הלילה",              _half_up(chatzos_halaila)),
    ]
    items.sort(key=lambda e: e.dt_local)
    return items


def compute_chametz_zmanim(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    havdalah_offset: int = 72,
) -> tuple[datetime, datetime]:
    """Return (sof_zman_achilas_chametz, sof_zman_sriefes_chametz) for
    `base_date`, computed MGA-style.

    Matches the existing dedicated sensors in zman_chumetz.py:
      • dawn       = sunrise − havdalah_offset
      • nightfall  = sunset  + havdalah_offset
      • sha'a      = (nightfall − dawn) / 12
      • Achilas    = dawn + 4·sha'a  (floored to the minute — machmir)
      • Sriefes    = dawn + 5·sha'a  (half-up rounded)

    Note: Uses `havdalah_offset` for MGA dawn to match the existing
    chametz sensor's output exactly. (The dedicated Alos / Shma MGA /
    etc. sensors hardcode 72 min instead — so if the user's
    havdalah_offset is not 72, there can be a small discrepancy between
    dawn-based zmanim here and those sensors. That mismatch exists in
    the integration today; we preserve it for consistency.)
    """
    cal = ZmanimCalendar(geo_location=geo, date=base_date)
    sunrise = cal.sunrise().astimezone(tz)
    sunset = cal.sunset().astimezone(tz)
    dawn = sunrise - timedelta(minutes=havdalah_offset)
    nightfall = sunset + timedelta(minutes=havdalah_offset)
    sha_a = (nightfall - dawn) / 12

    achilas_raw = dawn + sha_a * 4
    sriefes_raw = dawn + sha_a * 5

    # Achilas: floor (machmir for a deadline-to-stop)
    achilas = achilas_raw.replace(second=0, microsecond=0)
    # Sriefes: half-up (matches existing sensor)
    sriefes = (
        (sriefes_raw + timedelta(minutes=1))
        if sriefes_raw.second >= 30
        else sriefes_raw
    ).replace(second=0, microsecond=0)
    return achilas, sriefes
