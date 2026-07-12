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
  • Shkia, Tzies, Zman Maariv 60, Zman Maariv R"T → ceil (chumra)

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
from datetime import date as date_cls, datetime, time as time_cls, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .grossman_calculator import GrossmanCalculator


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
    """Ceil to the next minute — matches Shkia / Tzies / Maariv 60 style.

    True ceiling: a value already on an exact minute is returned
    unchanged (the printed luachs do the same — SF 5786 prints 6:16
    for a motzei that computes to exactly 6:16:00, not 6:17).
    """
    if dt.second == 0 and dt.microsecond == 0:
        return dt
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

# ── Public rounding aliases ─────────────────────────────────────────────
# THE single source of truth for minute-rounding across ALL YidCal
# sensors. Before these existed, ~24 sensor modules each carried their
# own copy-pasted `_round_half_up` / `_round_ceil` / `_round_floor`,
# and the copies had drifted (e.g. one file's ceil skipped already-exact
# minutes while another always bumped). Sensor modules must import these
# instead of defining their own:
#
#     from .yidcal_lib.zman_compute import round_half_up, round_ceil
#
# Semantics (identical to the private fns used by compute_zmanim_for_date):
#   - round_half_up : <30 s floors, >=30 s bumps to the next minute.
#   - round_floor   : truncate seconds (machmir for deadlines).
#   - round_ceil    : ALWAYS advance to the next whole minute (chumra for
#     end-of-window times like tzeis/havdalah). Note: for the raw
#     astronomical datetimes these are applied to, an exact :00.000000
#     input never occurs in practice, so this is equivalent to the
#     "bump only if seconds present" variant some sensors carried.
round_half_up = _half_up
round_floor = _floor
round_ceil = _ceil


def _grossman_transit(
    cal: ZmanimCalendar,
    geo: GeoLocation,
    base_date: date_cls,
    tz: ZoneInfo,
) -> datetime:
    """True solar transit (chatzos hayom) for ``base_date``, tz-aware.

    Grossmann's "Zmanim" software defines chatzos as the actual solar
    meridian crossing (mean noon + equation of time) and builds netz/
    shkia off it — NOT as the midpoint of sunrise & sunset. The midpoint
    runs ~15-30 s off true noon (sunrise and sunset are each refined to
    their own moment), enough to cross the display minute boundary on
    some dates (e.g. KJ luach week of 5 Sivan 5786: printed 12:53 all
    week vs midpoint 12:53→12:54).

    Uses the patched-in GrossmanCalculator's ``utc_noon``. Falls back to
    the sunrise/sunset midpoint only if some non-Grossman calculator is
    ever injected (keeps this helper total).
    """
    acalc = getattr(cal, "astronomical_calculator", None)
    if isinstance(acalc, GrossmanCalculator):
        h = acalc.utc_noon(base_date, geo)
        return (
            datetime.combine(base_date, time_cls(0), tzinfo=timezone.utc)
            + timedelta(hours=h)
        ).astimezone(tz)
    sr = cal.sunrise().astimezone(tz)
    ss = cal.sunset().astimezone(tz)
    return sr + (ss - sr) / 2


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
    # Shared cached sun events (one astro computation per location+date
    # across the coordinator, lookup sensors and upcoming-zmanim sensors).
    sunrise, sunset = sun_events_for_date(geo=geo, tz=tz, base_date=base_date)

    # MGA "day": dawn (sunrise-72) → nightfall (sunset+72)
    dawn = sunrise - timedelta(minutes=_ALOS_OFFSET_MIN)
    nightfall = sunset + timedelta(minutes=_ALOS_OFFSET_MIN)
    mga_hour = (nightfall - dawn) / 12

    # GRA "day": sunrise → sunset
    gra_hour = (sunset - sunrise) / 12

    # Talis & Tefilin = Alos + user offset
    talis = dawn + timedelta(minutes=tallis_offset)

    # Chatzos HaYom: Grossmann's true solar transit (mean noon + EoT),
    # NOT the sunrise/sunset midpoint — matches the printed luach. (Cached.)
    chatzos_hayom = chatzos_hayom_for_date(geo=geo, tz=tz, base_date=base_date)

    # Chatzos HaLaila: the solar *lower* transit — exactly 12 h after
    # chatzos hayom (same meridian-crossing anchor, opposite culmination;
    # equation-of-time drift over 12 h is sub-second and already absorbed
    # by the calculator's whole-second rounding). Replaces the former
    # (sunset+72 → next-dawn) midpoint, which carried the same ~10 s
    # asymmetry error the hayom midpoint did.
    chatzos_halaila = chatzos_hayom + timedelta(hours=12)

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
        ZmanEntry("חצות היום",              _half_up(chatzos_hayom),                     chatzos_hayom),
        ZmanEntry("מנחה גדולה",              _ceil(dawn + mga_hour * 6.5),                dawn + mga_hour * 6.5),
        ZmanEntry("מנחה קטנה",               _ceil(dawn + mga_hour * 9.5),                dawn + mga_hour * 9.5),
        ZmanEntry("פלג המנחה גר״א",          _half_up(sunrise + gra_hour * 10.75),        sunrise + gra_hour * 10.75),
        ZmanEntry("פלג המנחה מג״א",          _ceil(dawn + mga_hour * 10.75),              dawn + mga_hour * 10.75),
        ZmanEntry("שקיעת החמה",              _half_up(sunset),                            sunset),
        ZmanEntry("צאת הכוכבים",             _ceil(sunset + timedelta(minutes=havdalah_offset)),  sunset + timedelta(minutes=havdalah_offset)),
        ZmanEntry("זמן מעריב 60",            _ceil(sunset + timedelta(minutes=60)),       sunset + timedelta(minutes=60)),
        ZmanEntry("זמן מעריב ר״ת",           _ceil(sunset + timedelta(minutes=72)),       sunset + timedelta(minutes=72)),
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
    sriefes_round: str = "half_up",
) -> tuple[datetime, datetime]:
    """Return (sof_zman_achilas_chametz, sof_zman_sriefes_chametz) for
    `base_date`, computed MGA-style.

    Matches the existing dedicated sensors in zman_chumetz.py:
      • dawn       = sunrise − havdalah_offset
      • nightfall  = sunset  + havdalah_offset
      • sha'a      = (nightfall − dawn) / 12
      • Achilas    = dawn + 4·sha'a  (floored to the minute — machmir)
      • Sriefes    = dawn + 5·sha'a  (rounding selectable, see below)

    ``sriefes_round`` controls the sriefes minute-rounding:
      • ``"half_up"`` (default) — ≥30s rounds up, <30s floors.
        Matches the existing chametz sensor in zman_chumetz.py so the
        sensor's value is preserved exactly. This is the sensor-side
        convention; callers needing the sensor's behaviour pass
        nothing.
      • ``"floor"`` — always floor to the minute. This is the chumrah
        for the chametz-burning/owning deadline (be DONE earlier than
        the nominal halachic minute) and matches the printed
        KY/Brooklyn weekly luach. Used by the luach generator so the
        printed-luach text agrees with the printed reference to the
        minute.

    Note: Uses `havdalah_offset` for MGA dawn to match the existing
    chametz sensor's output exactly. (The dedicated Alos / Shma MGA /
    etc. sensors hardcode 72 min instead — so if the user's
    havdalah_offset is not 72, there can be a small discrepancy between
    dawn-based zmanim here and those sensors. That mismatch exists in
    the integration today; we preserve it for consistency.)
    """
    sunrise, sunset = sun_events_for_date(geo=geo, tz=tz, base_date=base_date)
    dawn = sunrise - timedelta(minutes=havdalah_offset)
    nightfall = sunset + timedelta(minutes=havdalah_offset)
    sha_a = (nightfall - dawn) / 12

    achilas_raw = dawn + sha_a * 4
    sriefes_raw = dawn + sha_a * 5

    # Achilas: floor (machmir for a deadline-to-stop)
    achilas = achilas_raw.replace(second=0, microsecond=0)
    # Sriefes: rounding per ``sriefes_round``
    if sriefes_round == "floor":
        # Chumrah — finish burning/owning chametz BEFORE the nominal
        # minute. Matches the printed KY/Brooklyn weekly luach.
        sriefes = sriefes_raw.replace(second=0, microsecond=0)
    else:
        # half-up — matches the existing zman_chumetz sensor.
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
    BEGINS at the sunset of ``base_date``.

    Defined as the solar *lower* transit — exactly 12 h after that
    day's chatzos hayom (Grossmann's true solar meridian crossing).
    This is the night analogue of the chatzos-hayom fix: the former
    ``(sunset + next_sunrise) / 2`` midpoint carried the same few-second
    asymmetry error vs true solar midnight (sunset and the next
    sunrise are each refined to their own moment, so their midpoint is
    not the exact anti-transit). Anchoring to the true transit keeps
    chatzos hayom / halaila perfectly consistent and matches the
    Grossmann engine the printed luachs are validated against.

    Returned datetime is tz-aware and rounded half-up (<30s floor,
    ≥30s ceil) to match the YidCal sensor's display rounding.
    """
    chatzos_hayom = chatzos_hayom_for_date(geo=geo, tz=tz, base_date=base_date)
    return _half_up(chatzos_hayom + timedelta(hours=12))


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


# ZMAN (Grossman לכל-זמן) convention, verified empirically against the
# printed Williamsburg 5786 Table-3 to sub-minute accuracy on 7 months
# (3 ז׳ שלמים + 4 ס״ז ק״ל, across DST and winter):
#   • The molad announcement is reckoned in JERUSALEM TRUE (mean-solar)
#     time, which ZMAN fixes at GMT+2h21 (per the program's own footer:
#     "מולד ותקופות: ע״פ שעת ירושלים האמתי (GMT+2h21)").
#   • ז׳ שלמים / ס״ז קידוש לבנה are then expressed in the observer's
#     LOCAL CIVIL clock (zone + DST), WITHOUT Equation of Time
#     ("ע״פ שעה מקומי, בלי התחשבות עם משוואת הזמן").
_MOLAD_JERUSALEM_TZ = timezone(timedelta(hours=2, minutes=21))


def _molad_clock_to_local(naive_dt: datetime, tz: ZoneInfo) -> datetime:
    """Convert a molad-announcement-derived naive datetime (whose
    clock digits are Jerusalem GMT+2h21 time) to the observer's local
    civil clock (``tz``, DST-aware), and return it as a naive local
    datetime. No Equation of Time. (This matches the numeric ZMAN
    *Table-3* column — NOT the printed weekly לכל-זמן booklet.)
    """
    aware_jer = naive_dt.replace(tzinfo=_MOLAD_JERUSALEM_TZ)
    return aware_jer.astimezone(tz).replace(tzinfo=None)


def _molad_clock_local_dst(naive_dt: datetime, tz: ZoneInfo) -> datetime:
    """The printed לכל-זמן weekly-BOOKLET convention ("method C"),
    reverse-engineered from the booklet and verified EXACT on 5/5
    ז׳-שלמים samples across the year (Tishrei/Kislev/Nisan/Sivan/Adar,
    both DST and winter):

      • the molad ANNOUNCEMENT digits are read directly as the
        observer's local CLOCK time (no Jerusalem conversion), then
      • the daylight-saving hour in effect ON THE RESULT DATE is
        added (so summer values land one hour later).

    Returns a naive local datetime.
    """
    dst = naive_dt.replace(tzinfo=tz).dst() or timedelta(0)
    return naive_dt + dst


def gimmel_shleimim_local(
    year: int, month: int, tz: ZoneInfo,
) -> datetime:
    """Compute ג׳ שלמים — exactly 3 days (72 hours) after the molad
    announcement. The 72-hour arithmetic is the universal convention
    (e.g. KosherJava's getTchilasZmanKidushLevana3Days: "adding 3 days
    ... to the molad time" — identical arithmetic to the 7-day zman,
    just 3 days instead of 7).

    Clock convention: the printed לכל-זמן booklet carries no ג׳-שלמים
    column, so there is no independent printed reference to verify
    against. This deliberately follows the in-house ז׳-שלמים family
    convention (``_molad_clock_local_dst`` — molad digits read as
    local clock + DST on the ג׳-שלמים result date) so ג׳ and ז׳ are
    internally consistent: exactly 4 × 24h apart on the announcement
    clock (they can differ by the DST hour only when the two result
    dates straddle a clock change, which is correct method-C
    behavior).

    Returns a naive ``datetime`` in local clock time.
    """
    naive = _molad_announcement_naive(year, month) + timedelta(days=3)
    return _molad_clock_local_dst(naive, tz)


def zayin_shleimim_local(
    year: int, month: int, tz: ZoneInfo,
) -> datetime:
    """Compute ז׳ שלמים — exactly 7 days after the molad announcement,
    expressed per the printed לכל-זמן weekly-booklet convention
    (``_molad_clock_local_dst`` — molad digits as local clock + DST
    on the ז׳-שלמים date). Verified EXACT against the printed KY
    booklet on 5/5 sampled months (Tishrei/Kislev/Nisan/Sivan/Adar).

    Returns a naive ``datetime`` in local clock time.
    """
    naive = _molad_announcement_naive(year, month) + timedelta(days=7)
    return _molad_clock_local_dst(naive, tz)


def sof_zman_kiddush_levana_rama_local(
    year: int, month: int, tz: ZoneInfo,
) -> datetime:
    """Compute סוף זמן קידוש לבנה per the Rama: half a mean synodic
    month after the molad (Orach Chayim 426:3 — the midpoint between
    consecutive molados).

    DESIGN DECISION (settled): this uses the well-sourced MEAN method —
    molad reckoned at Jerusalem GMT+2h21, + half the mean synodic
    month — which reproduces the authoritative ZMAN לכל-זמן *Table-3*
    EXACTLY for all 12 months of 5786 (night/day classification and
    times verified). The printed weekly KY booklet additionally adds
    the customary שעון-קיץ (summer) hour and shows two months
    (Kislev/Elul) that disagree with the program's OWN Table-3 —
    treated as booklet errata. We deliberately track the mean Table-3
    (Yoel's decision), not the hand-transcribed booklet, for this
    halachic deadline. (ז׳ שלמים is separate: it has no astronomical
    "true" form, so it uses the booklet's method-C base — see
    ``zayin_shleimim_local`` — and matches the booklet 5/5.)

    Returns a naive ``datetime`` in local clock time. The luach layer
    additionally applies the day/night "show at night / כל הלילה"
    display rule.
    """
    naive = _molad_announcement_naive(year, month) + _HALF_SYNODIC_MONTH
    return _molad_clock_to_local(naive, tz)

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
    if anchor == FAST_START_ALOS:
        raw = dawn_for_date(geo=geo, tz=tz, base_date=base_date)
    elif anchor == FAST_START_SHKIA:
        raw = sunset_for_date(geo=geo, tz=tz, base_date=base_date)
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


# ------------------------------------------------------------------------
# Shared, CACHED sun-event primitives -- the single source of truth for
# raw sunrise / sunset / dawn / nightfall / chatzos on ANY civil date.
# ------------------------------------------------------------------------
#
# WHY: outside the coordinator-driven zman_* sensors, ~30 sensor modules
# each rebuilt ``ZmanimCalendar(geo_location=..., date=d)`` inline and
# re-derived sunset/sunrise -- the same astronomical computation repeated
# once per sensor per date per update. These helpers compute each
# (location, date) pair ONCE and memoize it, so thirty sensors asking
# for this Friday's sunset cost one Grossman computation total.
#
# CONTRACT (read before changing):
#   * Values returned are RAW (unrounded, microsecond-precision), aware
#     in the caller's tz. Each sensor keeps applying its own rounding
#     rule via round_half_up / round_ceil / round_floor, so migrating a
#     sensor onto these helpers can never change what it displays.
#   * The cache key is (lat, lon, elevation, tzname, civil date) -- the
#     full set of inputs that determine the result. GeoLocation is
#     reconstructed from the key inside the cached fn (GeoLocation
#     itself is unhashable: the zmanim lib converts its tz to a
#     dateutil tzfile). Every YidCal geo is built by
#     zman_sensors._create_geo / zmanim_coordinator._resolve_geo_and_tz
#     with name="YidCal", elevation=0, so reconstruction is exact.
#   * Conversion to the caller's tz happens OUTSIDE the cache (values
#     are cached as UTC instants), so any tzinfo object works -- the
#     cache key only uses the tz NAME for GeoLocation construction,
#     where it does not affect the UTC instant.
#   * Like the rest of this module, these assume the package-level
#     GrossmanCalculator monkey-patch is active (it is applied by
#     importing yidcal_lib, which happens before anything here runs).

# ~5.5 years of distinct (single-location) dates; entries are tiny.
_SUN_CACHE_SIZE = 2048


def _geo_cache_key(geo: GeoLocation) -> tuple[float, float, float]:
    return (
        float(geo.latitude),
        float(geo.longitude),
        float(getattr(geo, "elevation", 0.0) or 0.0),
    )


@lru_cache(maxsize=_SUN_CACHE_SIZE)
def _sun_events_utc(
    lat: float, lon: float, elev: float, tzname: str, ordinal: int,
) -> tuple[datetime, datetime]:
    """(sunrise, sunset) for the civil date ``ordinal`` as UTC instants.

    Raises (uncached) on polar no-rise/no-set dates -- identical to the
    pre-refactor inline ``cal.sunrise().astimezone(tz)`` behavior.
    """
    geo = GeoLocation(
        name="YidCal", latitude=lat, longitude=lon,
        time_zone=tzname, elevation=elev,
    )
    cal = ZmanimCalendar(geo_location=geo, date=date_cls.fromordinal(ordinal))
    return (
        cal.sunrise().astimezone(timezone.utc),
        cal.sunset().astimezone(timezone.utc),
    )


@lru_cache(maxsize=_SUN_CACHE_SIZE)
def _transit_utc(
    lat: float, lon: float, elev: float, tzname: str, ordinal: int,
) -> datetime:
    """Grossman true solar transit (chatzos hayom) as a UTC instant."""
    geo = GeoLocation(
        name="YidCal", latitude=lat, longitude=lon,
        time_zone=tzname, elevation=elev,
    )
    d = date_cls.fromordinal(ordinal)
    cal = ZmanimCalendar(geo_location=geo, date=d)
    return _grossman_transit(cal, geo, d, ZoneInfo(tzname)).astimezone(
        timezone.utc
    )


def sun_events_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> tuple[datetime, datetime]:
    """RAW (sunrise, sunset) for ``base_date``, aware in ``tz``. Cached."""
    lat, lon, elev = _geo_cache_key(geo)
    tzname = getattr(tz, "key", None) or str(tz)
    sr_utc, ss_utc = _sun_events_utc(
        lat, lon, elev, tzname, base_date.toordinal()
    )
    return sr_utc.astimezone(tz), ss_utc.astimezone(tz)


def sunrise_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW sunrise for ``base_date``, aware in ``tz``. Cached."""
    return sun_events_for_date(geo=geo, tz=tz, base_date=base_date)[0]


def sunset_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW sunset for ``base_date``, aware in ``tz``. Cached."""
    return sun_events_for_date(geo=geo, tz=tz, base_date=base_date)[1]


def dawn_for_date(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    offset_min: int = _ALOS_OFFSET_MIN,
) -> datetime:
    """RAW Alos HaShachar (sunrise - ``offset_min``) for ``base_date``."""
    return sunrise_for_date(
        geo=geo, tz=tz, base_date=base_date
    ) - timedelta(minutes=offset_min)


def nightfall_for_date(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    base_date: date_cls,
    offset_min: int = _ALOS_OFFSET_MIN,
) -> datetime:
    """RAW nightfall (sunset + ``offset_min``) for ``base_date``."""
    return sunset_for_date(
        geo=geo, tz=tz, base_date=base_date
    ) + timedelta(minutes=offset_min)


def chatzos_hayom_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW chatzos hayom (Grossman true solar transit), aware in ``tz``.

    Same value compute_zmanim_for_date uses for the chatzos-hayom row
    before its half-up display rounding. Cached.
    """
    lat, lon, elev = _geo_cache_key(geo)
    tzname = getattr(tz, "key", None) or str(tz)
    return _transit_utc(
        lat, lon, elev, tzname, base_date.toordinal()
    ).astimezone(tz)


def mincha_ketana_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW MGA mincha ketana (dawn + 9.5 MGA sha'os) for ``base_date``.

    Same value compute_zmanim_for_date uses for "מנחה קטנה" before its
    ceil display rounding — i.e. what sensor.yidcal_mincha_ketana shows
    (after round_ceil). NOT the GRA (sunrise→sunset) variant that
    python-zmanim's ``ZmanimCalendar.mincha_ketana()`` returns, which
    runs ~40 min earlier. Cached via the shared sun events.
    """
    dawn = dawn_for_date(geo=geo, tz=tz, base_date=base_date)
    nightfall = nightfall_for_date(geo=geo, tz=tz, base_date=base_date)
    return dawn + (nightfall - dawn) / 12 * 9.5


def plag_hamincha_gra_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW plag hamincha GRA (sunrise + 10.75 GRA sha'os) for ``base_date``.

    Same value compute_zmanim_for_date uses for "פלג המנחה גר״א" before
    its half-up display rounding; numerically identical to python-zmanim's
    ``ZmanimCalendar.plag_hamincha()`` (verified to the microsecond).
    Cached via the shared sun events.
    """
    sunrise, sunset = sun_events_for_date(geo=geo, tz=tz, base_date=base_date)
    return sunrise + (sunset - sunrise) / 12 * 10.75


def plag_hamincha_mga_for_date(
    *, geo: GeoLocation, tz: ZoneInfo, base_date: date_cls,
) -> datetime:
    """RAW plag hamincha MGA (dawn + 10.75 MGA sha'os) for ``base_date``.

    Same value compute_zmanim_for_date uses for "פלג המנחה מג״א" before
    its ceil display rounding. python-zmanim's plain ZmanimCalendar has
    NO MGA plag method at all — this helper is the only source. Cached.
    """
    dawn = dawn_for_date(geo=geo, tz=tz, base_date=base_date)
    nightfall = nightfall_for_date(geo=geo, tz=tz, base_date=base_date)
    return dawn + (nightfall - dawn) / 12 * 10.75


def compute_holiday_windows(
    *,
    geo: GeoLocation,
    tz: ZoneInfo,
    festival_date: date_cls,
    actual_date: date_cls,
    candle_offset: int,
    havdalah_offset: int,
) -> dict[str, tuple[datetime, datetime]]:
    """The nine named holiday windows for ``festival_date`` — THE single
    source for "when does a holiday flag turn on/off". Extracted verbatim
    from holiday_sensor's window block so the sensor and any range/JSON
    consumer (paired with halacha_events.HOLIDAY_WINDOW_TYPE) share one
    implementation.

    ``festival_date`` is the havdalah-rolled Hebrew day being labeled;
    ``actual_date`` is the civil today (only candle_candle's END uses it:
    next-civil-day candles). Roundings match the sensor exactly:
    candles half-up, havdalah/motzei ceil, alos FLOORED (the v0.7.8
    fast-start chumra — alos here anchors minor-fast windows).
    """
    prev_sunset = sunset_for_date(geo=geo, tz=tz, base_date=festival_date - timedelta(days=1))
    fest_sunset = sunset_for_date(geo=geo, tz=tz, base_date=festival_date)
    next_sunset = sunset_for_date(geo=geo, tz=tz, base_date=festival_date + timedelta(days=1))
    tomorrow_sunset = sunset_for_date(geo=geo, tz=tz, base_date=actual_date + timedelta(days=1))
    dawn = _floor(dawn_for_date(geo=geo, tz=tz, base_date=festival_date))

    candles_erev = _half_up(prev_sunset - timedelta(minutes=candle_offset))
    havdalah_day = _ceil(fest_sunset + timedelta(minutes=havdalah_offset))
    motzei_prev = _ceil(prev_sunset + timedelta(minutes=havdalah_offset))

    # havdalah_havdalah start: when the festival day itself is Shabbos the
    # window opens at Friday candles, not motzei of the previous day.
    hh_start = candles_erev if festival_date.weekday() == 5 else motzei_prev

    return {
        "candle_havdalah":   (candles_erev, havdalah_day),
        "candle_both":       (candles_erev, _ceil(next_sunset + timedelta(minutes=havdalah_offset))),
        "alos_havdalah":     (dawn, havdalah_day),
        "alos_candle":       (dawn, _half_up(fest_sunset - timedelta(minutes=candle_offset))),
        "candle_alos":       (candles_erev, dawn),
        "havdalah_alos":     (motzei_prev, dawn),
        "havdalah_havdalah": (hh_start, havdalah_day),
        "havdalah_candle":   (motzei_prev, _half_up(fest_sunset - timedelta(minutes=candle_offset))),
        "candle_candle":     (candles_erev, _half_up(tomorrow_sunset - timedelta(minutes=candle_offset))),
    }
