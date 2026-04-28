"""
custom_components/yidcal/yidcal_lib/zman_day_label.py

Pure helper that builds a human-friendly Hebrew label for any given
Gregorian date, using only information already available from
``pyluach`` and the existing ``yidcal_lib.specials`` module.

Used by the Zmanim Lookup sensor / service. Examples of output:

  • Regular weekday:      'ליום ד׳ פרשת אחרי-קדושים'
  • Shabbos:              'לשבת פרשת פנחס'
  • Shabbos + special:    'לשבת פרשת נצבים - שבת שובה'
  • Shabbos Chol HaMoed:  'לשבת חול המועד פסח'
  • Yom Tov weekday:      'לפסח א׳' / 'לראש השנה ב׳' / 'ליום כיפור'
  • Chol HaMoed weekday:  'לא׳ דחול המועד פסח'
  • Shevii/Achron Pesach: 'לשביעי של פסח' / 'לאחרון של פסח'
  • Hoshana Rabbah:       'להושענא רבה'
  • Fast day:             'לתשעה באב' / 'לי״ז בתמוז' (pyluach-supplied)
  • Purim / Chanukah:     'לפורים' / 'לחנוכה א׳'
  • Rosh Chodesh:         'לראש חודש סיון' (Cheshvan 30 + Kislev 1)
  • Lag BaOmer:           'לל״ג בעומר'
  • Tu B'Shvat:           'לט״ו בשבט' (manual — pyluach omits)

Modern Israeli holidays (Yom HaAtzmaut, Yom HaShoah, Yom Yerushalayim,
Yom HaZikaron) are intentionally **not** labeled — they're excluded so
the sensor only shows halachic / traditional observances.
"""
from __future__ import annotations

from datetime import date as date_cls, timedelta

from pyluach import dates as pl_dates, parshios
from pyluach.hebrewcal import HebrewDate as PHebrewDate, Year

from . import specials


# Modern holidays we never want to surface — pyluach doesn't return them
# by default but this is belt-and-suspenders in case a future version
# adds them.
_MODERN_HOLIDAYS = {
    "יום השואה",
    "יום הזיכרון",
    "יום העצמאות",
    "יום ירושלים",
}


# Python weekday (Mon=0..Sun=6) → Hebrew short weekday
_WEEKDAY_HEB = {
    0: "יום ב׳",  # Mon
    1: "יום ג׳",  # Tue
    2: "יום ד׳",  # Wed
    3: "יום ה׳",  # Thu
    4: "יום ו׳",  # Fri
    # 5 (Sat) handled separately as "שבת"
    6: "יום א׳",  # Sun
}


def _chm_info(month: int, day: int, diaspora: bool) -> tuple[bool, str, int]:
    """Return (is_chm, 'פסח'/'סוכות', chm_day_number_1_based).

    Pesach: diaspora CHM = Nisan 17–20 (4 days), Israel = 16–20 (5 days).
    Sukkos: diaspora CHM = Tishrei 17–20 (4 days), Israel = 16–20 (5 days).
    (Hoshana Rabbah / Tishrei 21 is treated separately — not CHM here.)
    """
    if month == 1:  # Nisan
        if diaspora and 17 <= day <= 20:
            return (True, "פסח", day - 16)
        if not diaspora and 16 <= day <= 20:
            return (True, "פסח", day - 15)
    elif month == 7:  # Tishrei
        if diaspora and 17 <= day <= 20:
            return (True, "סוכות", day - 16)
        if not diaspora and 16 <= day <= 20:
            return (True, "סוכות", day - 15)
    return (False, "", 0)


_DAY_LETTER = {1: "א׳", 2: "ב׳", 3: "ג׳", 4: "ד׳", 5: "ה׳"}


def _is_rosh_chodesh_and_month(ph: PHebrewDate) -> tuple[bool, str]:
    """Return (is_rc, upcoming_month_name).

    RC covers day 1 of every month, plus day 30 when the preceding month
    has 30 days (Nisan, Sivan, Av, Tishrei, Shevat always; Cheshvan &
    Kislev conditionally; Adar I in leap years).
    """
    if ph.day == 1:
        return (True, _month_name_hebrew(ph))
    if ph.day == 30:
        # Day 30 of a 30-day month = first of the two RC days for next month.
        next_day = ph + 1  # pyluach supports + int (days)
        return (True, _month_name_hebrew(next_day))
    return (False, "")


def _month_name_hebrew(ph: PHebrewDate) -> str:
    """Hebrew month name, with proper Adar I/II disambiguation."""
    try:
        is_leap = Year(ph.year).leap
    except Exception:
        is_leap = False
    if is_leap and ph.month == 12:
        return "אדר א׳"
    if is_leap and ph.month == 13:
        return "אדר ב׳"
    return ph.month_name(hebrew=True)


def _reorder_prefix_day(label: str) -> str:
    """pyluach returns 'א׳ פסח' / 'ב׳ סוכות'. Flip to 'פסח א׳' ordering,
    which reads more naturally in contemporary Hebrew.

    Do NOT flip fast-day patterns like 'י׳ בטבת' or 'ט׳ באב' — there the
    second word starts with the preposition ב, so 'day + preposition +
    month' is already the natural order.
    """
    if not label:
        return label
    parts = label.split(" ", 1)
    if len(parts) == 2 and parts[0].endswith("׳") and len(parts[0]) <= 2:
        if parts[1].startswith("ב"):
            # Fast-day pattern — leave as-is
            return label
        return f"{parts[1]} {parts[0]}"
    return label


def _is_major_yt(ph: PHebrewDate, diaspora: bool) -> bool:
    """True if the given Hebrew date is a major Yom Tov — i.e. the
    regular Shabbos parsha is suppressed when this falls on Shabbos.

    Covers: Pesach (all 8 days incl. CHM and Shevii/Achron), Shavuos,
    Rosh Hashana, Yom Kippur, Sukkos (all days incl. CHM & Hoshana
    Rabbah), Shmini Atzeres, Simchas Torah.

    Minor holidays (Chanukah, Purim, Tu B'Shvat, Lag BaOmer, fast days)
    return False so the parsha still shows on Shabbos, with the holiday
    name appended as a suffix.
    """
    m, d = ph.month, ph.day
    # Pesach + CHM + Shevii/Achron
    if m == 1 and 15 <= d <= (22 if diaspora else 21):
        return True
    # Shavuos
    if m == 3 and (d == 6 or (d == 7 and diaspora)):
        return True
    # Tishrei majors: RH (1–2), YK (10), Sukkos+CHM+Shmini+S"T (15–23 diaspora, 15–22 Israel)
    if m == 7 and (d in (1, 2, 10) or 15 <= d <= (23 if diaspora else 22)):
        return True
    return False


def _pyluach_holiday(ph: PHebrewDate, diaspora: bool) -> str | None:
    """Return pyluach's holiday name with day-prefix if applicable,
    honoring Israel vs. diaspora differences (e.g. Sivan 7 is Shavuos
    Day 2 in diaspora but a regular day in Israel)."""
    israel = not diaspora
    h = None
    for attempt in (
        lambda: ph.holiday(hebrew=True, prefix_day=True, israel=israel),
        lambda: ph.holiday(hebrew=True, israel=israel),
        lambda: ph.holiday(hebrew=True, prefix_day=True),
        lambda: ph.holiday(hebrew=True),
    ):
        try:
            h = attempt()
            break
        except TypeError:
            continue
    if not h or h in _MODERN_HOLIDAYS:
        return None
    return h


def _parsha_name(greg_date: date_cls, diaspora: bool, metzora_display: str) -> str:
    """Return the Hebrew parsha name for that Shabbos, honoring
    the integration's Metzora override. Empty if no parsha (Yom Tov)."""
    greg = pl_dates.GregorianDate(greg_date.year, greg_date.month, greg_date.day)
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


def _special_shabbos(greg_date: date_cls, diaspora: bool) -> str:
    """Return the special-shabbos string, or ''."""
    try:
        raw = specials.get_special_shabbos_name(
            today=greg_date, is_in_israel=not diaspora
        )
    except TypeError:
        raw = specials.get_special_shabbos_name(today=greg_date)
    return raw or ""


def _erev_yt_label(greg_date: date_cls, diaspora: bool) -> str | None:
    """Return 'לערב <YT name>' if tomorrow is the first day of a major Yom Tov.

    Only fires for the day before Day 1 of: Pesach, Shavuos, Rosh Hashana,
    Yom Kippur, Sukkos. Erev Shmini Atzeres = Hoshana Rabbah, which keeps
    its specific name via the regular holiday_label path.
    Note: Erev Shevii shel Pesach (Nisan 20) is always a Chol HaMoed day,
    and we keep the CHM label there rather than overriding it.
    """
    tomorrow = greg_date + timedelta(days=1)
    ph = PHebrewDate.from_pydate(tomorrow)
    m, d = ph.month, ph.day
    if m == 1 and d == 15:
        return "לערב פסח"
    if m == 3 and d == 6:
        return "לערב שבועות"
    if m == 7 and d == 1:
        return "לערב ראש השנה"
    if m == 7 and d == 10:
        return "לערב יום כיפור"
    if m == 7 and d == 15:
        return "לערב סוכות"
    return None


def compute_day_label(
    greg_date: date_cls,
    *,
    diaspora: bool = True,
    metzora_display: str = "metzora",
    include_year: bool = False,
) -> str:
    """Build the Hebrew label for an arbitrary Gregorian date.

    Returns a string like 'לפורים', 'לשבת פרשת פנחס',
    'ליום ד׳ פרשת אחרי-קדושים', etc. Returns an empty string only if
    all lookups fail (extremely unlikely).

    If ``include_year`` is True, the Hebrew year (e.g. 'תשפ״ז') is
    appended to the label. The year used is the Hebrew year that
    contains ``greg_date`` — pyluach's standard convention, which rolls
    on Tishrei 1.
    """
    ph = PHebrewDate.from_pydate(greg_date)

    def _build() -> str:
        return _build_day_label_core(greg_date, ph, diaspora, metzora_display)

    label = _build()
    if include_year and label:
        try:
            year_str = ph.hebrew_year()
        except Exception:
            year_str = ""
        if year_str:
            label = f"{label} {year_str}"
    return label


def _build_day_label_core(
    greg_date: date_cls,
    ph: PHebrewDate,
    diaspora: bool,
    metzora_display: str,
) -> str:
    """Inner implementation of compute_day_label without the year suffix.
    Split out so the public function can append the year at one place."""
    m, d = ph.month, ph.day
    is_shabbos = (greg_date.weekday() == 5)

    # ── Targeted overrides that pyluach handles imperfectly ──
    is_hoshana_rabbah = (m == 7 and d == 21)
    is_shevii = (m == 1 and d == 21)
    is_achron = (m == 1 and d == 22 and diaspora)
    is_tu_bshvat = (m == 11 and d == 15)
    is_chm, chm_tag, chm_num = _chm_info(m, d, diaspora)

    # Resolve holiday_label (priority over parsha/weekday fallback)
    holiday_label: str | None = None
    if is_hoshana_rabbah:
        holiday_label = "הושענא רבה"
    elif is_shevii:
        holiday_label = "שביעי של פסח"
    elif is_achron:
        holiday_label = "אחרון של פסח"
    elif is_chm:
        holiday_label = f"{_DAY_LETTER[chm_num]} דחול המועד {chm_tag}"
    elif is_tu_bshvat:
        holiday_label = "ט״ו בשבט"
    else:
        pl_hol = _pyluach_holiday(ph, diaspora)
        if pl_hol:
            holiday_label = _reorder_prefix_day(pl_hol)

    # ── Shabbos branch ──
    if is_shabbos:
        if is_chm:
            # Shabbos Chol HaMoed — special wording; no parsha
            return f"לשבת חול המועד {chm_tag}"
        major_yt = _is_major_yt(ph, diaspora)
        if holiday_label and major_yt:
            # Major YT on Shabbos (e.g. Pesach Day 1 on Shabbos, YK on
            # Shabbos, Shmini Atzeres on Shabbos) — parsha is suppressed.
            return f"לשבת {holiday_label}"
        # Normal Shabbos OR minor holiday on Shabbos (Chanukah, Purim,
        # fasts, Tu B'Shvat, Lag BaOmer, Rosh Chodesh) — parsha keeps,
        # and the minor holiday / special-shabbos note is appended.
        parsha = _parsha_name(greg_date, diaspora, metzora_display)
        state = "לשבת"
        if parsha:
            state = f"לשבת פרשת {parsha}"
        suffixes: list[str] = []
        # Rosh Chodesh on Shabbos — append for parity with the weekday
        # branch's RC-as-suffix behavior.
        is_rc, rc_month = _is_rosh_chodesh_and_month(ph)
        if is_rc:
            suffixes.append(f"ראש חודש {rc_month}")
        if holiday_label and not major_yt:
            suffixes.append(holiday_label)
        special = _special_shabbos(greg_date, diaspora)
        if special:
            suffixes.append(special)
        if suffixes:
            state = f"{state} • {' • '.join(suffixes)}"
        return state

    # ── Weekday branch ──
    # Major YT on a weekday (Pesach Day 1 on Tuesday, RH on Monday, etc.)
    # → primary label, no parsha. Erev major YT also gets primary status.
    major_yt = _is_major_yt(ph, diaspora)
    if holiday_label and major_yt:
        return f"ל{holiday_label}"

    # Erev major-YT label — catches plain weekdays that are the day
    # before Pesach/Shavuos/RH/YK/Sukkos. Treated as primary (not
    # appended to a weekday) since "Erev <YT>" is the meaningful name
    # of the day.
    erev_lbl = _erev_yt_label(greg_date, diaspora)
    if erev_lbl:
        return erev_lbl

    # Anything else — minor holidays (Pesach Sheni, Lag BaOmer,
    # Tu B'Shvat, Tu B'Av, Chanukah, Purim, fasts), Rosh Chodesh, or
    # nothing — gets the weekday + parsha base, with the minor day(s)
    # appended after a • separator.
    weekday_heb = _WEEKDAY_HEB[greg_date.weekday()]
    offset = (5 - greg_date.weekday()) % 7
    if offset == 0:
        # Shouldn't happen (Shabbos handled above) but guard anyway.
        offset = 7
    shabbos = greg_date + timedelta(days=offset)
    parsha = _parsha_name(shabbos, diaspora, metzora_display)

    base = f"ל{weekday_heb}"
    if parsha:
        base = f"{base} פרשת {parsha}"

    suffixes: list[str] = []
    is_rc, rc_month = _is_rosh_chodesh_and_month(ph)
    if is_rc:
        suffixes.append(f"ראש חודש {rc_month}")
    if holiday_label and not major_yt:
        suffixes.append(holiday_label)

    if suffixes:
        return f"{base} • {' • '.join(suffixes)}"
    return base
