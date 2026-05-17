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

FAST-START EXCEPTION (single source of truth)
---------------------------------------------
The general rounding above treats Alos and Shkia as *positive* zman
boundaries (e.g. Shkia = end of the mincha-gedola window → ceil so the
window stays open lechumra; Alos = start of the tefilin window →
half-up). A fast START is the INVERTED chumra: the fast must begin
BEFORE the astronomical moment, never after, so it FLOORS (truncate
seconds) regardless of the general rule for that zman.

`fast_start_for_date()` is the one place this floored value is
computed. Every consumer that needs a fast-start time — the holiday
sensor's fast timers, the luach generator's fast annotations, and any
future fast sensor — MUST call it instead of re-deriving from raw
sunrise/sunset, so the floor logic never drifts between callers again.

This is a pure helper with no Home Assistant dependency so it can be
reused by:
  • UpcomingShabbosZmanimSensor
  • UpcomingYomTovZmanimSensor
  • HolidaySensor fast-start / fast-countdown logic
  • The luach generator's fast-day annotation rows
  • A "check zmanim for a specific day" service call.
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
    # Unrounded astronomical value (aware, local tz) — the exact argument
    # the rounding function wraps, before display rounding. This is what
    # the "with seconds" mode (sensor *_With_Seconds attributes and the
    # luach `seconds` option) exposes. Halachically identical to
    # dt_local; only the display stringency-direction rounding differs.
    # Defaults to None so existing `ZmanEntry(label, dt)` construction
    # stays valid; compute_zmanim_for_date always populates it.
    dt_raw_local: datetime | None = None


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
        ZmanEntry("עלות השחר",              _half_up(dawn),                              dawn),
        ZmanEntry("זמן טלית ותפילין",        _half_up(talis),                             talis),
        ZmanEntry("הנץ החמה",               _half_up(sunrise),                           sunrise),
        ZmanEntry("סוף זמן קריאת שמע מג״א",  _floor(dawn + mga_hour * 3),                 dawn + mga_hour * 3),
        ZmanEntry("סוף זמן קריאת שמע גר״א",  _floor(sunrise + gra_hour * 3),              sunrise + gra_hour * 3),
        ZmanEntry("סוף זמן תפילה מג״א",      _floor(dawn + mga_hour * 4),                 dawn + mga_hour * 4),
        ZmanEntry("סוף זמן תפילה גר״א",      _floor(sunrise + gra_hour * 4),              sunrise + gra_hour * 4),
        ZmanEntry("חצות היום",              _half_up(dawn + mga_hour * 6),               dawn + mga_hour * 6),
        ZmanEntry("מנחה גדולה",              _half_up(dawn + mga_hour * 6.5),             dawn + mga_hour * 6.5),
        ZmanEntry("מנחה קטנה",               _half_up(dawn + mga_hour * 9.5),             dawn + mga_hour * 9.5),
        ZmanEntry("פלג המנחה גר״א",          _half_up(sunrise + gra_hour * 10.75),        sunrise + gra_hour * 10.75),
        ZmanEntry("פלג המנחה מג״א",          _half_up(dawn + mga_hour * 10.75),           dawn + mga_hour * 10.75),
        ZmanEntry("שקיעת החמה",              _ceil(sunset),                               sunset),
        ZmanEntry("צאת הכוכבים",             _ceil(sunset + timedelta(minutes=havdalah_offset)),  sunset + timedelta(minutes=havdalah_offset)),
        ZmanEntry("זמן מעריב 60",            _ceil(sunset + timedelta(minutes=60)),       sunset + timedelta(minutes=60)),
        ZmanEntry("חצות הלילה",              _half_up(chatzos_halaila),                   chatzos_halaila),
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


def chatzos_halayla_for_night(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
) -> datetime:
    """Compute Chatzos HaLaila (halachic midnight) for the night that
    BEGINS at the sunset of ``base_date``. Matches the YidCal
    ``ChatzosHaLailaSensor`` (MGA midpoint of tzeis-R״T → alos).

    Algebraic identity: with night_start = sunset + 72 min and
    dawn_next = next_sunrise − 72 min, the 6-zmanit-hours midpoint
    simplifies to ``(sunset + sunrise_next) / 2`` (the ±72 offsets
    cancel). This is the same as the astronomical "true solar
    midnight" — the moment the sun is on the opposite meridian.

    Returned datetime is tz-aware and rounded half-up (<30s floor,
    ≥30s ceil) to match the YidCal sensor's display rounding.
    """
    cal_today = ZmanimCalendar(geo_location=geo, date=base_date)
    sunset = cal_today.sunset().astimezone(tz)
    cal_next = ZmanimCalendar(
        geo_location=geo, date=base_date + timedelta(days=1),
    )
    sunrise_next = cal_next.sunrise().astimezone(tz)
    # MGA midpoint = (sunset + sunrise_next) / 2
    midpoint = sunset + (sunrise_next - sunset) / 2
    return _half_up(midpoint)


# ────────────────────────────────────────────────────────────────────────
# Kiddush Levana helpers — molad-derived deadlines
# ────────────────────────────────────────────────────────────────────────
#
# References:
#   • ZMAN user-guide page 10 (טבלא 3 description):
#       — מולד: the mean molad, in Jerusalem clock time.
#       — ז' שלמים: exactly 7 days after the molad (earliest, per the
#         "seven complete days" opinion).
#       — סוף זמן קידוש לבנה (per the Rama): midpoint between consecutive
#         mean molados — i.e., half a mean synodic month after the molad.
#       Both are adjusted to the local clock (with DST if applicable).
#
#   • Mean synodic month = 29 days, 12 hours, 793 chalakim, where
#     1 chelek = 1/1080 hour = 10/3 seconds. Verified empirically against
#     pyluach: consecutive ``molad_announcement()`` results differ by
#     exactly 29d 12h 44m 3.333…s.
#
# These functions are pure helpers and do NOT currently feed into the
# luach PDF (they're staged for future use — e.g. a sensor or an
# optional yearly-sheet-luach KL row). The functions intentionally accept
# Hebrew (year, month) rather than a precomputed Molad object so that
# the DST round-trip is avoided: the announcement is computed cleanly
# in "announcement-clock" units, the duration is added, then local DST
# is applied to the result date — matching the convention in
# ``YidCalHelper.get_actual_molad``.

# Half mean synodic month: 14 days, 18 hours, 22 minutes, 1⅔ seconds.
# Stored with microsecond precision (1.666 666 s) so that the
# accumulated rounding error over many months stays sub-millisecond.
_HALF_SYNODIC_MONTH = timedelta(
    days=14, hours=18, minutes=22, seconds=1, microseconds=666_667,
)


def _molad_announcement_naive(year: int, month: int) -> datetime:
    """Return the molad announcement of (year, month) as a naive
    ``datetime`` — the raw announced clock time (e.g. "Wed 4:34 PM and
    13 chalakim") with no DST applied. Chalakim are converted to
    seconds (1 chelek = 10/3 seconds).

    This is the input for both ``zayin_shleimim_local`` and
    ``sof_zman_kiddush_levana_rama_local``.
    """
    # Imported lazily to keep top-level imports minimal.
    from pyluach.hebrewcal import HebrewDate as _PHebrewDate, Month as _PMonth

    ann = _PMonth(year, month).molad_announcement()
    parts_seconds = ann["parts"] * (10 / 3)

    # Find the Gregorian date of the molad. pyluach weekday is 1=Sun…7=Sat;
    # convert to Python weekday (Mon=0…Sun=6) and walk forward from the
    # 1st of the Hebrew month to the first matching weekday in range.
    weekday_py = (ann["weekday"] + 5) % 7
    first_of_month = _PHebrewDate(year, month, 1).to_pydate()
    delta_days = (weekday_py - first_of_month.weekday()) % 7
    molad_date = first_of_month + timedelta(days=delta_days)
    if molad_date > first_of_month:
        molad_date -= timedelta(days=7)

    return datetime(
        molad_date.year, molad_date.month, molad_date.day,
        ann["hour"], ann["minutes"],
        int(parts_seconds), int((parts_seconds % 1) * 1_000_000),
    )


def _apply_local_dst(naive_dt: datetime, tz: ZoneInfo) -> datetime:
    """Apply the YidCal molad-announcement DST convention: interpret
    the announced time as local clock time, and add the DST offset if
    DST is in effect at that local time. Returns a naive datetime.

    Mirrors ``YidCalHelper.get_actual_molad``'s DST handling so that
    KL deadlines display consistently with the announced molad.
    """
    aware = naive_dt.replace(tzinfo=tz)
    dst = aware.dst() or timedelta(0)
    return naive_dt + dst


def zayin_shleimim_local(
    year: int, month: int, tz: ZoneInfo,
) -> datetime:
    """Compute ז׳ שלמים — exactly 7 days after the molad announcement.

    Per the ZMAN manual: "Z'shleimim is essentially exactly the same
    time as the molad, one week later, except adjusted to the local
    clock at each place, with one hour added in summer where
    customary, in hours-minutes-seconds." (page 10, טבלא 3.)

    Returns a naive ``datetime`` in local clock time (DST applied for
    the Z'shleimim date itself, not the molad date — important near
    spring-forward / fall-back transitions).
    """
    naive = _molad_announcement_naive(year, month) + timedelta(days=7)
    return _apply_local_dst(naive, tz)


def sof_zman_kiddush_levana_rama_local(
    year: int, month: int, tz: ZoneInfo,
) -> datetime:
    """Compute סוף זמן קידוש לבנה per the Rama: half a mean synodic
    month after the molad announcement.

    The Rama opinion (Orach Chayim 426:3): the latest time to recite
    Kiddush Levana is the midpoint between consecutive molados, which
    is the moment the moon is at opposition (astronomical full moon)
    in the mean lunar cycle.

    Per the ZMAN manual: "The sof zman kiddush levana per the Rama
    [is] at the midpoint between molad and molad, also adjusted to
    the local clock." (page 10, טבלא 3.)

    Returns a naive ``datetime`` in local clock time (DST applied for
    the SZKL date itself, not the molad date).

    Note: this implementation does NOT consider the Equation of Time
    (משוואת הזמן). The ZMAN program offers that as an option for
    higher astronomical precision; if needed, a refined version can
    apply EoT at the SZKL date — but the simpler mean-time version
    matches the most common minhag.
    """
    naive = _molad_announcement_naive(year, month) + _HALF_SYNODIC_MONTH
    return _apply_local_dst(naive, tz)

# ────────────────────────────────────────────────────────────────────────
# Fast-start times — the single source of truth for the floored alos /
# shkia used at the START of a taanis. See the FAST-START EXCEPTION note
# in the module docstring.
# ────────────────────────────────────────────────────────────────────────

# The two anchor kinds a fast can start on:
#   • "alos"  → minor fasts (Tzom Gedaliah, Asara b'Teves,
#                Ta'anis Esther, 17 Tammuz) begin at dawn.
#   • "shkia" → Tisha b'Av (and T"B nidche) begins at sunset of the
#                preceding civil day (Erev T"B).
FAST_START_ALOS = "alos"
FAST_START_SHKIA = "shkia"


def fast_start_for_date(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    anchor: str,
) -> datetime:
    """Return the FLOORED fast-start datetime for ``base_date``.

    ``anchor`` is one of:
      • ``FAST_START_ALOS``  — alos hashachar (sunrise − 72 min, MGA
        0°50′). Used for the minor fasts. ``base_date`` is the fast
        day itself.
      • ``FAST_START_SHKIA`` — sunset. Used for Tisha b'Av; the fast
        begins at sunset of Erev T"B, so the caller passes the EREV
        civil date as ``base_date``.

    The result is floored to the minute (seconds truncated) — a fast
    must begin BEFORE the astronomical moment, never after, so this
    deliberately overrides the general half-up (alos) / ceil (shkia)
    rounding used elsewhere in this module. Computed from raw
    sunrise/sunset so no upstream rounding is inherited.

    Returns an aware datetime in ``tz``. Use ``format_simple_time`` or
    the ``H:MM`` pattern for display.
    """
    cal = ZmanimCalendar(geo_location=geo, date=base_date)
    if anchor == FAST_START_ALOS:
        sunrise = cal.sunrise().astimezone(tz)
        raw = sunrise - timedelta(minutes=_ALOS_OFFSET_MIN)
    elif anchor == FAST_START_SHKIA:
        raw = cal.sunset().astimezone(tz)
    else:
        raise ValueError(
            f"anchor must be {FAST_START_ALOS!r} or "
            f"{FAST_START_SHKIA!r}, got {anchor!r}"
        )
    # Floor: truncate seconds (lechumra for a fast start).
    return raw.replace(second=0, microsecond=0)


def format_fast_start_clock(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    anchor: str,
    fmt: str = "12short",
) -> str:
    """Convenience wrapper: ``fast_start_for_date`` formatted as a clock
    string.

    ``fmt``:
      • ``"12short"`` → ``"8:26"`` (12-hour, no AM/PM — the printed-luach
        style used in fast annotations / countdowns)
      • ``"12"``      → ``"8:26 PM"``
      • ``"24"``      → ``"20:26"``
    """
    dt = fast_start_for_date(
        geo=geo, tz=tz, base_date=base_date, anchor=anchor,
    )
    if fmt == "12short":
        return f"{dt.hour % 12 or 12}:{dt.minute:02d}"
    return format_simple_time(dt, fmt)
