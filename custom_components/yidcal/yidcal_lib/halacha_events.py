"""
custom_components/yidcal/yidcal_lib/halacha_events.py

Central module for Jewish-calendar event and date logic.

This module owns "what halachic event applies on date X?" — the pure
calendar-structural logic that is shared by the luach generator, by
date-driven sensors (Tekufah, Behab, Mevorchim, etc.), and (in a
future phase) by the YidCal calendar entity.

Design rules for this module:
  • Pure functions. No Home Assistant imports.
  • No clock-time computation (those live in ``zman_compute.py``).
  • No file/IO/PDF side-effects (those live in luach_yearly_multi_page_pdf.py
    and luach_yearly_sheet_pdf.py).
  • Inputs are civil ``datetime.date`` (or Hebrew year + month/day);
    outputs are dataclasses, primitives, or sets thereof.

What's in here:
  • Hebrew formatting utilities (letter gematria, month names, date
    strings, weekday labels).
  • Tekufos: anchor-based computation of Tekufas Tishrei / Teves /
    Nisan / Tammuz for any Hebrew year, with optional Standard-Time
    display (printed-luach convention ignores DST).
  • Behab: Mon–Thu–Mon fast cycles in Cheshvan and Iyar.
  • Mevorchim: which Shabbos benches which Hebrew month, and which
    Hebrew dates are Rosh Chodesh.
  • Pirkei Avos: chapter for any Shabbos in the Avos season.
  • Sefiras HaOmer: day number for any Hebrew date.
  • Yom Tov classification: major-YT name, Erev-YT detection,
    in-block-day descriptors, no-melacha block boundaries.
  • Parsha resolution with the YidCal display overrides (אחרי מות → אחרי,
    optional מצורע → טהרה).
  • Special-Shabbos labels (delegated to ``specials`` — re-exported here
    so callers have a single import surface).
  • Fast date resolution including נדחה (push from Shabbos to Sunday).
  • Civil-date lookups for Pesach Sheni / Lag BaOmer / 15 Av.
  • Molad short-form Hebrew formatter.
  • Year-cycle helpers: leap year (19-year machzor position, next
    leap year) and shmita cycle (position 1..7, years-until, next
    shmita year).

What's NOT in here (and why):
  • Daily clock-time zmanim → ``zman_compute.py``. Different shape
    (date + location → wall-clock times); different consumers (every
    daily sensor).
  • Lighting events (Erev Shabbos candle-lighting datetime, motzei
    datetime) → currently mirrored from ``zman_sensors.py``. Those
    are clock-time computations, so they belong with ``zman_compute``
    structurally; they're re-exported via ``zman_compute`` shims so
    this module doesn't pull in the zmanim library.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from pyluach import dates as pl_dates, parshios
from pyluach.hebrewcal import HebrewDate as PHebrewDate, Year as PYear


# ────────────────────────────────────────────────────────────────────────
# Hebrew formatting utilities
# ────────────────────────────────────────────────────────────────────────

def int_to_hebrew_letters(n: int) -> str:
    """Standard gematria for 1–999 with proper geresh/gershayim and the
    15→ט״ו, 16→ט״ז special-cases.

    Examples:
      1  → 'א׳'
      15 → 'ט״ו'
      16 → 'ט״ז'
      20 → 'כ׳'
      27 → 'כ״ז'
    """
    if n <= 0 or n >= 1000:
        return str(n)
    mapping = [
        (400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"),
        (90, "צ"), (80, "פ"), (70, "ע"), (60, "ס"), (50, "נ"),
        (40, "מ"), (30, "ל"), (20, "כ"), (10, "י"),
        (9, "ט"), (8, "ח"), (7, "ז"), (6, "ו"), (5, "ה"),
        (4, "ד"), (3, "ג"), (2, "ב"), (1, "א"),
    ]
    if n % 100 == 15:
        prefix = n - 15
        letters = ""
        for value, letter in mapping:
            while prefix >= value:
                letters += letter
                prefix -= value
        letters += "טו"
    elif n % 100 == 16:
        prefix = n - 16
        letters = ""
        for value, letter in mapping:
            while prefix >= value:
                letters += letter
                prefix -= value
        letters += "טז"
    else:
        letters = ""
        temp = n
        for value, letter in mapping:
            while temp >= value:
                letters += letter
                temp -= value
    if len(letters) > 1:
        return f"{letters[:-1]}\u05F4{letters[-1]}"  # gershayim
    return f"{letters}\u05F3"  # geresh


_HEB_MONTHS = {
    1: "ניסן", 2: "אייר", 3: "סיון", 4: "תמוז", 5: "אב", 6: "אלול",
    7: "תשרי", 8: "חשון", 9: "כסלו", 10: "טבת", 11: "שבט",
}


def hebrew_month_name(year: int, month: int) -> str:
    """Hebrew month name, with Adar I/II disambiguation in leap years."""
    if month in (12, 13):
        try:
            is_leap = PYear(year).leap
        except Exception:
            is_leap = False
        if month == 12:
            return "אדר א׳" if is_leap else "אדר"
        return "אדר ב׳"  # month 13
    return _HEB_MONTHS.get(month, str(month))


def hebrew_month_name_for_mevorchim(year: int, month: int) -> str:
    """Hebrew month name in the form used for Mevorchim announcements.

    Most months are identical to ``hebrew_month_name``; the only
    difference is **Av**, which is announced as ``"מנחם אב"`` in the
    Mevorchim line (per Monroe/KJ convention). The Hebrew-date column
    elsewhere on the same row still shows just ``"אב"``, so this is a
    Mevorchim-specific spelling.
    """
    if month == 5:
        return "מנחם אב"
    return hebrew_month_name(year, month)


def hebrew_date_str(d: date_cls, *, rc_emphasis: bool = True) -> str:
    """Format a Gregorian date as 'ב׳ ניסן' (day-letter + Hebrew month).

    When ``rc_emphasis=True`` (default — matches the Monroe/KJ printed
    luach convention), Rosh Chodesh days are rendered in a special form:
      • Day 30 of a 30-day month — which is also the *first* day of a
        two-day Rosh Chodesh — renders as 'א׳ דר״ח [next month]'.
      • Day 1 of a month, when the *previous* month had 30 days —
        which means today is the *second* day of a two-day RC —
        renders as 'ב׳ דר״ח [this month]'.
      • Day 1 of a month, when the previous month had 29 days — the
        single RC day — renders as 'ר״ח [this month]'.
      • 1 Tishrei is RH (the Hebrew new year), handled elsewhere as a
        YT label — we don't apply RC emphasis there.

    When ``rc_emphasis=False`` (matches the South-Fallsburg printed
    convention), all dates are rendered in the plain Hebrew-letter
    form regardless of RC status — e.g. ``ל׳ ניסן`` instead of
    ``א׳ דר״ח אייר`` for 30 Nissan. SF luachs convey the RC info
    via the row's special-Shabbos tag (``שבת ר״ח``) rather than
    encoding it in the date column.
    """
    ph = PHebrewDate.from_pydate(d)
    if rc_emphasis:
        if ph.day == 30:
            ph_next = PHebrewDate.from_pydate(d + timedelta(days=1))
            return f"א׳ דר״ח {hebrew_month_name(ph_next.year, ph_next.month)}"
        if ph.day == 1 and ph.month != 7:
            # Check previous Hebrew month length via the day-of-month of
            # the prior civil day (30 ⇒ 2-day RC, else single-day RC).
            ph_prev = PHebrewDate.from_pydate(d - timedelta(days=1))
            if ph_prev.day == 30:
                return f"ב׳ דר״ח {hebrew_month_name(ph.year, ph.month)}"
            return f"ר״ח {hebrew_month_name(ph.year, ph.month)}"
    return f"{int_to_hebrew_letters(ph.day)} {hebrew_month_name(ph.year, ph.month)}"


# Hebrew weekday labels for non-Shabbos days (Mon=0 .. Sun=6).
# Shabbos (weekday 5) is intentionally absent — callers should use 'שבת'
# or 'שב״ק' depending on context.
HE_WEEKDAY = {
    0: "יום ב׳",  # Monday
    1: "יום ג׳",  # Tuesday
    2: "יום ד׳",  # Wednesday
    3: "יום ה׳",  # Thursday
    4: "יום ו׳",  # Friday
    6: "יום א׳",  # Sunday
}


# ────────────────────────────────────────────────────────────────────────
# Hebrew year helpers
# ────────────────────────────────────────────────────────────────────────

def hebrew_year_from_letters(s: str) -> int | None:
    """Inverse of :func:`hebrew_year_letters` — 'תשפ״ו' → 5786.

    Plain gematria. Tolerates geresh/gershayim in either the Hebrew
    (׳ ״) or ASCII (' ") forms, an optional leading ה׳ for the 5000s,
    and surrounding whitespace. Returns None if it isn't Hebrew at all,
    so callers can fall through to plain-int parsing.

        'תשפ״ו'   → 5786
        'ה׳תשפ״ו' → 5786
        'תשפו'    → 5786   (marks are optional)
    """
    if not s:
        return None
    t = str(s).strip()
    for ch in ("\u05f4", "\u05f3", '"', "'", "\u2019", "\u201d", " "):
        t = t.replace(ch, "")
    if not t or not all("\u05d0" <= c <= "\u05ea" for c in t):
        return None
    vals = {
        "א": 1, "ב": 2, "ג": 3, "ד": 4, "ה": 5, "ו": 6, "ז": 7,
        "ח": 8, "ט": 9, "י": 10, "כ": 20, "ך": 20, "ל": 30,
        "מ": 40, "ם": 40, "נ": 50, "ן": 50, "ס": 60, "ע": 70,
        "פ": 80, "ף": 80, "צ": 90, "ץ": 90, "ק": 100, "ר": 200,
        "ש": 300, "ת": 400,
    }
    # A leading ה׳ spells out the 5000s ('ה׳תשפ״ו'); drop it so the rest
    # is the plain 3-digit remainder, exactly as hebrew_year_letters emits.
    if len(t) > 3 and t[0] == "ה":
        t = t[1:]
    try:
        n = sum(vals[c] for c in t)
    except KeyError:
        return None
    if n <= 0:
        return None
    return n + 5000 if n < 1000 else n


def hebrew_year_letters(hy: int) -> str:
    """Format a 4-digit Hebrew year (e.g. 5787) as letters 'תשפ״ז'.
    Strips the implicit 5000s prefix that's conventionally omitted.
    """
    if hy < 5000 or hy >= 6000:
        return int_to_hebrew_letters(hy)
    return int_to_hebrew_letters(hy - 5000)


# Year-length code letters (the middle letter of the 3-letter kvius).
# Standard year: 353=ח (chesera), 354=כ (kesidra), 355=ש (sheleima).
# Leap year:     383=ח,           384=כ,           385=ש.
_YEAR_LENGTH_LETTER = {
    353: "ח", 354: "כ", 355: "ש",
    383: "ח", 384: "כ", 385: "ש",
}

# Hebrew letters for weekdays 1..7 (pyluach convention: 1=Sunday … 7=Saturday).
# Note: in the kvius the weekday letter for ONE-digit values (1-9) uses just
# the letter without geresh, because it's already part of a 3-letter cluster.
_WEEKDAY_LETTER = {
    1: "א", 2: "ב", 3: "ג", 4: "ד", 5: "ה", 6: "ו", 7: "ז",
}


def kvius_components(hebrew_year: int) -> tuple[str, str, str, bool, int]:
    """Return the components of the year's kvius (calendar pattern).

    Returns ``(rh_letter, length_letter, pesach_letter, is_leap, shmita_year)``:

      * ``rh_letter`` — Hebrew letter for the weekday of Rosh Hashanah day 1
        (e.g. ``"ג"`` for Tuesday). RH never falls on Sun/Wed/Fri, so the
        only valid values are ב, ג, ה, ז.
      * ``length_letter`` — ח/כ/ש per ``_YEAR_LENGTH_LETTER``.
      * ``pesach_letter`` — Hebrew letter for the weekday of 15 Nissan
        (first day of Pesach). Valid values are א, ג, ה, ז.
      * ``is_leap`` — True if this is a 13-month leap year (מעוברת).
      * ``shmita_year`` — Year within the 7-year shmita cycle (1..7),
        where 7 is the שמיטה year itself. Calibrated against the
        well-known shmita year 5782 = year 7.
    """
    y = PYear(hebrew_year)
    days = len(y)
    rh = PHebrewDate(hebrew_year, 7, 1)
    pesach = PHebrewDate(hebrew_year, 1, 15)
    rh_letter = _WEEKDAY_LETTER.get(rh.weekday(), "")
    pesach_letter = _WEEKDAY_LETTER.get(pesach.weekday(), "")
    length_letter = _YEAR_LENGTH_LETTER.get(days, "")
    # Shmita position via the single shared helper (calibration:
    # 5782 mod 7 == 0 was a shmita year = year 7 of the cycle).
    shmita_year = shmita_cycle_year(hebrew_year)
    return rh_letter, length_letter, pesach_letter, y.leap, shmita_year


def format_kvius_line(hebrew_year: int) -> str:
    """Return the Hebrew 'kvius hashanah' descriptor for the given year.

    Format mirrors the South-Fallsburg-style printed luach:
        קביעת השנה: גכ״ה - פשוטה - ד׳ לשמיטה
    breakdown:
      * ``גכ״ה`` — three-letter kvius: RH weekday + year-length code +
        first-day-of-Pesach weekday, with a gershayim ``״`` before the
        last letter.
      * ``פשוטה`` / ``מעוברת`` — regular (12-month) or leap (13-month).
      * ``ד׳ לשמיטה`` — position within the 7-year shmita cycle
        (1..6 = ordinary year of cycle, 7 = שמיטה itself).
    """
    rh_l, len_l, pes_l, is_leap, shmita = kvius_components(hebrew_year)
    # Three-letter kvius cluster with gershayim before the last letter
    kvius_3 = f"{rh_l}{len_l}\u05F4{pes_l}"
    leap_word = "מעוברת" if is_leap else "פשוטה"
    if shmita == 7:
        # The actual shmita year itself — the printed SF sheet writes
        # 'שנת השמיטה' (5782: 'גכ״ז - מעוברת - שנת השמיטה').
        shmita_part = "שנת השמיטה"
    else:
        shmita_part = f"{int_to_hebrew_letters(shmita)} לשמיטה"
    return f"קביעת השנה: {kvius_3} - {leap_word} - {shmita_part}"


# ────────────────────────────────────────────────────────────────────────
# No-melacha block detection and Yom Tov classification
# ────────────────────────────────────────────────────────────────────────
# These functions duplicate the logic in zman_sensors.py intentionally:
# we want halacha_events to be self-contained and the canonical home for
# this logic going forward, while leaving existing sensors untouched
# until they're migrated in a later phase.

def _is_no_mel_internal(d: date_cls, *, diaspora: bool) -> bool:
    """Internal helper: True if ``d`` is Shabbos or YT.

    Imports hdate lazily so this module can be imported in contexts
    that don't need YT classification (e.g. raw Tekufah lookups).
    """
    from hdate import HDateInfo
    return d.weekday() == 5 or HDateInfo(d, diaspora=diaspora).is_yom_tov


def is_no_melacha(d: date_cls, *, diaspora: bool) -> bool:
    """Public: True if ``d`` is a day on which melacha is forbidden
    (Shabbos or Yom Tov in the given diaspora/Israel convention).
    """
    return _is_no_mel_internal(d, diaspora=diaspora)


def no_melacha_block(
    d: date_cls, *, diaspora: bool,
) -> tuple[date_cls, date_cls] | None:
    """If ``d`` is in a no-melacha block (Shabbos + consecutive YT),
    return ``(start, end)``. Returns None if ``d`` is a weekday.

    Handles the Shemini Atzeres → Simchas Torah bridge in diaspora
    (where the 8th day of Sukkos extends the block by one civil day
    beyond what HDateInfo flags as YT).
    """
    if not _is_no_mel_internal(d, diaspora=diaspora):
        return None
    start = d
    while _is_no_mel_internal(start - timedelta(days=1), diaspora=diaspora):
        start -= timedelta(days=1)
    end = d
    while _is_no_mel_internal(end + timedelta(days=1), diaspora=diaspora):
        end += timedelta(days=1)
    if diaspora:
        name_end = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
        name_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(
            hebrew=True, prefix_day=False
        )
        if name_end == "שמיני עצרת" and name_next == "שמחת תורה":
            end = end + timedelta(days=1)
    return (start, end)


def major_yt_name(ph: PHebrewDate, *, diaspora: bool) -> str | None:
    """Return the canonical Hebrew name of the major Yom Tov this
    Hebrew date belongs to, or None.

    Returned values: 'פסח', 'שביעי של פסח', 'אחרון של פסח' (diaspora
    only), 'שבועות', 'ראש השנה', 'יום כיפור', 'סוכות', 'שמיני עצרת',
    'שמחת תורה' (diaspora only).
    """
    m, d = ph.month, ph.day
    if m == 1:  # Nisan
        if 15 <= d <= (22 if diaspora else 21):
            if d == 21:
                return "שביעי של פסח"
            if d == 22 and diaspora:
                return "אחרון של פסח"
            return "פסח"
        return None
    if m == 3:  # Sivan
        if d == 6 or (d == 7 and diaspora):
            return "שבועות"
        return None
    if m == 7:  # Tishrei
        if d in (1, 2):
            return "ראש השנה"
        if d == 10:
            return "יום כיפור"
        if 15 <= d <= 21:
            return "סוכות"
        if d == 22:
            return "שמיני עצרת"
        if d == 23 and diaspora:
            return "שמחת תורה"
        return None
    return None


def intra_block_day_label(ph: PHebrewDate, *, diaspora: bool) -> str | None:
    """For a day INSIDE a YT block, return its descriptor like 'ב׳ דפסח'
    or 'הושענא רבה'. Used by callers building Erev-row titles for
    in-block candle events.
    """
    m, d = ph.month, ph.day
    if m == 1 and 15 <= d <= (22 if diaspora else 21):
        if d == 15:
            return "א׳ דפסח"
        if d == 16:
            return "ב׳ דפסח"
        if d == 21:
            return "שביעי של פסח"
        if d == 22 and diaspora:
            return "אחרון של פסח"
        # Chol HaMoed Pesach. First CH day = א׳ דחוה״מ:
        #   diaspora YT = 15,16 → first CH = 17 → 17-16 = 1
        #   E"Y     YT = 15    → first CH = 16 → 16-15 = 1
        # (Verified vs the printed Brooklyn/KY luach.)
        chm_num = d - (16 if diaspora else 15)
        return f"{int_to_hebrew_letters(chm_num)} דחוה״מ פסח"
    if m == 3 and (d == 6 or (d == 7 and diaspora)):
        return "א׳ דשבועות" if d == 6 else "ב׳ דשבועות"
    if m == 7:
        if d == 1:
            return "א׳ דראש השנה"
        if d == 2:
            return "ב׳ דראש השנה"
        if d == 10:
            return "יום כיפור"
        if 15 <= d <= 21:
            if d == 15:
                return "א׳ דסוכות"
            if d == 16 and diaspora:
                return "ב׳ דסוכות"
            if d == 21:
                return "הושענא רבה"
            # Chol HaMoed Sukkos. First CH day = א׳ דחוה״מ:
            #   diaspora YT = 15,16 → first CH = 17 → 17-16 = 1
            #   E"Y     YT = 15    → first CH = 16 → 16-15 = 1
            # (Verified vs the printed Brooklyn/KY Erev-Sukkos card.)
            chm_offset = d - (16 if diaspora else 15)
            return f"{int_to_hebrew_letters(chm_offset)} דחוה״מ סוכות"
        if d == 22:
            return "שמיני עצרת"
        if d == 23 and diaspora:
            return "שמחת תורה"
    return None


# ────────────────────────────────────────────────────────────────────────
# Parsha resolution
# ────────────────────────────────────────────────────────────────────────

def parsha_name(
    saturday: date_cls,
    *,
    diaspora: bool,
    metzora_display: str = "metzora",
) -> str:
    """Hebrew parsha name for that Shabbos, with the same overrides
    that the rest of YidCal uses:
      • 'אחרי מות' → 'אחרי' (unconditional shortening)
      • 'מצורע' → 'טהרה' (only when metzora_display=='tahara')

    Returns the empty string when ``saturday`` is a YT-only Shabbos
    (chag) and parshios.getparsha returns no entry.
    """
    greg = pl_dates.GregorianDate(saturday.year, saturday.month, saturday.day)
    idx = parshios.getparsha(greg, israel=not diaspora)
    if not idx:
        return ""
    heb = parshios.getparsha_string(greg, israel=not diaspora, hebrew=True) or ""
    combined = heb.replace(", ", "-").strip()
    if not combined:
        return ""
    combined = combined.replace("אחרי מות", "אחרי")
    if metzora_display == "tahara":
        combined = combined.replace("מצורע", "טהרה")
    return combined


# ────────────────────────────────────────────────────────────────────────
# Pirkei Avos chapter
# ────────────────────────────────────────────────────────────────────────

def avos_skip_reason(shabbos: date_cls, *, diaspora: bool) -> str | None:
    """Skip reason for Pirkei Avos on this Shabbos, else None.
    Canonical port of the audited perek_avot_sensor rules (v0.7.8):
      • Shavuos on Shabbos (6 Sivan; also 7 Sivan in diaspora)
      • 9 Av on Shabbos itself (nidche)
      • 8 Av on Shabbos when 9 Av is Sunday (Erev Tisha b'Av)
    """
    from .helper import int_to_hebrew as _ith
    sh_hd = PHebrewDate.from_pydate(shabbos)
    if sh_hd.month == 3 and (sh_hd.day == 6 or (diaspora and sh_hd.day == 7)):
        return f"הדלגה — שבועות ({_ith(sh_hd.day)} סיון)"
    if sh_hd.month == 5 and sh_hd.day == 9:
        return "הדלגה — תשעה באב נדחה"
    if sh_hd.month == 5 and sh_hd.day == 8:
        if PHebrewDate(sh_hd.year, 5, 9).to_pydate().weekday() == 6:
            return "הדלגה — שבת חזון (ערב תשעה באב)"
    return None


def pirkei_avos_info(
    shabbos: date_cls, *, diaspora: bool,
) -> tuple[str, str | None, int | None, int | None]:
    """Canonical Pirkei Avos computation — THE single source shared by
    perek_avot_sensor and the luach. Returns
    ``(chapter_label, skip_reason, reading_index, reading_total)``:
      • outside the season → ("", None, None, None)
      • on a skipped Shabbos → ("", reason, None, total)
      • else → ("פרק X" / "פרק X-Y", None, index, total)

    Algorithm (audited v0.7.8 sensor): season runs from the first
    Shabbos strictly after the last day of Pesach through the last
    Shabbos before Rosh Hashana. Universal rule: always start at פרק א
    and end at פרק ו; doubles_needed = (6 − total_valid_weeks mod 6)
    mod 6, doubling lands at the END of the season walking the pairs
    (1-2)(3-4)(5-6) backward from (5-6).
    """
    from .helper import int_to_hebrew as _ith
    sh_hd = PHebrewDate.from_pydate(shabbos)
    hyear = sh_hd.year

    pesach_last_day = 22 if diaspora else 21
    pesach_py = PHebrewDate(hyear, 1, pesach_last_day).to_pydate()
    offset = (5 - pesach_py.weekday()) % 7 or 7
    first_shabbos = pesach_py + timedelta(days=offset)

    rh_py = PHebrewDate(hyear + 1, 7, 1).to_pydate()
    prev_day = rh_py - timedelta(days=1)
    last_shabbos = prev_day - timedelta(days=(prev_day.weekday() - 5) % 7)

    if not (first_shabbos <= shabbos <= last_shabbos):
        return ("", None, None, None)

    total = 0
    d = first_shabbos
    while d <= last_shabbos:
        if not avos_skip_reason(d, diaspora=diaspora):
            total += 1
        d += timedelta(days=7)

    reason = avos_skip_reason(shabbos, diaspora=diaspora)
    if reason:
        return ("", reason, None, total)

    valid_week_count = 0
    d = first_shabbos
    while d <= shabbos:
        if not avos_skip_reason(d, diaspora=diaspora):
            valid_week_count += 1
        d += timedelta(days=7)

    valid_remaining = 0
    d = shabbos
    while d <= last_shabbos:
        if not avos_skip_reason(d, diaspora=diaspora):
            valid_remaining += 1
        d += timedelta(days=7)

    doubles_needed = (6 - total % 6) % 6
    if 0 < valid_remaining <= doubles_needed:
        pairs = [(1, 2), (3, 4), (5, 6)]
        n1, n2 = pairs[(3 - valid_remaining) % 3]
        label = f"פרק {_ith(n1)}-{_ith(n2)}"
    else:
        n = ((valid_week_count - 1) % 6) + 1
        label = f"פרק {_ith(n)}"
    return (label, None, valid_week_count, total)


def pirkei_avos_for_shabbos(shabbos: date_cls, *, diaspora: bool) -> str:
    """Luach-facing wrapper: chapter label in season, '' on skipped
    Shabbosos and outside the season. NOW DELEGATES to the canonical
    ``pirkei_avos_info`` (audited sensor algorithm) — the previous
    independent implementation here predated the v0.7.8 doubling-rule
    and Tisha-b'Av-nidche fixes and disagreed with the sensor on ~7%%
    of season Shabbosos (audit bench); the printed luach now matches
    sensor.yidcal_perek_avot exactly.
    """
    label, _reason, _i, _t = pirkei_avos_info(shabbos, diaspora=diaspora)
    return label
def omer_day_for(d: date_cls) -> int:
    """Return the Omer day (1–49) for the GIVEN Hebrew date of ``d``'s
    daytime, or 0 if outside the Omer period.

    Note: this is the day-display value (what should appear on a row
    that's anchored to that calendar day). For the halachic counting
    threshold (which begins at tzeis), see sfirah_helper.py.
    """
    ph = PHebrewDate.from_pydate(d)
    if ph.month == 1 and ph.day >= 16:
        return ph.day - 15
    if ph.month == 2:
        return 15 + ph.day
    if ph.month == 3 and ph.day <= 5:
        return 44 + ph.day
    return 0


# ────────────────────────────────────────────────────────────────────────
# Rosh Chodesh + Mevorchim
# ────────────────────────────────────────────────────────────────────────

def _month_length(year: int, month: int) -> int:
    """Number of days in a Hebrew month (handles 29 vs 30 safely)."""
    try:
        PHebrewDate(year, month, 30)
        return 30
    except Exception:
        return 29


def _prior_month(year: int, month: int) -> tuple[int, int]:
    """Return (prior_year, prior_month) for a given Hebrew (year, month).
    Handles the Nisan/Tishrei year-boundary quirk in pyluach:
    pyluach increments the year number at Tishrei, so Nisan stays in
    the same year as the preceding Adar, but Tishrei is in a year
    number ONE HIGHER than the preceding Elul.
    """
    if month == 1:
        return (year, real_adar_month(year))
    if month == 7:
        return (year - 1, 6)  # Elul of prior year
    return (year, month - 1)


def rosh_chodesh_civil_days(year: int, month: int) -> list[date_cls]:
    """Return the 1 or 2 civil dates that are Rosh Chodesh for the
    given Hebrew month.

    If the prior month has 30 days, RC is a 2-day observance (30th of
    prior month + 1st of this month). Otherwise it's a single day
    (1st of this month).
    """
    out: list[date_cls] = []
    prior_year, prior_month = _prior_month(year, month)
    if _month_length(prior_year, prior_month) == 30:
        out.append(PHebrewDate(prior_year, prior_month, 30).to_pydate())
    out.append(PHebrewDate(year, month, 1).to_pydate())
    return sorted(out)


# Gimatria for small ordinals used in compound labels like
# "ה׳ דחנוכה" / "שבת א׳ דר״ח". Kept local because the only valid
# inputs are 1-8 (Chanukah days) and 1-2 (RC positions).
_SMALL_ORDINAL_HE = {
    1: "א׳", 2: "ב׳", 3: "ג׳", 4: "ד׳",
    5: "ה׳", 6: "ו׳", 7: "ז׳", 8: "ח׳",
}


def chol_hamoed_day(
    month: int, day: int, *, diaspora: bool, include_hoshana_rabbah: bool = True,
) -> tuple[str, int] | None:
    """Canonical Chol-HaMoed rule. Returns (chag, 1-based CHM day) or None.

    Diaspora: Pesach CHM = Nisan 17-20, Sukkos CHM = Tishrei 17-20 (+21
    Hoshana Rabbah when ``include_hoshana_rabbah``).
    Israel:   Pesach CHM = Nisan 16-20, Sukkos CHM = Tishrei 16-20 (+21).
    Verified equivalent to the pyluach-festival derivation used by
    day_type across 5779-5812, both modes (audit bench).
    """
    lo = 17 if diaspora else 16
    if month == 1 and lo <= day <= 20:
        return ("פסח", day - lo + 1)
    hi = 21 if include_hoshana_rabbah else 20
    if month == 7 and lo <= day <= hi:
        return ("סוכות", day - lo + 1)
    return None


def is_chol_hamoed(
    d: date_cls, *, diaspora: bool, include_hoshana_rabbah: bool = True,
) -> bool:
    """Civil-date convenience wrapper around ``chol_hamoed_day``."""
    ph = PHebrewDate.from_pydate(d)
    return chol_hamoed_day(
        ph.month, ph.day, diaspora=diaspora,
        include_hoshana_rabbah=include_hoshana_rabbah,
    ) is not None


def chanukah_day_for_date(d: date_cls) -> int | None:
    """Return the day of Chanukah (1-8) for a civil date, or None if
    the date is outside Chanukah. Handles both Kislev-30-day and
    Kislev-29-day years correctly by counting forward from 25 Kislev.
    """
    ph = PHebrewDate.from_pydate(d)
    # Quick reject: Chanukah spans 25 Kislev (month 9) through ~2-3 Teves.
    if ph.month == 9 and ph.day >= 25:
        return ph.day - 24  # 25→1 .. 30→6
    if ph.month == 10 and 1 <= ph.day <= 3:
        # Day 7/8 depends on whether Kislev has 30 days. Count forward
        # from 25 Kislev to avoid mis-mapping.
        day1 = PHebrewDate(ph.year, 9, 25).to_pydate()
        diff = (d - day1).days
        if 0 <= diff <= 7:
            return diff + 1
    return None


def chanukah_day_label_he(d: date_cls) -> str | None:
    """Return the SF-style Chanukah-day label (e.g. "ה׳ דחנוכה") for
    the given civil date, or None if d is not within Chanukah.
    """
    n = chanukah_day_for_date(d)
    if n is None:
        return None
    return f"{_SMALL_ORDINAL_HE[n]} דחנוכה"


def rc_day_position_for_date(d: date_cls) -> tuple[int, int] | None:
    """If ``d`` is a Rosh Chodesh day, return ``(position, total)``
    where ``total`` is the number of days in this RC (1 or 2) and
    ``position`` is the 1-indexed position of ``d`` within those days.

    Returns ``None`` if ``d`` is not a RC day.

    Examples:
      • RC Teves 5786 = (Sat 30 Kislev, Sun 1 Teves) → Sat returns
        (1, 2); Sun returns (2, 2).
      • RC Shvat 5786 = (Mon 1 Shvat) → returns (1, 1).
    """
    ph = PHebrewDate.from_pydate(d)
    # Case A: day 30 of any month — this is ALWAYS day 1 of a 2-day
    # RC for the next Hebrew month.
    if ph.day == 30:
        return (1, 2)
    # Case B: day 1 of any month — could be the only day of a 1-day
    # RC, or day 2 of a 2-day RC. Decide by checking the prior day.
    if ph.day == 1:
        prev = d - timedelta(days=1)
        ph_prev = PHebrewDate.from_pydate(prev)
        if ph_prev.day == 30:
            return (2, 2)
        return (1, 1)
    return None


def shabbos_rc_label_he(saturday: date_cls) -> str:
    """Return the SF-style "Shabbos that is RC" label, with the
    day-position qualifier added only when it's NOT redundant with
    the row's Hebrew date.

      • 1-day RC                            → 'שבת ר״ח'
      • 2-day RC and Shabbos is day 1      → 'שבת א׳ דר״ח'
        (informative — Friday is the regular 29-of-prev-month, RC
        starts tomorrow)
      • 2-day RC and Shabbos is day 2      → 'שבת ר״ח'
        (omitted — Friday is the 30-of-prev-month, already implying
        Saturday is RC day 2; SF prints just "שבת ר״ח" here)

    Falls back to plain 'שבת ר״ח' if ``saturday`` isn't a RC day
    (defensive — caller should only invoke this when the Shabbos
    actually IS RC).
    """
    pos_info = rc_day_position_for_date(saturday)
    if pos_info is None:
        return "שבת ר״ח"
    pos, total = pos_info
    if total == 2 and pos == 1:
        return f"שבת {_SMALL_ORDINAL_HE[pos]} דר״ח"
    return "שבת ר״ח"


def is_yt_without_shehecheyanu(
    d: date_cls,
    *,
    diaspora: bool,
) -> bool:
    """Return True when ``d`` is a Yom Tov day on which Shehecheyanu
    is NOT recited.

    Shehecheyanu is recited on the FIRST occurrence of a Yom Tov in
    the year — so on the 1st (and 2nd) day of each holiday. The
    blessing is NOT repeated on the 7th and 8th days of Pesach,
    because Pesach is considered one continuous Yom Tov whose
    Shehecheyanu was already said on day 1.

    Yom Tov days on which Shehecheyanu IS recited (RH, YK, Sukkos
    1-2, Shmini Atzeret + Simchas Torah, Pesach 1-2, Shavuos 1-2)
    return False here.

    Coverage:
      • 21 Nissan — Shvii Shel Pesach (diaspora AND Israel)
      • 22 Nissan — Acharon Shel Pesach (diaspora only)

    Designed for re-use by both the luach renderer (which displays
    the "א״א שהחיינו" marker on the Erev row) and a future Erev /
    candle-lighting sensor (which can expose this as a boolean
    attribute like ``Shehecheyanu_Tomorrow``).
    """
    ph = PHebrewDate.from_pydate(d)
    if ph.month == 1:  # Nissan
        if ph.day == 21:
            return True
        if ph.day == 22 and diaspora:
            return True
    return False


def mevorchim_shabbos_for_month(target_year: int, target_month: int) -> date_cls | None:
    """Return the Shabbos on which we bench HaChodesh for the given
    Hebrew month, or None for Tishrei (Tishrei is never benched).

    The rule: the Shabbos immediately preceding the FIRST Rosh
    Chodesh date of the month. When that Shabbos coincides with RC
    itself (i.e., RC is on Shabbos), we bench the PREVIOUS Shabbos.
    """
    if target_month == 7:
        return None
    rc_dates = rosh_chodesh_civil_days(target_year, target_month)
    first_rc = rc_dates[0]
    first_wd = first_rc.weekday()  # Mon=0 .. Sun=6
    if first_wd == 5:
        return first_rc - timedelta(days=7)
    days_back = (first_wd - 5) % 7
    return first_rc - timedelta(days=days_back)


def format_rc_days_he(rc_days_civil: list[date_cls]) -> str:
    """Format the RC weekdays in Mevorchim phrasing (NO parsha labels):
      • Single weekday: 'ר״ח יום ב׳'
      • Two-day RC, both normal weekdays: 'ר״ח יום ב׳ וג׳'
        (drops the redundant second 'יום' — Monroe/KJ convention).
      • Two-day RC involving Friday/Shabbos: 'ר״ח עש״ק ושב״ק'
        (special weekday names kept verbatim).

    For the WITH-parsha variant used in the Mevorchim announcement,
    see ``format_rc_days_with_parshas_he`` — that one labels each RC
    day with its own parsha when the parsha changes mid-RC (e.g.
    'ר״ח שב״ק מקץ ויום א׳ ויגש').
    """
    wd_names = {
        0: "יום ב׳", 1: "יום ג׳", 2: "יום ד׳", 3: "יום ה׳",
        4: "עש״ק",   5: "שב״ק",   6: "יום א׳",
    }
    parts = [wd_names[rd.weekday()] for rd in rc_days_civil]
    # Two-day RC, both 'יום X׳' weekdays → drop second 'יום' for brevity.
    if (
        len(parts) == 2
        and parts[0].startswith("יום ")
        and parts[1].startswith("יום ")
    ):
        # 'יום ב׳' + 'יום ג׳' → 'יום ב׳ וג׳'
        second_letter = parts[1].split(" ", 1)[1]  # 'ג׳'
        return f"ר״ח {parts[0]} ו{second_letter}"
    return "ר״ח " + " ו".join(parts)


def format_rc_days_with_parshas_he(
    rc_days_civil: list[date_cls],
    rc_parshas: list[str],
) -> str:
    """Format the RC weekdays with per-day parsha labels inlined.

    Each entry of ``rc_parshas`` is the parsha label that applies to
    the matching ``rc_days_civil`` entry (the parsha of the upcoming
    Shabbos for that day's week). Consecutive RC days that share the
    same parsha are grouped together using the standard short forms
    from ``format_rc_days_he`` (e.g., 'יום ב׳ וג׳ <parsha>'); when
    the parsha changes mid-RC, each group is rendered with its own
    parsha tail.

    Examples (Teves 5786 / SF convention):
        rc_days = [Sat 2025-12-20, Sun 2025-12-21]
        rc_parshas = ['מקץ', 'ויגש']
        → 'ר״ח שב״ק מקץ ויום א׳ ויגש'

        rc_days = [Wed, Thu]   (Cheshvan-style)
        rc_parshas = ['נח', 'נח']
        → 'ר״ח יום ד׳ וה׳ נח'

        rc_days = [Mon]
        rc_parshas = ['בא']
        → 'ר״ח יום ב׳ בא'
    """
    assert len(rc_days_civil) == len(rc_parshas)
    if not rc_days_civil:
        return "ר״ח"

    wd_names = {
        0: "יום ב׳", 1: "יום ג׳", 2: "יום ד׳", 3: "יום ה׳",
        4: "עש״ק",   5: "שב״ק",   6: "יום א׳",
    }

    # Group consecutive RC days that share the same parsha label.
    groups: list[tuple[list[date_cls], str]] = []
    for rd, ps in zip(rc_days_civil, rc_parshas):
        if groups and groups[-1][1] == ps:
            groups[-1][0].append(rd)
        else:
            groups.append(([rd], ps))

    def _render_group(days: list[date_cls], parsha: str) -> str:
        names = [wd_names[d.weekday()] for d in days]
        if (
            len(names) == 2
            and names[0].startswith("יום ")
            and names[1].startswith("יום ")
        ):
            # 'יום ב׳' + 'יום ג׳' → 'יום ב׳ וג׳'
            second_letter = names[1].split(" ", 1)[1]
            wd_part = f"{names[0]} ו{second_letter}"
        else:
            wd_part = " ו".join(names)
        if parsha:
            return f"{wd_part} {parsha}"
        return wd_part

    rendered = [_render_group(days, ps) for days, ps in groups]
    # Join groups: first group has "ר״ח " prefix; subsequent groups
    # get a "ו" (vav) prefix on the first character of the group's
    # weekday portion (matching SF: "...שב״ק מקץ ויום א׳ ויגש").
    out = "ר״ח " + rendered[0]
    for r in rendered[1:]:
        out += " ו" + r
    return out


# ────────────────────────────────────────────────────────────────────────
# Molad short-form formatter
# ────────────────────────────────────────────────────────────────────────

def parsha_current_for_date(
    civil: date_cls,
    *,
    diaspora: bool,
    metzora_display: str = "metzora",
) -> str:
    """Return the parsha "currently in effect" for the week containing
    ``civil`` — i.e., the parsha that will be read on the upcoming
    Shabbos (or that day's parsha if ``civil`` IS Shabbos).

    When the upcoming Shabbos falls on a chag (e.g., Shavuos or Yom Tov
    of Pesach in diaspora), ``parsha_name`` returns the empty string;
    in that case we walk backwards a week at a time, up to 6 weeks, to
    find the most recent Shabbos that DID have a parsha reading. That
    parsha is what's "currently in effect" in the weekly reading cycle.

    Returns the empty string when no parsha can be resolved within the
    look-back window (extremely unlikely in practice).
    """
    if civil.weekday() == 5:
        shabbos = civil
    else:
        days_ahead = (5 - civil.weekday()) % 7
        shabbos = civil + timedelta(days=days_ahead)
    probe = shabbos
    # Safety cap at 6 weeks back (Pesach + Shavuos has at most 1-2
    # consecutive parsha-less Shabbosos, so 6 is generous).
    for _ in range(6):
        try:
            p = parsha_name(
                probe, diaspora=diaspora, metzora_display=metzora_display,
            )
        except Exception:
            p = ""
        if p:
            return p
        probe = probe - timedelta(days=7)
    return ""


def parsha_for_mevorchim_rc_day_he(
    rc_day: date_cls,
    *,
    diaspora: bool,
    metzora_display: str = "metzora",
) -> str:
    """Return the parsha label for an RC day in the SF Mevorchim line,
    applying the chag-week disambiguation rule (matches the convention
    used by ``ParshaSensor`` for weekly parsha display):

    1. If the upcoming Shabbos for ``rc_day``'s week IS a parsha
       Shabbos, return that parsha (no suffix).
    2. Otherwise, if the week contains at least one regular weekday
       Mon/Thu (= a day that falls BEFORE Yom Tov starts), scan
       FORWARD for the next Shabbos with a parsha and return it with
       the SF ``א׳`` marker (e.g. ``"נשא א׳"``). The marker indicates
       "this parsha's first Mon/Thu krias has happened, but its
       Shabbos kriah has not — because Shabbos itself is YT".
    3. Otherwise, fall back to ``parsha_current_for_date`` (which
       walks back to the most recent parsha Shabbos) so callers
       always receive a non-empty string when at all possible.

    Concrete example (5786 Sivan):
      RC Sivan = Sun May 17, 2026. The week's Shabbos (May 23) is
      Shavuos day 2 — no parsha. But Mon May 18 and Thu May 21 fall
      before Shavuos starts (Fri May 22 night), so their Mon/Thu
      kriah uses נשא (the next parsha-bearing Shabbos's parsha, Sat
      May 30). This function returns ``"נשא א׳"``.
    """
    from hdate import HDateInfo  # local import — same pattern as is_no_melacha

    # Find the upcoming Shabbos (or rc_day itself if it's Shabbos)
    if rc_day.weekday() == 5:
        shabbos = rc_day
    else:
        days_ahead = (5 - rc_day.weekday()) % 7
        shabbos = rc_day + timedelta(days=days_ahead)

    # Direct hit: that Shabbos has a parsha
    try:
        direct = parsha_name(
            shabbos, diaspora=diaspora, metzora_display=metzora_display,
        )
    except Exception:
        direct = ""
    if direct:
        return direct

    # No parsha on that Shabbos. Are there regular Mon/Thu days in
    # that week? Week = Sunday-through-Friday before ``shabbos`` (we
    # already know Shabbos itself has no krias parsha).
    week_start = shabbos - timedelta(days=6)
    has_regular_mon_thu = False
    for i in range(6):  # Sun=0 .. Fri=5
        d = week_start + timedelta(days=i)
        if d.weekday() not in (0, 3):  # Mon=0, Thu=3
            continue
        try:
            is_yt = HDateInfo(d, diaspora=diaspora).is_yom_tov
        except Exception:
            is_yt = False
        if is_yt:
            continue
        has_regular_mon_thu = True
        break

    if has_regular_mon_thu:
        # Forward-scan up to 6 weeks for the next Shabbos with a
        # parsha and tag it with the SF ``א׳`` marker.
        probe = shabbos + timedelta(days=7)
        for _ in range(6):
            try:
                p = parsha_name(
                    probe, diaspora=diaspora, metzora_display=metzora_display,
                )
            except Exception:
                p = ""
            if p:
                return f"{p} א׳"
            probe += timedelta(days=7)

    # Fallback: existing walk-back behavior. Used when there's no
    # weekday krias to "carry" the next parsha forward (rare).
    return parsha_current_for_date(
        rc_day, diaspora=diaspora, metzora_display=metzora_display,
    )


def format_molad_short(
    m,
    *,
    diaspora: bool = True,
    metzora_display: str = "metzora",
    style: str = "monroe",
) -> str:
    """Format a Molad object (from yidcal_lib.helper.Molad) in Hebrew
    short phrasing. Two styles are supported:

    style="monroe" (default) — Monroe/KJ weekly-luach convention:
       Standard:    ``יום <wd> <parsha> <tod> H:MM ו<chalakim> חלקים``
       Pre-dawn (non-Sun): ``יום <wd> <parsha> באשה״ב H:MM ו<chalakim> חלקים``
       Late-evening: ``אור ליום <wd+1> <parsha> H:MM ו<chalakim> חלקים``
       Sun pre-dawn: ``מוצש״ק <prev-parsha> H:MM ו<chalakim> חלקים``

    style="sf" — South-Fallsburg yearly-luach convention:
       Standard:    ``יום <wd> <parsha> בשעה H:MM ו<chalakim> חלקים <tod>``
       Pre-dawn (non-Sun): ``אור ליום <wd> <parsha> בשעה H:MM ו<chalakim> חלקים``
       Late-evening: ``אור ליום <wd+1> <parsha> בשעה H:MM ו<chalakim> חלקים``
       Sun pre-dawn: ``מוצש״ק <prev-parsha> בשעה H:MM ו<chalakim> חלקים``

    Both styles share the same 6-tier time-of-day classification, the
    same Motzaei-Shabbos special case for Sunday pre-dawn, and the
    same parsha lookup (parsha of the upcoming Shabbos, or — for
    Motzaei Shabbos — the parsha of the Shabbos that just ended).

    Visible differences:
      • SF adds a ``בשעה`` prefix before the time and places the TOD
        word AFTER the chalakim, instead of before the time.
      • Monroe uses ``באשה״ב`` as a TOD suffix for pre-dawn 00:00–04:59;
        SF replaces that with an ``אור ל`` prefix on the current day
        (matching how Hebrew "the eve-of" is read for pre-dawn hours).

    Time-of-day tiers (24-hour):
      • 00:00 – 04:59 → pre-dawn (style-specific; see above)
      • 05:00 – 09:59 → ``בבוקר``    (early morning)
      • 10:00 – 11:59 → ``קודה״צ``   (late morning, קודם הצהריים)
      • 12:00 – 18:59 → ``אחה״צ``    (afternoon)
      • 19:00 – 20:59 → SF: ``לפנות ערב`` / Monroe: ``בערב``  (late afternoon)
      • 21:00 – 23:59 → ``אור ל`` prefix on NEXT day (both styles)

    The Molad class exposes: ``day`` (English weekday name), ``hours``
    (12-hr), ``minutes``, ``am_or_pm``, ``chalakim``, ``date`` (civil
    date object). The civil ``date`` attribute is what lets us resolve
    the parsha; if it's missing, the parsha portion is silently
    omitted and the line falls back to the day+time+chalakim form.
    """
    eng_to_he = {
        "Sunday": "א׳", "Monday": "ב׳", "Tuesday": "ג׳", "Wednesday": "ד׳",
        "Thursday": "ה׳", "Friday": "עש״ק", "Shabbos": "שב״ק", "Saturday": "שב״ק",
    }
    next_eng_day = {
        "Sunday": "Monday", "Monday": "Tuesday", "Tuesday": "Wednesday",
        "Wednesday": "Thursday", "Thursday": "Friday",
        "Friday": "Saturday", "Saturday": "Sunday", "Shabbos": "Sunday",
    }

    civil_day = getattr(m, "day", "")
    h12 = getattr(m, "hours", 0)
    mi = getattr(m, "minutes", 0)
    ampm = str(getattr(m, "am_or_pm", "am")).lower()
    parts = getattr(m, "chalakim", 0)
    civil = getattr(m, "date", None)

    # Convert 12-hour to 24-hour for time-of-day classification
    if ampm == "am":
        h24 = 0 if h12 == 12 else h12
    else:  # pm
        h24 = 12 if h12 == 12 else h12 + 12

    chal_phrase = ""
    if parts:
        chal_phrase = f" ו{int_to_hebrew_letters(parts)} חלקים"

    # Time fragment differs by style:
    #   Monroe:  "H:MM וP חלקים"     (chalakim trail the time directly)
    #   SF:      "בשעה H:MM וP חלקים" (with explicit "בשעה" prefix)
    if style == "sf":
        time_phrase = f"בשעה {h12}:{mi:02d}{chal_phrase}"
    else:
        time_phrase = f"{h12}:{mi:02d}{chal_phrase}"

    def _lookup_parsha(d):
        if d is None:
            return ""
        try:
            return parsha_current_for_date(
                d, diaspora=diaspora, metzora_display=metzora_display,
            ) or ""
        except Exception:
            return ""

    # ── Motzaei Shabbos special case (Sun pre-dawn 00:00 - 04:59) ──
    # Civil time is early Sunday morning, but in Hebrew terms this is
    # still the night-portion of the Shabbos that just ended.
    if 0 <= h24 < 5 and civil_day == "Sunday":
        prev_sat = (civil - timedelta(days=1)) if civil else None
        parsha = _lookup_parsha(prev_sat)
        parsha_phrase = f" {parsha}" if parsha else ""
        return f"מוצש״ק{parsha_phrase} {time_phrase}"

    parsha = _lookup_parsha(civil)
    parsha_phrase = f" {parsha}" if parsha else ""

    if style == "sf":
        # ── SF: pre-dawn (non-Sun) uses "אור ל" prefix on CURRENT day ──
        if h24 < 5:
            day_he = eng_to_he.get(civil_day, "")
            return f"אור ליום {day_he}{parsha_phrase} {time_phrase}"

        # ── SF: late evening uses "אור ל" prefix on NEXT day ──
        if h24 >= 21:
            day_he = eng_to_he.get(next_eng_day.get(civil_day, civil_day), "")
            return f"אור ליום {day_he}{parsha_phrase} {time_phrase}"

        # ── SF: standard 4-tier with TOD suffix at the end ──
        if h24 < 10:
            tod = "בבוקר"
        elif h24 < 12:
            tod = "קודה״צ"
        elif h24 < 19:
            tod = "אחה״צ"
        else:  # 19 - 20
            # SF prints "לפנות ערב" (approaching evening) for this tier
            # — actual ערב/nightfall in the Catskills sits closer to
            # 9:30 PM, so this is the more semantically accurate
            # description of late-afternoon hours like 7-8 PM. (Monroe
            # uses plain "בערב" in this slot.)
            tod = "לפנות ערב"
        day_he = eng_to_he.get(civil_day, "")
        return f"יום {day_he}{parsha_phrase} {time_phrase} {tod}"

    # ── Monroe (default) ──
    # 6-tier branching; pre-dawn uses "באשה״ב" suffix on CURRENT day;
    # late evening uses "אור ל" prefix on NEXT day.
    if h24 < 5:
        prefix, tod = "", "באשה״ב"
        day_he = eng_to_he.get(civil_day, "")
    elif h24 < 10:
        prefix, tod = "", "בבוקר"
        day_he = eng_to_he.get(civil_day, "")
    elif h24 < 12:
        prefix, tod = "", "קודה״צ"
        day_he = eng_to_he.get(civil_day, "")
    elif h24 < 19:
        prefix, tod = "", "אחה״צ"
        day_he = eng_to_he.get(civil_day, "")
    elif h24 < 21:
        prefix, tod = "", "בערב"
        day_he = eng_to_he.get(civil_day, "")
    else:  # 21 - 23: late evening, anchor to NEXT day with אור ל prefix
        prefix, tod = "אור ל", ""
        day_he = eng_to_he.get(next_eng_day.get(civil_day, civil_day), "")

    body = f"{prefix}יום {day_he}{parsha_phrase}"
    if tod:
        body = f"{body} {tod}"
    return f"{body} {time_phrase}"


# ────────────────────────────────────────────────────────────────────────
# Tekufos (תקופות) — solar-quarter dates
# ────────────────────────────────────────────────────────────────────────
# Tekufas Shmuel: each tekufah = 91 days 7.5 hours after the previous.
# The cycle is anchored to a known epoch and projected forward/back.

# Standard Shmuel anchor: Tekufas Tishrei 5786 = Oct 7 2025 09:00 EST
# (Standard Time, not DST) = 14:00 UTC.
# Validated against the printed Kiryas Joel luach 5786.
_TEKUFAS_TISHREI_5786_UTC = datetime(2025, 10, 7, 14, 0, tzinfo=ZoneInfo("UTC"))

# Length of one tekufah (one solar quarter).
_PERIOD = timedelta(days=91, hours=7, minutes=30)

# Names of the four tekufos in Tishrei-year order.
_TEKUFAH_NAMES = ("תשרי", "טבת", "ניסן", "תמוז")


@dataclass(frozen=True)
class TekufahEntry:
    """One computed Tekufah occurrence."""
    name: str                # 'תשרי' / 'טבת' / 'ניסן' / 'תמוז'
    hebrew_year: int         # The Hebrew year this Tekufah belongs to
    dt_utc: datetime         # Aware UTC datetime of the moment
    dt_local: datetime       # Aware datetime in the requested timezone

    @property
    def label_he(self) -> str:
        """Full Hebrew label, e.g. 'תקופת ניסן'."""
        return f"תקופת {self.name}"


def _tekufas_tishrei_utc(hebrew_year: int) -> datetime:
    """Return the UTC datetime of Tekufas Tishrei for the given
    Hebrew year. Computed by projecting from the 5786 anchor:
    each Hebrew year contains four tekufos, so the next Tishrei
    is 4 periods later (91d 7.5h × 4 = 365.25 days, the Julian year).
    """
    delta_years = hebrew_year - 5786
    return _TEKUFAS_TISHREI_5786_UTC + (4 * delta_years) * _PERIOD


def compute_tekufos_for_hebrew_year(
    hebrew_year: int,
    *,
    tz: ZoneInfo,
) -> list[TekufahEntry]:
    """Return all four Tekufos belonging to ``hebrew_year``, in
    chronological order: Tishrei, Teves, Nisan, Tammuz.

    All four are anchored to Tishrei of the given year (pyluach's
    standard year-numbering convention).
    """
    tishrei_utc = _tekufas_tishrei_utc(hebrew_year)
    out: list[TekufahEntry] = []
    for i, name in enumerate(_TEKUFAH_NAMES):
        utc = tishrei_utc + i * _PERIOD
        out.append(TekufahEntry(
            name=name,
            hebrew_year=hebrew_year,
            dt_utc=utc,
            dt_local=utc.astimezone(tz),
        ))
    return out


def compute_tekufos_in_range(
    *,
    start: date_cls,
    end: date_cls,
    tz: ZoneInfo,
) -> list[TekufahEntry]:
    """Return all Tekufah occurrences whose LOCAL date falls within
    ``[start, end]``. Walks the surrounding Hebrew years generously
    so events near the year-boundary aren't missed.
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    out: list[TekufahEntry] = []
    for hy in range(start_hy - 1, end_hy + 2):
        for tk in compute_tekufos_for_hebrew_year(hy, tz=tz):
            if start <= tk.dt_local.date() <= end:
                out.append(tk)
    out.sort(key=lambda t: t.dt_local)
    return out


def format_tekufah_time(dt_local: datetime) -> str:
    """Format a Tekufah's local time in the printed-luach convention:
    'H:MM <tod>' where ``tod`` is one of five MSM-matching labels.

    Time-of-day mapping (matches MSM / KJ printed luach convention):
      • 05:00 – 11:59 → בבוקר   (morning)
      • 12:00 – 16:59 → אחה״צ   (afternoon)
      • 17:00 – 20:59 → בערב    (evening, around/after sunset)
      • 21:00 – 23:59 → בלילה   (late night, post-shkia full dark)
      • 00:00 – 04:59 → באשה״ב  (pre-dawn, אשמורת הבוקר)
    """
    h = dt_local.hour
    mi = dt_local.minute
    h12 = h % 12 or 12
    if 5 <= h <= 11:
        tod = "בבוקר"
    elif 12 <= h <= 16:
        tod = "אחה״צ"
    elif 17 <= h <= 20:
        tod = "בערב"
    elif 21 <= h <= 23:
        tod = "בלילה"
    else:  # 0 <= h <= 4
        tod = "באשה״ב"
    return f"{h12}:{mi:02d} {tod}"


# ────────────────────────────────────────────────────────────────────────
# Behab (ב"ה"ב) — Mon/Thu/Mon fast cycle
# ────────────────────────────────────────────────────────────────────────

# Season identifiers; both follow the same rule shape, only the Hebrew
# month differs:
#   'cheshvan' → after Sukkos (Hebrew month 8)
#   'iyar'     → after Pesach (Hebrew month 2)
BEHAB_SEASON_TO_MONTH = {"cheshvan": 8, "iyar": 2}


@dataclass(frozen=True)
class BehabCycle:
    """One year's Behab cycle in a given season."""
    season: str                       # 'cheshvan' or 'iyar'
    hebrew_year: int                  # Hebrew year of the announcement
    mevorchim_shabbos: date_cls       # Shabbos of 'מברכין בה"ב'
    fast_mon_1: date_cls              # תענית שני קמא
    fast_thu: date_cls                # תענית חמישי
    fast_mon_2: date_cls              # תענית שני תנינא

    @property
    def fasts(self) -> tuple[date_cls, date_cls, date_cls]:
        """Three fast days in chronological order."""
        return (self.fast_mon_1, self.fast_thu, self.fast_mon_2)

    @property
    def fast_labels_he(self) -> tuple[str, str, str]:
        """Hebrew labels for the three fasts."""
        return ("תענית שני קמא", "תענית חמישי", "תענית שני תנינא")


def compute_behab_cycle(hebrew_year: int, season: str) -> BehabCycle:
    """Compute the Behab cycle for a single Hebrew year × season.

    Rule (matches KJ luach 5786 and standard Ashkenazi luach convention):
      1. ``מברכין בה"ב`` is announced on the FIRST Shabbos of the month
         that is NOT itself a Rosh Chodesh date.
      2. The three fasts fall +2, +5, +9 days after that Shabbos.
    """
    if season not in BEHAB_SEASON_TO_MONTH:
        raise ValueError(f"Unknown Behab season: {season!r}")
    month = BEHAB_SEASON_TO_MONTH[season]

    rc_dates = set(rosh_chodesh_civil_days(hebrew_year, month))
    first_of_month = PHebrewDate(hebrew_year, month, 1).to_pydate()

    days_to_sat = (5 - first_of_month.weekday()) % 7
    saturday = first_of_month + timedelta(days=days_to_sat)
    while saturday in rc_dates:
        saturday += timedelta(days=7)

    return BehabCycle(
        season=season,
        hebrew_year=hebrew_year,
        mevorchim_shabbos=saturday,
        fast_mon_1=saturday + timedelta(days=2),
        fast_thu=saturday + timedelta(days=5),
        fast_mon_2=saturday + timedelta(days=9),
    )


def compute_behab_in_range(
    *,
    start: date_cls,
    end: date_cls,
) -> list[BehabCycle]:
    """Return all Behab cycles whose Mevorchim Shabbos falls in
    ``[start, end]``. Walks the surrounding Hebrew years generously.
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    out: list[BehabCycle] = []
    for hy in range(start_hy - 1, end_hy + 2):
        for season in ("cheshvan", "iyar"):
            try:
                cyc = compute_behab_cycle(hy, season)
            except Exception:
                continue
            if start <= cyc.mevorchim_shabbos <= end:
                out.append(cyc)
    out.sort(key=lambda c: c.mevorchim_shabbos)
    return out


# ────────────────────────────────────────────────────────────────────────
# Fast date resolution (including נדחה push)
# ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FastDay:
    """One occurrence of a fast day."""
    kind: str                  # '17_tammuz' / 'tisha_bav' / 'asara_btevet' / ...
    label_he: str              # 'שבעה עשר בתמוז' / 'תשעה באב' / ...
    nominal_date: date_cls     # Hebrew-calendar nominal date
    actual_date: date_cls      # After נדחה adjustment (push from Shabbos)
    is_nidcheh: bool           # True if nominal_date was Shabbos


# ────────────────────────────────────────────────────────────────────────
# Canonical calendar-rule helpers — THE single source of truth for rules
# that used to be copy-pasted across sensor files (leap-year check, real
# Adar selection, observed/nidcheh fast dates, Purim dates). Sensors must
# import these instead of re-deriving:
#
#     from .yidcal_lib import halacha_events as he
#     he.tisha_bav_observed(hyear), he.real_adar_month(hyear), ...
# ────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────
# Canonical flag → window-kind map. THE single source for "when does a
# holiday attribute turn on/off": the holiday sensor consumes it live,
# and any range/JSON feature (e.g. "show everything between Erev Shavuos
# and Motzei Shavuos") should read the same map + the window math in
# zman_compute.compute_holiday_windows.
#
# Window kinds (start → end):
#   candle_havdalah   candles(erev)            → havdalah(day)
#   havdalah_havdalah motzei(prev day)*        → havdalah(day)   (*Friday-candles when day is Shabbos)
#   candle_both       candles(erev)            → havdalah(day+1)
#   alos_havdalah     alos(day)                → havdalah(day)
#   alos_candle       alos(day)                → candles(day)
#   candle_alos       candles(erev)            → alos(day)
#   havdalah_alos     motzei(prev day)         → alos(day)
#   havdalah_candle   motzei(prev day)         → candles(day)
#   candle_candle     candles(erev)            → candles(day, i.e. next-day candles)
# ────────────────────────────────────────────────────────────────────────
HOLIDAY_WINDOW_TYPE: dict[str, str] = {
        "א׳ סליחות":                     "havdalah_candle",
        "ערב ראש השנה":                  "havdalah_candle",
        "ראש השנה א׳":                   "candle_havdalah",
        "ראש השנה ב׳":                   "havdalah_havdalah",
        "ראש השנה א׳ וב׳":                "candle_both",
        "צום גדליה":                      "alos_havdalah",
        "שלוש עשרה מדות":                 "alos_candle",
        "ערב יום כיפור":                   "candle_candle",
        "יום הכיפורים":                    "candle_havdalah",
        "ערב סוכות":                      "havdalah_candle",
        "סוכות א׳":                       "candle_havdalah",
        "סוכות ב׳":                       "havdalah_havdalah",
        "סוכות א׳ וב׳":                    "candle_both",
        "א׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ב׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ג׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ד׳ דחול המועד סוכות":               "havdalah_havdalah",
        "ה׳ דחול המועד סוכות":               "havdalah_havdalah",
        "חול המועד סוכות":                  "havdalah_havdalah",
        "הושענא רבה":                     "havdalah_candle",
        "שמיני עצרת":                      "candle_havdalah",
        "שמחת תורה":                     "havdalah_havdalah",
        "אסרו חג סוכות":                   "havdalah_havdalah",
        "ערב חנוכה":                      "alos_havdalah",
        "חנוכה":                         "havdalah_havdalah",
        "ערב שבת חנוכה":                  "alos_candle",
        "שבת חנוכה":                      "candle_havdalah", 
        "שבת חנוכה ראש חודש":              "candle_havdalah",
        "א׳ דחנוכה":                      "havdalah_havdalah",
        "ב׳ דחנוכה":                      "havdalah_havdalah",
        "ג׳ דחנוכה":                      "havdalah_havdalah",
        "ד׳ דחנוכה":                      "havdalah_havdalah",
        "ה׳ דחנוכה":                      "havdalah_havdalah",
        "ו׳ דחנוכה":                      "havdalah_havdalah",
        "ז׳ דחנוכה":                      "havdalah_havdalah",
        "זאת חנוכה":                      "havdalah_havdalah",
        "שובבים":                        "havdalah_havdalah",
        "שובבים ת\"ת":                   "havdalah_havdalah",
        "צום עשרה בטבת":                 "alos_havdalah",
        "חמשה עשר בשבט":                "havdalah_havdalah",
        "תענית אסתר":                     "alos_havdalah",
        "תענית אסתר מוקדם":                "alos_havdalah",
        "פורים":                         "havdalah_havdalah",
        "שושן פורים":                     "havdalah_havdalah",
        "ערב בדיקת חמץ":                  "alos_havdalah",
        "ליל בדיקת חמץ":                   "havdalah_alos",
        "ערב פסח מוקדם":                  "havdalah_candle",
        "שבת ערב פסח":                   "candle_candle",
        "ערב פסח":                       "havdalah_candle",
        "פסח א׳":                        "candle_havdalah",
        "פסח ב׳":                        "havdalah_havdalah",
        "פסח א׳ וב׳":                     "candle_both",
        "א׳ דחול המועד פסח":                "havdalah_havdalah",
        "ב׳ דחול המועד פסח":                "havdalah_havdalah",
        "ג׳ דחול המועד פסח":                "havdalah_havdalah",
        "ד׳ דחול המועד פסח":                "havdalah_havdalah",
        "ה׳ דחול המועד פסח":                "havdalah_havdalah",
        "חול המועד פסח":                  "havdalah_candle",
        "שביעי של פסח":                   "candle_havdalah",
        "אחרון של פסח":                   "havdalah_havdalah",
        "אסרו חג פסח":                    "havdalah_havdalah",
        "פסח שני":                       "havdalah_havdalah",
        "ל\"ג בעומר":                    "havdalah_havdalah",
        "ערב שבועות":                    "havdalah_candle",
        "שבועות א׳":                     "candle_havdalah",
        "שבועות ב׳":                     "havdalah_havdalah",
        "שבועות א׳ וב׳":                  "candle_both",
        "אסרו חג שבועות":                "havdalah_havdalah",
        "צום שבעה עשר בתמוז":             "alos_havdalah",
        "ערב תשעה באב":                 "alos_havdalah",
        "תשעה באב":                    "candle_havdalah",
        "תשעה באב נדחה":                "candle_havdalah",
        "ט\"ו באב":                     "alos_havdalah",
        "יום כיפור קטן":                  "alos_havdalah",
        "ראש חודש":                    "havdalah_havdalah",
}


def is_leap_hebrew_year(hyear: int) -> bool:
    """True for a 13-month (מעוברת) Hebrew year.

    Uses the 19-year-cycle formula ((7y+1) mod 19) < 7 — identical to
    ``PYear(hyear).leap`` (asserted by the audit harness across 5779-5812)
    but allocation-free for hot loops.
    """
    return ((7 * hyear + 1) % 19) < 7


def real_adar_month(hyear: int) -> int:
    """The pyluach month number of the "real" Adar — the one Purim,
    Ta'anis Esther and Shushan Purim fall in: Adar II (13) in leap
    years, Adar (12) otherwise.
    """
    return 13 if is_leap_hebrew_year(hyear) else 12


def year_in_cycle(hyear: int) -> int:
    """1-based position of *hyear* within the 19-year machzor.

    Leap years fall on cycle years 3, 6, 8, 11, 14, 17 and 19.
    """
    return ((hyear - 1) % 19) + 1


def next_leap_year(hyear: int) -> int:
    """First leap (מעוברת) year strictly after *hyear*."""
    y = hyear + 1
    while not is_leap_hebrew_year(y):
        y += 1
    return y


# The נוסח printed at the foot of the sheet whenever a pruzbol note appears.
# Lives HERE, not in the renderer, so the sheet and the sensors quote the
# identical text.
PRUZBOL_FOOTNOTE_HE = (
    "*נוסח הפרוזבול: במותב תלתא כחדא הוינא ואתא פלוני המלוה ואמר "
    "לפנינו: מוסרני לכם פלוני ופלוני הדיינים שבמקום פלוני שכל חוב "
    "שיש לי שאגבנו כל זמן שארצה (הדיינים צריכים לחתום)"
)


def pruzbol_note(
    hebrew_year_incoming: int, kind: str, *, star: bool = False,
) -> str:
    """The printed-luach line for a pruzbol day.

    ``hebrew_year_incoming`` is the year Rosh Hashana brings IN (the one the
    line names). ``kind`` comes from :func:`pruzbol_kind`.

        required → בער״ה תשפ״ג צריכין לעשות פרוזבול
        chumra   → בער״ה תשפ״ב יש מחמירים לעשות פרוזבול (לכתחלה)

    ``star`` adds the footnote marker (the sheet stars only the FIRST note
    on the page; a sensor has no footnote to tie, so it passes False).
    """
    body = (
        "יש מחמירים לעשות פרוזבול (לכתחלה)" if kind == "chumra"
        else "צריכין לעשות פרוזבול"
    )
    return f"בער״ה {hebrew_year_letters(hebrew_year_incoming)} {body}" + (
        "*" if star else ""
    )


def pruzbol_shmita_year(d: date_cls) -> int | None:
    """The SHMITA year this pruzbol day brackets (same year either way).

    29 Elul 5781 (chumra, entering) and 29 Elul 5782 (required, leaving)
    both bracket shmita year 5782.
    """
    kind = pruzbol_kind(d)
    if not kind:
        return None
    ph = PHebrewDate.from_pydate(d)
    return ph.year + 1 if kind == "chumra" else ph.year


def pruzbol_kind(d: date_cls) -> str | None:
    """Which pruzbol note (if any) belongs on ``d`` — 29 Elul.

    The printed SF sheets carry TWO different notes, because the two
    Erev-RH days that bracket a shmita year are not the same halacha:

      * ``"chumra"``  — Erev RH ENTERING shmita (29 Elul of cycle
        year 6). 'יש מחמירים לעשות פרוזבול (לכתחלה)'.
      * ``"required"`` — Erev RH LEAVING shmita (29 Elul of the
        shmita year itself, cycle year 7): shevi'is has just ended
        and the debts are about to be cancelled.
        'צריכין לעשות פרוזבול'.

    A shmita-year sheet therefore shows BOTH — one at the top, one
    near the bottom (verified against the printed SF 5782 sheet).

    SINGLE SOURCE OF TRUTH: the yearly luach's notes/footnote and any
    future binary_sensor.yidcal_pruzbol both read from HERE.
    """
    try:
        ph = PHebrewDate.from_pydate(d)
        if not (ph.month == 6 and ph.day == 29):   # 29 Elul
            return None
        cyc = shmita_cycle_year(ph.year)
        if cyc == 7:
            return "required"
        if cyc == 6:
            return "chumra"
        return None
    except Exception:
        return None


def needs_pruzbol(d: date_cls, *, diaspora: bool = True) -> bool:
    """True on any Erev-RH that carries a pruzbol note (either kind).

    Pruzbol is written at the END of the shevi'is year — i.e. on
    29 Elul of the shmita year, which is Erev RH of the NEXT year.
    (Printed SF 5783 sheet: "בער״ה תשפ״ג צריכין לעשות פרוזבול" —
    5782 was shmita.)

    Thin wrapper over ``pruzbol_kind()`` — use that when the
    distinction (chumra vs required) matters.
    """
    return pruzbol_kind(d) is not None


def shmita_cycle_year(hyear: int) -> int:
    """Position of *hyear* within the 7-year shmita cycle (1..7).

    Calibration: 5782 mod 7 == 0 was a shmita year, so shmita ==
    cycle year 7. ``kvius_components`` delegates here.
    """
    return ((hyear - 1) % 7) + 1


def is_shmita_year(hyear: int) -> bool:
    """True when *hyear* is a Shmita year (year 7 of the cycle)."""
    return shmita_cycle_year(hyear) == 7


def years_until_shmita(hyear: int) -> int:
    """Whole years from *hyear* until the next Shmita; 0 during
    Shmita itself."""
    return 7 - shmita_cycle_year(hyear)


def next_shmita_year(hyear: int) -> int:
    """The Shmita year being counted down to (== *hyear* during
    Shmita)."""
    return hyear + years_until_shmita(hyear)


def _push_from_shabbos(d: date_cls) -> date_cls:
    """Push a date forward by 1 day if it falls on Shabbos. Used for
    fasts that move from their nominal date to Sunday."""
    return d + timedelta(days=1) if d.weekday() == 5 else d


def tzom_gedaliah_observed(hyear: int) -> date_cls:
    """Observed civil date of Tzom Gedaliah in ``hyear``:
    3 Tishrei, pushed to Sunday 4 Tishrei when 3 Tishrei is Shabbos
    (which happens when Rosh Hashana day 1 falls on Thursday).
    """
    return _push_from_shabbos(PHebrewDate(hyear, 7, 3).to_pydate())


def asara_bteves_observed(hyear: int) -> date_cls:
    """Observed civil date of Asara B'Teves — always 10 Teves (the
    fixed calendar never puts it on Shabbos, so no push rule exists).
    """
    return PHebrewDate(hyear, 10, 10).to_pydate()


def shiva_asar_btamuz_observed(hyear: int) -> date_cls:
    """Observed civil date of Shiva Asar B'Tammuz: 17 Tammuz, pushed
    to Sunday 18 Tammuz when it falls on Shabbos.
    """
    return _push_from_shabbos(PHebrewDate(hyear, 4, 17).to_pydate())


def tisha_bav_observed(hyear: int) -> date_cls:
    """Observed civil date of Tisha B'Av: 9 Av, pushed (nidche) to
    Sunday 10 Av when 9 Av falls on Shabbos.
    """
    return _push_from_shabbos(PHebrewDate(hyear, 5, 9).to_pydate())


def is_tisha_bav_nidche(hyear: int) -> bool:
    """True when 9 Av falls on Shabbos and the fast is observed Sunday."""
    return PHebrewDate(hyear, 5, 9).to_pydate().weekday() == 5


def taanis_esther_observed(hyear: int) -> date_cls:
    """Observed civil date of Ta'anis Esther: 13 of the real Adar,
    moved BACKWARD to Thursday 11 Adar when 13 Adar is Shabbos (the
    only fast that moves back — Friday would interfere with Shabbos
    preparations).
    """
    nominal = PHebrewDate(hyear, real_adar_month(hyear), 13).to_pydate()
    return nominal - timedelta(days=2) if nominal.weekday() == 5 else nominal


def purim_date(hyear: int) -> date_cls:
    """Civil date of Purim: 14 of the real Adar (never on Shabbos)."""
    return PHebrewDate(hyear, real_adar_month(hyear), 14).to_pydate()


def shushan_purim_date(hyear: int) -> date_cls:
    """Nominal civil date of Shushan Purim: 15 of the real Adar."""
    return PHebrewDate(hyear, real_adar_month(hyear), 15).to_pydate()


def shushan_purim_observed(hyear: int) -> date_cls:
    """Observed civil date of Shushan Purim for motzei purposes:
    15 of the real Adar, deferred to Sunday 16 Adar when 15 Adar is
    Shabbos (Purim Meshulash).
    """
    return _push_from_shabbos(shushan_purim_date(hyear))


def fasts_in_range(
    *,
    start: date_cls,
    end: date_cls,
) -> list[FastDay]:
    """Return the post-Sinai-prophetic fasts (excluding Yom Kippur)
    whose actual date falls in ``[start, end]``.

    Covers 17 Tammuz, Tisha B'Av, Tzom Gedaliah, Asara B'Teves,
    Ta'anis Esther. Same dates and nidcheh rules apply in diaspora
    and Israel for all five.
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year

    fast_specs = [
        ("17_tammuz",      "שבעה עשר בתמוז",   4, 17),
        ("tisha_bav",      "תשעה באב",          5, 9),
        ("tzom_gedaliah",  "צום גדלי׳",        7, 3),
        ("asara_btevet",   "עשרה בטבת",         10, 10),
    ]

    out: list[FastDay] = []
    for hy in range(start_hy - 1, end_hy + 2):
        for kind, label, month, day in fast_specs:
            try:
                nominal = PHebrewDate(hy, month, day).to_pydate()
            except Exception:
                continue
            # Asara B'Teves is the one fast that is NOT pushed from
            # Shabbos (it can never fall on Shabbos by the fixed
            # calendar's structure anyway). All others are pushed.
            # (Same rule as the canonical *_observed helpers above.)
            actual = _push_from_shabbos(nominal) if kind != "asara_btevet" else nominal
            if start <= actual <= end:
                out.append(FastDay(
                    kind=kind, label_he=label,
                    nominal_date=nominal, actual_date=actual,
                    is_nidcheh=(actual != nominal),
                ))
        # Ta'anis Esther: 13 of the LAST Adar (Adar II in leap years).
        # Unique nidcheh rule among the post-Sinai fasts: when 13 Adar
        # falls on Shabbos, the fast is pushed BACKWARD to Thursday
        # 11 Adar (not forward to Sunday like the others), because
        # Friday 12 Adar is Erev Shabbos and fasting would interfere
        # with Shabbos preparations.
        try:
            te_nominal = PHebrewDate(hy, real_adar_month(hy), 13).to_pydate()
            te_actual = taanis_esther_observed(hy)
            if start <= te_actual <= end:
                out.append(FastDay(
                    kind="taanis_esther",
                    label_he="תענית אסתר",
                    nominal_date=te_nominal,
                    actual_date=te_actual,
                    is_nidcheh=(te_actual != te_nominal),
                ))
        except Exception:
            pass
    out.sort(key=lambda f: f.actual_date)
    return out


# ────────────────────────────────────────────────────────────────────────
# Minor days: Pesach Sheni, Lag BaOmer, 15 Av
# ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MinorDay:
    """One minor calendar day (not a YT, not a fast)."""
    kind: str                  # 'pesach_sheni' / 'lag_baomer' / 'tu_bav'
    label_he: str
    civil_date: date_cls


def minor_days_in_range(
    *,
    start: date_cls,
    end: date_cls,
) -> list[MinorDay]:
    """Return Pesach Sheni / Lag BaOmer / 15 Av / Purim / Shushan Purim
    occurrences in range.

    Helper is location-neutral — emits ALL kinds as raw data. Each
    consumer filters by its own observance.

    Kinds and their observance:
      • ``pesach_sheni``, ``lag_baomer``, ``tu_bav`` — same date for
        diaspora and Israel.
      • ``purim`` (14 Adar) — observed in diaspora.
      • ``shushan_purim`` (15 Adar) — observed in Israel / Jerusalem.

    Recommended diaspora-vs-Israel filter (mirrors the integration's
    ``is_in_israel`` flag, where ``diaspora = not is_in_israel``):

        skip = {"shushan_purim"} if diaspora else {"purim"}
        for m in minor_days_in_range(start=..., end=...):
            if m.kind in skip:
                continue
            ...
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    specs = [
        ("pesach_sheni",     "פסח שני",        2, 14),  # Iyar 14
        ("lag_baomer",       "ל״ג בעומר",      2, 18),  # Iyar 18
        ("tu_bav",           "ט״ו באב",        5, 15),  # Av 15
        ("chanukah_night_1", "ליל א׳ דחנוכה",  9, 25),  # Kislev 25 = Day 1 daytime
        ("tu_bishvat",       "ט״ו בשבט",      11, 15),  # Shvat 15
    ]
    out: list[MinorDay] = []
    for hy in range(start_hy - 1, end_hy + 2):
        for kind, label, month, day in specs:
            try:
                d = PHebrewDate(hy, month, day).to_pydate()
            except Exception:
                continue
            if start <= d <= end:
                out.append(MinorDay(kind=kind, label_he=label, civil_date=d))
        # Purim + Shushan Purim: 14 and 15 of the LAST Adar (Adar II in
        # leap years). Both are emitted; consumers filter by observance.
        try:
            adar_last = 13 if PYear(hy).leap else 12
            purim_d = PHebrewDate(hy, adar_last, 14).to_pydate()
            if start <= purim_d <= end:
                out.append(MinorDay(
                    kind="purim", label_he="פורים", civil_date=purim_d,
                ))
            shushan_d = PHebrewDate(hy, adar_last, 15).to_pydate()
            if start <= shushan_d <= end:
                out.append(MinorDay(
                    kind="shushan_purim", label_he="שושן פורים",
                    civil_date=shushan_d,
                ))
        except Exception:
            pass
    out.sort(key=lambda m: m.civil_date)
    return out


# ────────────────────────────────────────────────────────────────────────
# DST (Daylight Saving Time) transitions
# ────────────────────────────────────────────────────────────────────────
# Tz-aware (independent of the Hebrew calendar). Intended as the single
# canonical source for DST events across YidCal — usable by sensors,
# the luach generator, and the planned calendar entity. Works for any
# IANA tz that observes DST (not US-specific).

@dataclass(frozen=True)
class DstChange:
    """One DST transition.

    Attributes:
        kind:        ``'dst_start'`` (spring forward) or ``'dst_end'``
                     (fall back).
        civil_date:  The civil date on which the transition took effect
                     (typically a Sunday in the US, but any weekday is
                     possible for other timezones).
    """
    kind: str
    civil_date: date_cls


def dst_changes_in_range(
    *,
    start: date_cls,
    end: date_cls,
    tz: ZoneInfo,
) -> list[DstChange]:
    """Return all DST transitions in ``[start, end]`` for the given tz.

    Detects transitions by comparing the UTC offset at noon on each
    day against noon on the preceding day. Spring-forward (offset
    moves closer to UTC, i.e. utcoffset increases) is ``'dst_start'``;
    fall-back is ``'dst_end'``. Returns an empty list for timezones
    that don't observe DST.
    """
    out: list[DstChange] = []
    d = start
    while d <= end:
        prev = d - timedelta(days=1)
        off_prev = datetime(
            prev.year, prev.month, prev.day, 12, tzinfo=tz,
        ).utcoffset()
        off_curr = datetime(
            d.year, d.month, d.day, 12, tzinfo=tz,
        ).utcoffset()
        if off_prev != off_curr:
            kind = "dst_start" if off_curr > off_prev else "dst_end"
            out.append(DstChange(kind=kind, civil_date=d))
        d += timedelta(days=1)
    return out


def is_dst_in_effect(d: date_cls, *, tz: ZoneInfo) -> bool:
    """True if DST is in effect at noon on ``d`` in ``tz``.

    Convenience helper for sensors / calendar consumers that need a
    point-in-time check rather than a range scan.
    """
    dt = datetime(d.year, d.month, d.day, 12, tzinfo=tz)
    dst_offset = dt.dst()
    return dst_offset is not None and dst_offset.total_seconds() != 0


# ────────────────────────────────────────────────────────────────────────
# Tal U'Matar (השאלה) recitation starts
# ────────────────────────────────────────────────────────────────────────
# Two distinct observances:
#   • Diaspora:  start at Maariv of day 60 after Tekufas Tishrei
#                (= Tekufas Tishrei civil date + 59 days).
#   • Israel:    start at Maariv of 7 Cheshvan.
# Helper is location-neutral — emits BOTH as raw data; consumers
# filter by their config (matches the Purim/Shushan Purim pattern).

@dataclass(frozen=True)
class TalUmatarStart:
    """One Tal U'Matar / Hashalah recitation start.

    Attributes:
        observance:  ``'diaspora'`` or ``'israel'``.
        civil_date:  The civil day on which Tal U'Matar is first
                     recited in the daytime tefilos. (Recitation
                     actually begins at Maariv of the prior evening,
                     since Hebrew night starts at sunset.)
        hebrew_year: The Hebrew year this event belongs to.
    """
    observance: str
    civil_date: date_cls
    hebrew_year: int


def tal_umatar_starts_in_range(
    *,
    start: date_cls,
    end: date_cls,
    tz: ZoneInfo,
) -> list[TalUmatarStart]:
    """Return all Tal U'Matar recitation-start occurrences whose
    civil date falls in ``[start, end]``. Emits BOTH diaspora and
    Israel observances as raw data — consumers filter.

    Recommended diaspora-vs-Israel filter:

        wanted = "diaspora" if diaspora else "israel"
        for s in tal_umatar_starts_in_range(...):
            if s.observance != wanted: continue
            ...
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    out: list[TalUmatarStart] = []
    for hy in range(start_hy - 1, end_hy + 2):
        # Diaspora: Tekufas Tishrei + 59 days (= day 60 inclusive).
        try:
            tk_tishrei_utc = _tekufas_tishrei_utc(hy)
            tk_local_date = tk_tishrei_utc.astimezone(tz).date()
            diaspora_start = tk_local_date + timedelta(days=59)
            if start <= diaspora_start <= end:
                out.append(TalUmatarStart(
                    observance="diaspora",
                    civil_date=diaspora_start,
                    hebrew_year=hy,
                ))
        except Exception:
            pass
        # Israel: 7 Cheshvan (month 8, day 7).
        try:
            israel_start = PHebrewDate(hy, 8, 7).to_pydate()
            if start <= israel_start <= end:
                out.append(TalUmatarStart(
                    observance="israel",
                    civil_date=israel_start,
                    hebrew_year=hy,
                ))
        except Exception:
            pass
    out.sort(key=lambda t: t.civil_date)
    return out


# ────────────────────────────────────────────────────────────────────────
# Erev Yom Tov detection
# ────────────────────────────────────────────────────────────────────────

def erev_yt_name(d: date_cls, *, diaspora: bool) -> str | None:
    """If ``d`` is the day BEFORE a major Yom Tov (and not itself a
    YT/Shabbos), return the YT's display name. Returns None otherwise.

    This is the structural "Erev YT" predicate — for candle-lighting
    purposes the lighting event itself is computed by zman_compute /
    zman_sensors.lighting_event_for_day.
    """
    if _is_no_mel_internal(d, diaspora=diaspora):
        return None
    tom = d + timedelta(days=1)
    ph = PHebrewDate.from_pydate(tom)
    m, dd = ph.month, ph.day
    if m == 1 and dd == 15:
        return "פסח"
    if m == 3 and dd == 6:
        return "שבועות"
    if m == 7 and dd == 1:
        return "ראש השנה"
    if m == 7 and dd == 10:
        return "יום כיפור"
    if m == 7 and dd == 15:
        return "סוכות"
    if m == 1 and dd == 21 and not _is_no_mel_internal(d, diaspora=diaspora):
        return "שביעי של פסח"
    return None


# ────────────────────────────────────────────────────────────────────────
# Special Shabbos labels — re-export from the existing ``specials`` module
# ────────────────────────────────────────────────────────────────────────

def special_shabbos_labels(
    saturday: date_cls,
    *,
    diaspora: bool,
) -> list[str]:
    """Return any special-Shabbos labels for ``saturday`` (a list,
    since multiple can apply on the same Shabbos — e.g. שבת ר״ח plus
    שבת שובה in some years).

    Thin wrapper around the existing ``specials.get_special_shabbos_name``
    that splits the dash-joined result and handles signature variants
    across versions.

    Note: the Chanukah qualifier is NOT injected here. The day-of-
    Chanukah label depends on the ROW's civil date (the Erev/Friday),
    not the Saturday's date, so it must be added by the caller using
    ``chanukah_day_label_he(row.civil_date)``.
    """
    from . import specials
    try:
        spec = specials.get_special_shabbos_name(
            today=saturday, is_in_israel=not diaspora
        )
    except TypeError:
        spec = specials.get_special_shabbos_name(today=saturday)
    labels = (
        [s.strip() for s in spec.split("-") if s.strip()]
        if spec else []
    )
    return labels


# ────────────────────────────────────────────────────────────────────────
# Mevorchim announcement events (for date ranges)
# ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MevorchimEvent:
    """One Mevorchim HaChodesh announcement."""
    civil_date: date_cls       # The Mevorchim Shabbos
    hebrew_year: int           # Year of the month being benched
    hebrew_month: int          # Month being benched (1=Nisan .. 13=Adar II)
    month_name_he: str         # Display name ('אייר', 'אדר א׳', etc.)
    rc_civil_days: list[date_cls]  # 1 or 2 dates of RC
    rc_phrase_he: str          # 'ר״ח יום ב׳' / 'ר״ח עש״ק ושב״ק'


def mevorchim_in_range(
    *,
    start: date_cls,
    end: date_cls,
) -> list[MevorchimEvent]:
    """Return all Mevorchim Shabbosos whose date falls in ``[start, end]``.
    Tishrei is excluded (we never bench Tishrei).
    """
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    out: list[MevorchimEvent] = []
    for hy in range(start_hy - 1, end_hy + 2):
        max_month = 13 if PYear(hy).leap else 12
        for tm in range(1, max_month + 1):
            if tm == 7:
                continue
            try:
                sb = mevorchim_shabbos_for_month(hy, tm)
            except Exception:
                sb = None
            if sb is None or not (start <= sb <= end):
                continue
            rc = rosh_chodesh_civil_days(hy, tm)
            out.append(MevorchimEvent(
                civil_date=sb,
                hebrew_year=hy,
                hebrew_month=tm,
                month_name_he=hebrew_month_name(hy, tm),
                rc_civil_days=rc,
                rc_phrase_he=format_rc_days_he(rc),
            ))
    out.sort(key=lambda m: m.civil_date)
    return out
