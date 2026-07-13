"""
custom_components/yidcal/yidcal_lib/luach_data.py

Thin orchestrator that assembles ``LuachRow`` + ``AnnotationRow``
records for the luach generator.

All halachic event/date logic lives in ``halacha_events`` (Tekufos,
Behab, Mevorchim, parsha, Pirkei Avos, etc.). All clock-time
computation lives in ``zman_compute`` (Erev/Motzei lighting events,
daily zmanim, Erev Pesach chametz times).

This module's job is layout-aware row assembly: turning the date
range into a sequence of "this is one printed row" / "this is one
text annotation" records. Renderers (PDF, future calendar entity,
etc.) take it from there.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls, datetime, timedelta
import re
from typing import Union
from zoneinfo import ZoneInfo

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.util.geo_location import GeoLocation

from . import halacha_events as he
from .luach_pdf_common import INFO_SEP
from .zman_compute import (
    round_half_up as _round_half_up,
    round_ceil as _round_ceil,
    sunset_for_date,
    sun_events_for_date,
    compute_zmanim_for_date,
    compute_chametz_zmanim,
    chatzos_halayla_for_night,
    sof_zman_kiddush_levana_rama_local,
    zayin_shleimim_local,
    fast_start_for_date,
    format_fast_start_clock,
    FAST_START_ALOS,
    FAST_START_SHKIA,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)


# ────────────────────────────────────────────────────────────────────────
# SF-style compact-label helpers (Erev-YT abbreviations)
# ────────────────────────────────────────────────────────────────────────

def _sf_compactify_yt_name(yt: str, compact: bool) -> str:
    """SF-style abbreviations applied to a YT name BEFORE the 'ערב '
    prefix is attached.

    Currently:
        'יום כיפור' → 'יו״כ'
    Other YT names (notably 'ראש השנה' → 'ר״ה') are already abbreviated
    inline at each call site via ``yt.replace`` and are left unchanged
    by this helper.

    No-op when ``compact`` is False.
    """
    if not compact:
        return yt
    return yt.replace("יום כיפור", "יו״כ")


def _sf_compactify_erev_label(label: str, compact: bool) -> str:
    """SF-style merge of the 'ערב' prefix into compound erev labels.

    Currently:
        'ערב ר״ה <year>' → 'ער״ה <year>'

    No-op when ``compact`` is False.
    """
    if not compact:
        return label
    if label.startswith("ערב ר״ה"):
        return "ער״ה" + label[len("ערב ר״ה"):]
    return label


def _sf_abbrev_mevorchim_parsha(text: str, compact: bool) -> str:
    """SF-style double-parsha abbreviation applied to Mevorchim-line
    text (both the RC clause and the molad clause).

    Currently only:
        'תזריע-מצורע' → 'תז״מ'

    Substitution is done with ``replace`` so it correctly handles
    variants that may appear with the SF ``א׳`` mon/thu-krias suffix
    (e.g. 'תזריע-מצורע א׳' → 'תז״מ א׳'). No-op when ``compact`` is
    False or when the substring is absent.
    """
    if not compact:
        return text
    return text.replace("תזריע-מצורע", "תז״מ")


# ────────────────────────────────────────────────────────────────────────
# Row + Annotation data classes
# ────────────────────────────────────────────────────────────────────────

@dataclass
class LuachRow:
    """A single candle-lighting row in the luach."""
    civil_date: date_cls           # the anchor (Erev) date — for chronological sort
    hebrew_date_he: str            # the Hebrew date for the Erev's daytime
    title_he: str                  # row title: parsha or 'ערב <YT>' etc. (combined main+suffix)
    # Two-tier title (matches the SF luach's typography, where the
    # parsha / YT name is rendered larger + bolder than the day-of-week
    # suffix). ``title_main_he`` is the visually-prominent label
    # (e.g., "נח", "ערב סוכות", "ב׳ דראש השנה"). ``title_suffix_he``
    # is the smaller trailing portion (e.g., "יום ב׳", "ערב שב״ק")
    #
    # ``*_sheet_he`` are OPTIONAL yearly-SINGLE-SHEET wordings for the
    # same row. The SF sheet abbreviates the Erev prefixes where the
    # weekly card wants them spelled out, and the sheet's title cell
    # is only 41 mm wide. Every other consumer (weekly card, multi-page
    # yearly) reads the plain fields and is untouched.
    # or empty when the title is a bare parsha name.
    # ``title_he`` is kept as ``f"{title_main_he} {title_suffix_he}".strip()``
    # for any renderer that wants the combined string.
    title_main_he: str = ""
    title_main_sheet_he: str = ""
    title_suffix_sheet_he: str = ""
    title_suffix_he: str = ""
    pirkei_avos_he: str = ""       # 'פרק א׳' / 'פרק ה׳-ו׳' / ''
    special_shabbos_he: list[str] = field(default_factory=list)  # ['שבת ר״ח', ...]
    eruv_tavshilin: bool = False   # True when this Erev needs (עירוב תבשילין)
    candle_lighting: datetime | None = None
    candle_kind: str = "none"      # 'erev_before_sunset' / 'between_yt' / 'motzaei_sh_to_yt'
    motzei: datetime | None = None
    motzei_label_he: str = ""      # 'מוצאי שב״ק' / 'מוצאי יו״ט' / ''
    zmanim: dict[str, datetime] = field(default_factory=dict)  # alos, sof_zman_shma_mga, ...
    omer_day: int = 0              # 1..49, or 0 if not in omer period


@dataclass
class AnnotationRow:
    """An interleaved Hebrew text row (Mevorchim, Tekufah, fast times, etc.)."""
    civil_date: date_cls           # anchor for chronological positioning
    kind: str                      # 'mevorchim' / 'tekufah' / 'erev_pesach_chametz' / etc.
    text_he: str                   # the formatted Hebrew text line
    position: str = "before"       # 'before' or 'after' the row at civil_date
    # Optional yearly-SHEET wording for the same annotation (the SF
    # sheet abbreviates and breaks differently from the weekly card).
    # '\n' separates visual lines. The weekly reads text_he ONLY, so
    # anything that lives here never reaches the weekly card.
    text_sheet_he: str = ""


LuachItem = Union[LuachRow, AnnotationRow]


# ────────────────────────────────────────────────────────────────────────
# Candle-lighting event helper (mirrors zman_sensors.lighting_event_for_day)
# ────────────────────────────────────────────────────────────────────────
# This is clock-time logic, so it logically belongs in zman_compute. It's
# currently duplicated from zman_sensors.py to keep luach_data import-clean
# (no circular risk with the live sensor module). When the sensor
# migration happens, this moves into zman_compute and zman_sensors imports
# it.

def _lighting_event_for_day(
    d: date_cls,
    *,
    diaspora: bool,
    tz: ZoneInfo,
    geo: GeoLocation,
    candle_offset: int,
    havdalah_offset: int,
) -> tuple[datetime | None, str]:
    """Return (datetime, kind) of any candle-lighting event on civil
    day ``d``."""
    hd_today = HDateInfo(d, diaspora=diaspora)
    hd_tom = HDateInfo(d + timedelta(days=1), diaspora=diaspora)

    is_shabbos_today = (d.weekday() == 5)
    is_shabbos_tom = ((d + timedelta(days=1)).weekday() == 5)
    is_yt_today = hd_today.is_yom_tov
    is_yt_tom = hd_tom.is_yom_tov

    sunset = sunset_for_date(geo=geo, tz=tz, base_date=d)

    if is_shabbos_tom:
        return (sunset - timedelta(minutes=candle_offset), "erev_before_sunset")

    if is_yt_tom:
        if is_shabbos_today:
            return (sunset + timedelta(minutes=havdalah_offset),
                    "motzaei_shabbos_after_tzeis")
        if is_yt_today:
            return (sunset + timedelta(minutes=havdalah_offset),
                    "between_yt_after_tzeis")
        return (sunset - timedelta(minutes=candle_offset), "erev_before_sunset")
    return (None, "none")


# Fast-start flooring (alos / shkia) now lives in the single source of
# truth: zman_compute.fast_start_for_date / format_fast_start_clock.
# The previously-local _floor_clock_for_alos / _floor_clock_for_shkia
# helpers were removed so the floor-lechumra policy has exactly one
# definition shared with the holiday sensor and any future fast code.


# ────────────────────────────────────────────────────────────────────────
# Row title builder (luach-specific layout concern)
# ────────────────────────────────────────────────────────────────────────

def _is_chol_hamoed(ph, *, diaspora: bool) -> bool:
    """Canonical Chol-HaMoed rule (halacha_events), Hoshana Rabbah included."""
    return he.chol_hamoed_day(ph.month, ph.day, diaspora=diaspora) is not None


# The footnote the SF sheet prints at the very bottom whenever the
# pruzbol note appears. Exact text supplied by Yoel.
PRUZBOL_FOOTNOTE_HE = (
    "*נוסח הפרוזבול: במותב תלתא כחדא הוינא ואתא פלוני המלוה ואמר "
    "לפנינו: מוסרני לכם פלוני ופלוני הדיינים שבמקום פלוני שכל חוב "
    "שיש לי שאגבנו כל זמן שארצה (הדיינים צריכים לחתום)"
)


def _build_pruzbol_annotations(
    *, start: date_cls, end: date_cls,
) -> list[AnnotationRow]:
    """Erev-RH pruzbol note, when that Erev RH closes a shmita year.

    Printed SF 5783: 'בער״ה תשפ״ג צריכין לעשות פרוזבול*' — with the
    נוסח as a footnote at the foot of the sheet. The predicate lives
    in halacha_events.needs_pruzbol() so a future
    binary_sensor.yidcal_pruzbol reads the SAME rule.
    """
    out: list[AnnotationRow] = []
    d = start
    while d <= end:
        if he.needs_pruzbol(d):
            ph = PHebrewDate.from_pydate(d)
            try:
                yl = he.hebrew_year_letters(ph.year + 1)
            except Exception:
                yl = ""
            out.append(AnnotationRow(
                civil_date=d,
                kind="pruzbol",
                text_he=f"בער״ה {yl} צריכין לעשות פרוזבול*".strip(),
                position="before",
            ))
        d += timedelta(days=1)
    return out


def _pesach_chatzos_str(erev_pesach, config) -> str:
    """Chatzos halayla of the FIRST seder night.

    Uses the same ``chatzos_halayla_for_night`` (MGA midpoint) as the
    weekday Erev-Pesach branch and YidCal's ChatzosHaLaila sensor, so
    the luach text and the sensor never disagree."""
    ch = chatzos_halayla_for_night(
        geo=config.geo, tz=config.tz, base_date=erev_pesach,
    )
    return _weekly_fmt_time(ch, config.time_format)


def _build_row_title(
    *,
    anchor: date_cls,
    kind: str,
    diaspora: bool,
    metzora_display: str,
    compact_erev_yt_labels: bool = False,
) -> tuple[str, str]:
    """Build the row's title as a (main, suffix) tuple.

    ``main`` is the visually-prominent label (parsha name or YT name)
    that the yearly-sheet luach renders BOLD and LARGER. ``suffix`` is the
    smaller trailing portion such as the weekday designation
    ("יום ב׳") or an "ערב שב״ק" marker. ``suffix`` is the empty
    string when the row's title is a bare parsha name.

    Combined as ``f"{main} {suffix}".strip()``, this also reproduces
    the single-string title the yearly-multi-page luach renderer expects.
    """
    next_day = anchor + timedelta(days=1)
    is_shabbos_tom = next_day.weekday() == 5
    is_yt_tom = HDateInfo(next_day, diaspora=diaspora).is_yom_tov
    is_yt_today = HDateInfo(anchor, diaspora=diaspora).is_yom_tov
    is_shabbos_today = anchor.weekday() == 5

    ph_today = PHebrewDate.from_pydate(anchor)
    ph_next = PHebrewDate.from_pydate(next_day)

    # Regular Shabbos (parsha row) — or Erev Shabbos chol hamoed when
    # both today and tomorrow are chol hamoed.
    if (
        kind == "erev_before_sunset"
        and not is_yt_today
        and is_shabbos_tom
        and not is_yt_tom
    ):
        # Erev Shabbos chol hamoed (Sukkos): today and tomorrow are
        # both chol hamoed. parsha_name returns empty during chol
        # hamoed, so emit the MSM-style label instead.
        if _is_chol_hamoed(ph_today, diaspora=diaspora):
            return ("ערב שבת חוה״מ", "")
        return (
            he.parsha_name(
                next_day, diaspora=diaspora, metzora_display=metzora_display,
            ),
            "",
        )

    # Erev YT on weekday (no Shabbos involvement)
    if (
        kind == "erev_before_sunset"
        and not is_yt_today
        and not is_shabbos_today
        and is_yt_tom
        and not is_shabbos_tom
    ):
        weekday = he.HE_WEEKDAY.get(anchor.weekday(), "")
        # Hoshana Rabba special case: 21 Tishrei is the eve of Shemini
        # Atzeret, but MSM labels it 'הושענא רבה' rather than
        # 'ערב שמיני עצרת'.
        if ph_today.month == 7 and ph_today.day == 21:
            return ("הושענא רבה", weekday) if weekday else ("הושענא רבה", "")
        yt = he.major_yt_name(ph_next, diaspora=diaspora) or ""
        yt = yt.replace("שביעי של פסח", "שביעי ש״פ")
        # Erev RH: abbreviate to ר״ה and append the new Hebrew year, to
        # match MSM's 'ערב ר״ה תשפ״ז'-style label (and to be consistent
        # with the same handling in the Erev-RH-on-Shabbos branch below).
        yt = yt.replace("ראש השנה", "ר״ה")
        # SF-style compact YT abbreviations (e.g. 'יום כיפור' → 'יו״כ')
        yt = _sf_compactify_yt_name(yt, compact_erev_yt_labels)
        if "ר״ה" in yt:
            try:
                yt = f"{yt} {he.hebrew_year_letters(ph_next.year)}"
            except Exception:
                pass
        if yt:
            label = _sf_compactify_erev_label(f"ערב {yt}", compact_erev_yt_labels)
            return (label, weekday)
        return ("", weekday)

    # Erev where tomorrow is BOTH Shabbos AND YT (e.g. RH on Shabbos)
    if (
        kind == "erev_before_sunset"
        and not is_yt_today
        and not is_shabbos_today
        and is_shabbos_tom
        and is_yt_tom
    ):
        # Hoshana Rabba again — 21 Tishrei is 'הושענא רבה', never
        # 'ערב שמיני עצרת', even when שמיני עצרת lands on Shabbos
        # (5784: printed 'הושענא רבה עש״ק, כ״א תשרי'). The same
        # special case above only covered the weekday branch.
        if ph_today.month == 7 and ph_today.day == 21:
            return ("הושענא רבה", "ערב שב״ק")
        yt = he.major_yt_name(ph_next, diaspora=diaspora) or ""
        yt = yt.replace("שביעי של פסח", "שביעי ש״פ")
        yt = yt.replace("ראש השנה", "ר״ה")
        yt = _sf_compactify_yt_name(yt, compact_erev_yt_labels)
        if "ר״ה" in yt:
            try:
                yt = f"{yt} {he.hebrew_year_letters(ph_next.year)}"
            except Exception:
                pass
        if yt:
            label = _sf_compactify_erev_label(f"ערב {yt}", compact_erev_yt_labels)
            return (label, "ערב שב״ק")
        return ("ערב שב״ק", "")

    # In-block candle (today is YT or Shabbos)
    if is_yt_today or is_shabbos_today:
        today_lbl = (
            he.intra_block_day_label(ph_today, diaspora=diaspora) or "יום טוב"
            if is_yt_today
            else "שבת"
        )
        if is_shabbos_tom and is_yt_tom:
            # SF convention: when tomorrow is BOTH Shabbos AND YT,
            # the YT name is rendered at the SAME bold/big size as
            # "ערב שבת" — they form one continuous prominent label.
            # So we merge them into ``title_main_he`` (no suffix).
            #
            # Example: Shavuos Day 1 on Friday, Day 2 on Shabbos →
            # 'ערב שבת שבועות' (one bold-big label).
            # Same applies to Day 2 of Pesach / Sukkos / RH falling
            # on Shabbos. The day-of-YT designation is omitted —
            # "ערב שבת" + calendar context conveys the day.
            yt_name = he.major_yt_name(ph_next, diaspora=diaspora) or ""
            yt_name = yt_name.replace("שביעי של פסח", "שביעי ש״פ")
            yt_name = yt_name.replace("ראש השנה", "ר״ה")
            yt_name = _sf_compactify_yt_name(yt_name, compact_erev_yt_labels)
            if not yt_name:
                yt_name = he.intra_block_day_label(
                    ph_next, diaspora=diaspora,
                ) or today_lbl
            if yt_name:
                return (f"ערב שבת {yt_name}", "")
            return ("ערב שבת", "")
        if is_shabbos_tom:
            if _is_chol_hamoed(ph_next, diaspora=diaspora):
                return ("ערב שבת חוה״מ", today_lbl)
            return ("ערב שבת", today_lbl)
        if is_yt_tom and not is_shabbos_tom:
            tom_lbl = he.intra_block_day_label(ph_next, diaspora=diaspora) or ""
            if tom_lbl:
                return (f"ערב {tom_lbl}", "")
            return (today_lbl, "")

    return (he.hebrew_date_str(anchor), "")


# ────────────────────────────────────────────────────────────────────────
# LuachConfig and the public entry point
# ────────────────────────────────────────────────────────────────────────

@dataclass
class LuachConfig:
    """Inputs for building luach data."""
    geo: GeoLocation
    tz: ZoneInfo
    diaspora: bool
    candle_offset: int
    havdalah_offset: int
    tallis_offset: int = DEFAULT_TALLIS_TEFILIN_OFFSET
    metzora_display: str = "metzora"
    # Clock format for the WEEKLY card's zmanim strings ("12"/"24").
    # "12" (default) matches every printed luach; the annotation
    # composers below also consult this, but only entry points that
    # explicitly set "24" (the weekly service) change behaviour.
    time_format: str = "12"
    extra_zmanim_labels: tuple[str, ...] = ("עלות השחר", "סוף זמן קריאת שמע מג״א")
    include_pirkei_avos: bool = True
    # Molad phrasing style: "monroe" (default — used by the yearly-multi-page luach,
    # matches the Monroe/KJ printed convention with the time-of-day word
    # before the H:MM) or "sf" (used by the yearly-sheet luach, matches the
    # South-Fallsburg printed convention with a ``בשעה`` prefix and the
    # time-of-day word at the end of the line).
    molad_style: str = "monroe"
    # Hebrew-date Rosh Chodesh emphasis: when True (default — matches
    # the Monroe/KJ convention), days that are RC render specially as
    # "א׳ דר״ח <next-month>" or "ב׳ דר״ח <month>" or "ר״ח <month>".
    # When False (South-Fallsburg convention used by the yearly-sheet luach),
    # dates are always plain Hebrew letters — e.g. ``ל׳ ניסן`` instead
    # of ``א׳ דר״ח אייר`` for 30 Nissan; the RC info is conveyed via
    # the row's special-Shabbos tag (``שבת ר״ח``) on the Erev row.
    hebrew_date_rc_emphasis: bool = True
    # SF-style compact abbreviations for Erev-YT row titles. When True
    # (yearly-sheet luach), apply these merges:
    #     "ערב ר״ה תשפ״ו"   → "ער״ה תשפ״ו"
    #     "ערב יום כיפור"    → "ערב יו״כ"
    # Default False preserves the Monroe/weekly convention which writes
    # both prefixes in full.
    compact_erev_yt_labels: bool = False
    # SF-style compact abbreviations for double-parsha names that
    # appear in the Mevorchim annotation line (both the RC clause and
    # the molad clause). When True (yearly-sheet luach), apply:
    #     "תזריע-מצורע" → "תז״מ"
    # Only affects the Mevorchim line — the parsha row itself
    # continues to print the full name (matching SF). Default False
    # preserves the existing weekly behavior.
    compact_mevorchim_parsha: bool = False
    # When True (yearly-sheet luach), suppress the "חזק" special-Shabbos
    # marker on row titles for the parshas where it's traditionally
    # said (last parsha of each Chumash). SF doesn't print "חזק" in
    # its yearly layout. Other special-Shabbos markers — including the
    # arba parshiyos (שקלים/זכור/פרה/החודש), שובה, נחמו, חזון, הגדול,
    # etc. — are unaffected and continue to render normally. Sensor
    # output is also unaffected (this flag only filters at the row-
    # building step in the luach layout). Default False preserves the
    # existing weekly behavior.
    omit_chazak: bool = False
    # Yearly-sheet-only Tisha B'Av format: one chronological block
    # instead of the Monroe two-line form (avoids repeating the fast's
    # name on consecutive lines). Rendered as two centered lines only
    # because the fully merged text measures ~139 mm at the 8 pt
    # annotation size vs the sheet's 95 mm column:
    #     <label> <wd> <parsha> - התחלת זמן התענית ערב ת״ב H:MM
    #     חצות H:MM • מנחה גדולה H:MM • מנחה קטנה H:MM • צאה״כ מוצאי ת״ב H:MM
    # The default (Monroe multi-page / weekly) format is unchanged and
    # keeps the printed-luach spelling ערב תשעה באב in full.
    tisha_bav_single_line: bool = False


def build_luach(
    *,
    start_date: date_cls,
    end_date: date_cls,
    config: LuachConfig,
    molad_provider=None,
) -> list[LuachItem]:
    """Build the ordered list of LuachRow + AnnotationRow items for
    the given civil date range.

    ``molad_provider`` is an optional callable that takes a civil date
    and returns a ``Molad`` object (from yidcal_lib.helper.Molad).
    When ``None``, Mevorchim rows omit the Molad text.
    """
    rows = _build_rows(start_date, end_date, config=config)
    annotations = _build_annotations(
        start_date, end_date, config=config,
        molad_provider=molad_provider, rows=rows,
    )
    return _merge_in_order(rows, annotations)


# ────────────────────────────────────────────────────────────────────────
# Row assembly
# ────────────────────────────────────────────────────────────────────────

def _build_rows(
    start: date_cls,
    end: date_cls,
    *,
    config: LuachConfig,
) -> list[LuachRow]:
    """Walk day-by-day; emit one LuachRow per Erev-Shabbos / Erev-YT
    candle-lighting event."""
    out: list[LuachRow] = []
    behab_cycles = he.compute_behab_in_range(start=start, end=end)

    d = start
    while d <= end:
        dt_event, kind = _lighting_event_for_day(
            d,
            diaspora=config.diaspora,
            tz=config.tz,
            geo=config.geo,
            candle_offset=config.candle_offset,
            havdalah_offset=config.havdalah_offset,
        )
        if dt_event is None or kind != "erev_before_sunset":
            d += timedelta(days=1)
            continue

        candle = _round_half_up(dt_event)

        title_main, title_suffix = _build_row_title(
            anchor=d, kind=kind,
            diaspora=config.diaspora,
            metzora_display=config.metzora_display,
            compact_erev_yt_labels=config.compact_erev_yt_labels,
        )
        # ── Friday-is-a-YT-day: name the row for its SHABBOS ──
        # (printed SF convention, verified 5783/5784/5785). The
        # Friday's own YT becomes a parenthetical, appended below to
        # the row's special-Shabbos list so the sheet prints it AFTER
        # the Hebrew date.
        _fri_yt_paren = ""
        # Shabbos-is-Erev-Pesach: the printed sheet has NO row for that
        # Saturday — it tags the Friday row instead (5785: 'צו שבת
        # הגדול, י״ג ניסן (שבת ער״פ)').
        if kind == "erev_before_sunset" and d.weekday() == 4:
            try:
                _sat_yt = he.erev_yt_name(
                    d + timedelta(days=1), diaspora=config.diaspora)
            except Exception:
                _sat_yt = None
            if not _sat_yt:
                # erev_yt_name() excludes no-melacha days, so probe the
                # Hebrew date directly (only פסח/שבועות can start Sun).
                try:
                    _tp = PHebrewDate.from_pydate(d + timedelta(days=2))
                    if (_tp.month, _tp.day) == (1, 15):
                        _sat_yt = "פסח"
                    elif (_tp.month, _tp.day) == (3, 6):
                        _sat_yt = "שבועות"
                except Exception:
                    _sat_yt = None
            if _sat_yt:
                _abbr = {"פסח": "ער״פ"}.get(_sat_yt, f"ערב {_sat_yt}")
                _fri_yt_paren = f"(שבת {_abbr})"

        if (not _fri_yt_paren
                and kind == "erev_before_sunset" and d.weekday() == 4
                and title_main.startswith("ערב שבת")):
            try:
                _sat = d + timedelta(days=1)
                _ph_today = PHebrewDate.from_pydate(d)
                _yt_lbl = he.intra_block_day_label(
                    _ph_today, diaspora=config.diaspora) or ""
                if _yt_lbl and HDateInfo(
                        d, diaspora=config.diaspora).is_yom_tov:
                    _p = he.parsha_name(
                        _sat, diaspora=config.diaspora,
                        metzora_display=config.metzora_display,
                    )
                    # ONLY when the Shabbos has a real parsha. A
                    # חוה״מ Shabbos keeps the tight 5785 form
                    # ('ערב שבת חוה״מ ב׳ דסוכות') — note the 5783
                    # sheet parenthesises that one instead; the
                    # newer 5785 sheet is the reference.
                    if _p:
                        title_main, title_suffix = _p, ""
                        _fri_yt_paren = f"({_yt_lbl})"
            except Exception:
                _fri_yt_paren = ""

        # ── SF SHEET wording (verified vs the printed 5783/5784/5785) ──
        #   • a FRIDAY Erev row's suffix is 'עש״ק', never 'ערב שב״ק'
        #     (ער״ה תשפ״ד עש״ק · ערב סוכות עש״ק · הושענא רבה עש״ק)
        #   • Erev-Shvi'i-of-Pesach reads 'ע׳ שביעי של פסח' — the YT
        #     name spelled OUT and the ערב abbreviated. Ours is the
        #     other way round ('ערב שביעי ש״פ') and overran the cell.
        _sheet_main = ""
        _sheet_suffix = ""
        if title_suffix == "ערב שב״ק":
            _sheet_suffix = "עש״ק"
        if title_main.startswith("ערב שביעי ש״פ"):
            _sheet_main = title_main.replace(
                "ערב שביעי ש״פ", "ע׳ שביעי של פסח", 1)

        title = f"{title_main} {title_suffix}".strip()
        hebrew_date = he.hebrew_date_str(d, rc_emphasis=config.hebrew_date_rc_emphasis)

        is_shabbos_tom = (d + timedelta(days=1)).weekday() == 5
        special_he: list[str] = []
        if is_shabbos_tom:
            saturday = d + timedelta(days=1)
            # Pull all special-Shabbos labels EXCEPT 'מברכים חודש X' —
            # Mevorchim is rendered as its own annotation line above the
            # row, so showing it again in the row title is redundant and
            # crowds the row on busy weeks.
            all_labels = he.special_shabbos_labels(saturday, diaspora=config.diaspora)
            # SF-style: suppress the "חזק" marker from row titles. The
            # sensor still emits "שבת חזק" — this only affects the
            # luach row layout.
            if config.omit_chazak:
                all_labels = [lbl for lbl in all_labels if lbl != "שבת חזק"]
            # Strip the "שבת " prefix from most labels — the luach format
            # shows just the qualifier (e.g. "(חזון)" not "(שבת חזון)")
            # since the parsha column already implies it's Shabbos.
            # Exceptions:
            #   • "שבת ראש חודש" → "שבת ר״ח" (with 2-day-RC position
            #     qualifier when applicable, e.g. "שבת א׳ דר״ח")
            #   • "שבת הגדול" stays in full per MSM convention (the
            #     dedicated מבה״ח-style emphasis warrants the full label)
            def _format_special(lbl: str) -> str:
                if lbl == "שבת ראש חודש":
                    return he.shabbos_rc_label_he(saturday)
                if lbl == "שבת הגדול":
                    return lbl
                return lbl.removeprefix("שבת ")
            special_he = [
                _format_special(lbl) for lbl in all_labels
                if not lbl.startswith("מברכים חודש")
            ]
            # SF convention: when the ROW's Friday (= the candle-lighting
            # day) is the SECOND day of a 2-day Rosh Chodesh — and the
            # Saturday is therefore NOT a RC day — SF still emits an
            # "ב׳ דר״ח" tag on the parsha row. Example (5786 Elul):
            #     שופטים, א׳ אלול, ב׳ דר״ח, פרק ו׳
            # Here 30 Av + 1 Elul are a 2-day RC (Thu + Fri); Shabbos
            # (2 Elul) isn't RC, but Friday is, so SF flags it. When
            # Saturday IS the RC day (covered above by special_shabbos_
            # labels → shabbos_rc_label_he) we leave that label alone.
            #
            # We deliberately DON'T emit a label for the 1-day RC on
            # Friday case (e.g. Toldot 5786 with 1 Kislev on Friday):
            # SF doesn't tag those rows — the Hebrew date column
            # already conveys it ("א׳ כסלו").
            saturday_already_rc = any(
                s.startswith("שבת ר") or s.startswith("שבת א")
                for s in special_he
            )
            if not saturday_already_rc:
                fr_pos = he.rc_day_position_for_date(d)
                if fr_pos is not None:
                    pos, total = fr_pos
                    if total == 2 and pos == 2:
                        special_he.append("ב׳ דר״ח")
            # Day-of-Chanukah label uses the ROW's own civil date
            # (e.g. on Erev Miketz Friday 29 Kislev → "ה׳ דחנוכה",
            # even though Shabbos itself is 30 Kislev = day 6). SF
            # places this label adjacent to the parsha (before the
            # date), so we prepend it to special_he. Only added when
            # the row's date is actually within Chanukah — which
            # filters out the cases where the row is the Erev but
            # the Shabbos is the very first day of Chanukah (25
            # Kislev): on that Erev row the Friday is still 24
            # Kislev, pre-Chanukah, and no label is added — matching
            # the SF convention of labeling by the row's date.
            chanukah_lbl = he.chanukah_day_label_he(d)
            if chanukah_lbl is not None:
                special_he.insert(0, chanukah_lbl)
            if any(c.mevorchim_shabbos == saturday for c in behab_cycles):
                special_he.append("מברכין בה״ב")

        # א״א שהחיינו marker — halachic reminder that Shehecheyanu is
        # NOT said on the upcoming Yom Tov. The day check lives in
        # ``halacha_events.is_yt_without_shehecheyanu`` so it can be
        # reused by sensors (e.g. an Erev / candle-lighting sensor
        # that exposes ``Shehecheyanu_Tomorrow`` as an attribute).
        #
        # SF places this label AFTER the Hebrew date (e.g. "ערב שביעי
        # של פסח, יום ג׳ כ׳ ניסן, א״א שהחיינו"). The renderer's
        # ``_is_loose_special`` predicate treats it as a LOOSE-bucket
        # label so it sorts in the right position.
        if he.is_yt_without_shehecheyanu(
            d + timedelta(days=1), diaspora=config.diaspora,
        ):
            special_he.append("א״א שהחיינו")

        pirkei = ""
        if config.include_pirkei_avos and is_shabbos_tom:
            pirkei = he.pirkei_avos_for_shabbos(
                d + timedelta(days=1), diaspora=config.diaspora,
            )

        eruv_tav = False
        if kind == "erev_before_sunset":
            # Eruv tavshilin is made ONCE, on the actual Erev YT (the day
            # before the no-melacha block starts). If today is already
            # inside the block (today is YT or Shabbos), the eruv has
            # already been made — don't mark it again.
            is_yt_today = HDateInfo(d, diaspora=config.diaspora).is_yom_tov
            is_shabbos_today = d.weekday() == 5
            if not is_yt_today and not is_shabbos_today:
                block = he.no_melacha_block(
                    d + timedelta(days=1), diaspora=config.diaspora,
                )
                if block is not None:
                    start_blk, end_blk = block
                    if end_blk.weekday() == 5 and start_blk != end_blk:
                        eruv_tav = True

        # B1 convention: alos / sof zman krias shma columns are
        # computed for the row's OWN civil date (``d``), so that each
        # row matches the KJ printed luach on the same date when
        # cross-referenced. (Motzei is built separately and stays as
        # the Shabbos/YT-day's sunset+72, since there's no motzei on
        # an Erev day.)
        all_zmanim = compute_zmanim_for_date(
            geo=config.geo, tz=config.tz, base_date=d,
            tallis_offset=config.tallis_offset,
            havdalah_offset=config.havdalah_offset,
        )
        z_map: dict[str, datetime] = {}
        for entry in all_zmanim:
            if entry.label in config.extra_zmanim_labels:
                z_map[entry.label] = entry.dt_local

        omer = he.omer_day_for(d + timedelta(days=1))

        out.append(LuachRow(
            civil_date=d,
            hebrew_date_he=hebrew_date,
            title_he=title,
            title_main_he=title_main,
            title_suffix_he=title_suffix,
            title_main_sheet_he=_sheet_main,
            title_suffix_sheet_he=_sheet_suffix,
            pirkei_avos_he=pirkei,
            special_shabbos_he=(
                special_he + [_fri_yt_paren] if _fri_yt_paren
                else special_he
            ),
            eruv_tavshilin=eruv_tav,
            candle_lighting=candle,
            candle_kind=kind,
            zmanim=z_map,
            omer_day=omer,
        ))

        d += timedelta(days=1)

    _insert_shabbos_to_yt_rows(out, start, end, config=config)
    _attach_motzei(out, config=config, end_date=end)
    return out


def _attach_motzei(
    rows: list[LuachRow], *, config: LuachConfig,
    end_date: date_cls | None = None,
) -> list[LuachRow]:
    """Attach motzei times following the MSM/KJ printed-luach convention:

    **Rule 1** — Every Erev row's motzei = tzeis of the appropriate day
    in its no-melacha block. The "appropriate day" depends on whether
    we'll be emitting a trailing row (Rule 2) for the block:
      • **Single-day block** (``start_blk == end_blk``) — one day, so
        motzei = that day's tzeis.
      • **Multi-day Tishrei block** (RH / Sukkos / Shmini Atzeres) —
        we DO emit a trailing row that carries the end-of-block
        motzei. So this Erev row shows ``start_blk``'s tzeis (the
        "between-YT-days" transition time — useful for re-lighting
        candles from an existing flame the next evening).
      • **Multi-day non-Tishrei block** (Pesach / Shvi'i shel Pesach /
        Shavuos) — we do NOT emit a trailing row (MSM convention).
        So this Erev row carries ``end_blk``'s tzeis: when havdalah
        is actually said. Per MSM principle, the Erev line shows
        candle-lighting from the start and motzei from the end of
        the section.

    **Rule 2** — For multi-day blocks (``start_blk != end_blk``), emit a
    separate trailing row at ``end_blk`` with title + block-end motzei
    (no candle, no Hebrew date), unless ``end_blk`` is already covered
    by another row in the list (an Erev row whose civil_date == end_blk,
    or an inserted ``motzaei_sh_to_yt`` row from
    ``_insert_shabbos_to_yt_rows``). Per MSM, trailing rows are only
    emitted for Tishrei blocks.

    Non-Erev rows (trailing / motzaei_sh_to_yt) carry pre-set motzei
    values from their respective inserters; this function leaves them
    untouched.
    """
    extra_rows: list[LuachRow] = []
    trailing_dates_emitted: set[date_cls] = set()

    for row in list(rows):  # iterate snapshot — we may extend rows
        if row.candle_kind != "erev_before_sunset":
            continue

        target_day = row.civil_date + timedelta(days=1)
        block = he.no_melacha_block(target_day, diaspora=config.diaspora)
        if block is None:
            continue
        start_blk, end_blk = block

        # ── Rule 1: which day's tzeis does this Erev row print? ──
        # (verified row-by-row against the printed SF 5786 yearly sheet)
        #   • Block runs past the sheet's end_date → print nothing
        #     (SF leaves motzei blank on the trailing Erev-RH row).
        #   • Entered day IS the block's last day → its own tzeis
        #     (real havdalah / motzaei Shabbos, e.g. ערב שבת חוה"מ).
        #   • Entered day is a Friday mid-block → print nothing: that
        #     day ends with PRE-SUNSET Shabbos lighting carried by the
        #     next Erev row (SF 5786 leaves ערב שבועות blank).
        #   • Otherwise (day ends at tzeis, block continues): show the
        #     between-days tzeis IF a later row carries the block-end
        #     motzei (a Tishrei trailing row per Rule 2, or an Erev-
        #     Shabbos row on a Friday inside the block — e.g. first
        #     days of Pesach 5786 print 8:38, not the block-end 8:40);
        #     when this row is the block's ONLY motzei carrier (mid-
        #     week 2-day YT like last days of Pesach), print the
        #     block-end havdalah instead.
        # Does the block END need its OWN row? The מוצאי-ש״ק column of
        # a FRIDAY Erev row is committed to that Shabbos's tzeis, so it
        # can never also carry a block end that falls later. Verified
        # against the printed SF sheets 5783/5784/5785 — all 13 YT
        # blocks:
        #   • end falls on Shabbos    → the Friday Erev row carries it
        #                               → no trailing row (ר״ה/סוכות/
        #                                 שמח״ת 5785, פסח 5783)
        #   • Tishrei block           → MSM always breaks the end out
        #                               → trailing row (5783 mid-week
        #                                 ר״ה, 5784 Sat+Sun blocks)
        #   • Friday Erev row (else)  → it cannot carry the end
        #                               → trailing row (פסח + שביעי
        #                                 של פסח 5785)
        #   • weekday Erev, non-Tishrei → the Erev row carries the end
        #                               → no trailing row (פסח 5784)
        _blk_needs_trailing = (
            start_blk != end_blk
            and end_blk.weekday() != 5
            and (PHebrewDate.from_pydate(end_blk).month == 7
                 or row.civil_date.weekday() == 4)
        )

        if end_date is not None and end_blk > end_date:
            target_blk = None
        elif target_day == end_blk:
            target_blk = target_day
        elif target_day.weekday() == 4:
            target_blk = None
        else:
            has_trailing = _blk_needs_trailing
            has_friday_carrier = any(
                (target_day + timedelta(days=i)).weekday() == 4
                for i in range(1, (end_blk - target_day).days)
            )
            target_blk = (
                target_day if (has_trailing or has_friday_carrier)
                else end_blk
            )

        if target_blk is None:
            row.motzei = None
            row.motzei_label_he = ""
        else:
            sunset_target = sunset_for_date(geo=config.geo, tz=config.tz, base_date=target_blk)
            immediate_motzei = _round_ceil(
                sunset_target + timedelta(minutes=config.havdalah_offset)
            )
            target_is_yt = HDateInfo(
                target_blk, diaspora=config.diaspora,
            ).is_yom_tov
            row.motzei = immediate_motzei
            row.motzei_label_he = (
                "מוצאי יום טוב" if target_is_yt else "מוצאי שב״ק"
            )

        # ── Rule 2: trailing row for end_blk ──
        # Per MSM convention, trailing rows are emitted ONLY for major
        # Tishrei YT blocks (RH D2, Sukkos D2, Simchat Torah). Non-
        # Tishrei YT blocks (Pesach D2, Shvi'i-of-Pesach D2, Shavuos
        # D2, etc.) DO NOT get a trailing row — they're implied by the
        # next parsha/Erev row. The two conditions:
        #   • block must be multi-day (start_blk != end_blk)
        #   • end_blk must be in Tishrei (Hebrew month 7)
        # ...plus the dedup check (end_blk not already covered by an
        # Erev row or the motzaei_sh_to_yt insert).
        if not _blk_needs_trailing:
            continue
        ph_end = PHebrewDate.from_pydate(end_blk)
        if end_blk in trailing_dates_emitted:
            continue
        if any(r.civil_date == end_blk for r in rows if r is not row):
            continue
        # Don't emit a trailing row past the requested end date. This
        # specifically drops the ב׳-דראש-השנה row of (hy+1) when the
        # yearly-sheet luach's range stops at Erev RH (hy+1).
        if end_date is not None and end_blk > end_date:
            continue

        sunset_end = sunset_for_date(geo=config.geo, tz=config.tz, base_date=end_blk)
        end_motzei = _round_ceil(
            sunset_end + timedelta(minutes=config.havdalah_offset)
        )
        last_is_yt = HDateInfo(end_blk, diaspora=config.diaspora).is_yom_tov
        end_label = "מוצאי יום טוב" if last_is_yt else "מוצאי שב״ק"

        trailing_main = (
            he.intra_block_day_label(ph_end, diaspora=config.diaspora)
            or "יום טוב"
        )
        # The weekday designation is the SF-style suffix to the
        # trailing main label (e.g. "ב׳ דראש השנה" + "יום ד׳" →
        # "ב׳ דראש השנה יום ד׳"). HE_WEEKDAY already omits Shabbos
        # for the edge case where end_blk lands on Shabbos.
        trailing_suffix = he.HE_WEEKDAY.get(end_blk.weekday(), "")
        trailing_title = f"{trailing_main} {trailing_suffix}".strip()
        extra_rows.append(LuachRow(
            civil_date=end_blk,
            hebrew_date_he=he.hebrew_date_str(
                end_blk, rc_emphasis=config.hebrew_date_rc_emphasis,
            ),  # MSM includes Hebrew date on trailing rows
            title_he=trailing_title,
            title_main_he=trailing_main,
            title_suffix_he=trailing_suffix,
            pirkei_avos_he="",
            special_shabbos_he=[],
            eruv_tavshilin=False,
            candle_lighting=None,
            candle_kind="trailing",
            motzei=end_motzei,
            motzei_label_he=end_label,
            zmanim={},
            omer_day=he.omer_day_for(end_blk),
        ))
        trailing_dates_emitted.add(end_blk)

    if extra_rows:
        rows.extend(extra_rows)
        rows.sort(key=lambda r: r.civil_date)
    return rows


def _insert_shabbos_to_yt_rows(
    rows: list[LuachRow],
    start: date_cls,
    end: date_cls,
    *,
    config: LuachConfig,
) -> None:
    """Insert a dedicated row for each Shabbos that's also Erev YT —
    i.e., where Shabbos transitions directly into Yom Tov at tzeis.

    This covers (in diaspora):
      • Erev Pesach on Shabbos (14 Nisan = Sat)
      • Erev Shvi'i shel Pesach on Shabbos (20 Nisan = Sat)
      • Erev Shavuos on Shabbos (5 Sivan = Sat)
      • Erev Rosh Hashana on Shabbos (29 Elul = Sat)
    Erev Sukkos can never fall on Shabbos by the BeDU calendar rule.

    The emitted row carries:
      • title: 'ערב <YT> - שב״ק'  (RH includes the year letters,
        matching the existing Friday-Erev-RH convention)
      • Hebrew date: the Saturday's Hebrew date
      • candle_lighting: None (no fresh lighting on the Shabbos→YT
        transition — light from an existing flame after tzeis)
      • motzei: tzeis of the **end** of the no-melacha block (e.g.,
        Mon-night havdalah for the 2-day Pesach/Shavuos/Shvi'i
        block). Shabbos tzeis itself is meaningless here — Shabbos
        rolls directly into Yom Tov, no havdalah said until the
        block ends. This row now carries that block-end time
        because MSM doesn't emit a separate ב׳-of-YT trailing row
        for non-Tishrei blocks (see ``_attach_motzei`` Rule 2).
      • zmanim: Saturday's (the row's own civil date) morning alos /
        sof zman shma. B1 convention — matches the same per-row-date
        rule used by the main Erev rows.

    Detection condition:
      d.weekday() == 5 AND NOT is_yt(d) AND is_yt(d+1)
    This deliberately excludes RH-D1-on-Shabbos (where Shabbos IS the
    YT day, not Erev) and Shabbos Chol HaMoed → weekday transitions.

    Mutates ``rows`` in place.
    """
    extra: list[LuachRow] = []
    d = start
    while d <= end:
        if d.weekday() == 5:
            hd_today = HDateInfo(d, diaspora=config.diaspora)
            hd_tom = HDateInfo(d + timedelta(days=1), diaspora=config.diaspora)
            if (not hd_today.is_yom_tov) and hd_tom.is_yom_tov:
                ph_tom = PHebrewDate.from_pydate(d + timedelta(days=1))
                yt = he.major_yt_name(ph_tom, diaspora=config.diaspora) or ""
                yt = yt.replace("שביעי של פסח", "שביעי ש״פ")
                yt = yt.replace("ראש השנה", "ר״ה")
                yt = _sf_compactify_yt_name(yt, config.compact_erev_yt_labels)
                if "ר״ה" in yt:
                    try:
                        yt = f"{yt} {he.hebrew_year_letters(ph_tom.year)}"
                    except Exception:
                        pass
                if yt:
                    title_main = _sf_compactify_erev_label(
                        f"ערב {yt}", config.compact_erev_yt_labels,
                    )
                    title_suffix = "שב״ק"
                else:
                    title_main = "שב״ק"
                    title_suffix = ""
                title = f"{title_main} {title_suffix}".strip()

                # Motzei = tzeis of the LAST day of the no-melacha
                # block (e.g., Mon for Erev Pesach on Sat → block is
                # Sat+Sun+Mon = Shabbos + 2 days YT). This is when
                # havdalah is actually said. Falls back gracefully to
                # the Saturday itself if the block lookup somehow
                # returns None (shouldn't happen — Sat is always
                # no-melacha — but defensive).
                block = he.no_melacha_block(d, diaspora=config.diaspora)
                end_blk = block[1] if block else d
                sunset_end = sunset_for_date(geo=config.geo, tz=config.tz, base_date=end_blk)
                motzei_dt = _round_ceil(
                    sunset_end + timedelta(minutes=config.havdalah_offset)
                )
                end_is_yt = HDateInfo(
                    end_blk, diaspora=config.diaspora,
                ).is_yom_tov
                motzei_label_he = (
                    "מוצאי יום טוב" if end_is_yt else "מוצאי שב״ק"
                )

                # B1 convention: alos / sof zman krias shma for the
                # row's own civil date (the Saturday/Shabbos), not the
                # next-day YT. Matches per-row-date alignment with the
                # KJ printed luach.
                all_zmanim = compute_zmanim_for_date(
                    geo=config.geo, tz=config.tz, base_date=d,
                    tallis_offset=config.tallis_offset,
                    havdalah_offset=config.havdalah_offset,
                )
                z_map: dict[str, datetime] = {}
                for entry in all_zmanim:
                    if entry.label in config.extra_zmanim_labels:
                        z_map[entry.label] = entry.dt_local

                extra.append(LuachRow(
                    civil_date=d,
                    hebrew_date_he=he.hebrew_date_str(d, rc_emphasis=config.hebrew_date_rc_emphasis),
                    title_he=title,
                    title_main_he=title_main,
                    title_suffix_he=title_suffix,
                    candle_lighting=None,
                    candle_kind="motzaei_sh_to_yt",
                    motzei=motzei_dt,
                    motzei_label_he=motzei_label_he,
                    zmanim=z_map,
                    omer_day=he.omer_day_for(d),
                ))
        d += timedelta(days=1)

    if extra:
        rows.extend(extra)
        rows.sort(key=lambda r: r.civil_date)


# ────────────────────────────────────────────────────────────────────────
# Annotation assembly
# ────────────────────────────────────────────────────────────────────────

def _build_annotations(
    start: date_cls,
    end: date_cls,
    *,
    config: LuachConfig,
    molad_provider,
    rows: list[LuachRow],
) -> list[AnnotationRow]:
    """Build all interleaved annotation rows for the date range."""
    out: list[AnnotationRow] = []
    out.extend(_annotations_mevorchim(start, end, config=config, molad_provider=molad_provider))
    out.extend(_annotations_molad_tishrei(start, end, config=config, molad_provider=molad_provider))
    out.extend(_annotations_tekufah(start, end, config=config))
    out.extend(_annotations_erev_pesach(start, end, config=config))
    out.extend(_annotations_fasts(start, end, config=config))
    out.extend(_annotations_minor_days(start, end, config=config))
    out.extend(_annotations_dst(start, end, config=config))
    out.extend(_annotations_hashala(start, end, config=config))
    out.extend(_build_pruzbol_annotations(start=start, end=end))
    return out


def _annotations_molad_tishrei(
    start: date_cls, end: date_cls, *, config: LuachConfig, molad_provider,
) -> list[AnnotationRow]:
    """Standalone Molad-Tishrei announcement.

    Tishrei has no Mevorchim Shabbos (the new Hebrew year is announced
    by RH itself, not via a benching of the upcoming month), but the
    molad of Tishrei IS still announced — typically rendered as a
    standalone line just above the Erev RH row, e.g.:

        מולד תשרי: יום ב׳ וילך אחה״צ 1:10 וז׳ חלקים

    For each RH in the date range, we look up the Tishrei molad via
    ``molad_provider`` (passing Erev RH, which is 29 Elul — day≥3 in
    Elul, so the provider returns the *next* month's molad = Tishrei).
    """
    out: list[AnnotationRow] = []
    if molad_provider is None:
        return out
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    for hy in range(start_hy, end_hy + 2):
        try:
            rh_d1 = PHebrewDate(hy, 7, 1).to_pydate()
        except Exception:
            continue
        erev_rh = rh_d1 - timedelta(days=1)
        if not (start <= erev_rh <= end):
            continue
        try:
            m = molad_provider(erev_rh)
        except Exception:
            m = None
        if m is None:
            continue
        molad_text = he.format_molad_short(
            m,
            diaspora=config.diaspora,
            metzora_display=config.metzora_display,
            style=config.molad_style,
        )
        out.append(AnnotationRow(
            civil_date=erev_rh,
            kind="molad_tishrei",
            text_he=f"מולד תשרי: {molad_text}",
            position="before",
        ))
    return out


def _annotations_mevorchim(
    start: date_cls, end: date_cls, *, config: LuachConfig, molad_provider,
) -> list[AnnotationRow]:
    """Mevorchim HaChodesh announcement annotations.

    Format (matches Monroe printed luach):
        מבה״ח <month-mevorchim-form> <rc-phrase> <parsha> · המולד: <molad>

    Notes:
      • Month name uses the Mevorchim-specific form ('מנחם אב' for Av,
        otherwise identical to the standard form).
      • The RC phrase ends with the parsha currently in effect for the
        Mevorchim Shabbos's week (e.g., 'ר״ח יום ב׳ וג׳ קרח').
      • Space (NOT '·') separates the month name from the RC phrase;
        the only '·' is before 'המולד:' when a molad is present.
    """
    out: list[AnnotationRow] = []
    for ev in he.mevorchim_in_range(start=start, end=end):
        # Mevorchim-form month name ('מנחם אב' for Av)
        month_he = he.hebrew_month_name_for_mevorchim(
            ev.hebrew_year, ev.hebrew_month,
        )

        # The parsha labels in the RC clause follow SF's convention:
        # each RC day gets the parsha "in effect" for that day's week
        # (i.e., the parsha of the upcoming Shabbos that contains the
        # day). When the RC spans Shabbos→Sunday, this produces
        # different parshas for the two days, and SF labels each one
        # explicitly (e.g., Teves 5786:
        #     "מבה״ח טבת: ר״ח שב״ק מקץ ויום א׳ ויגש"
        # — Shabbos is the final day of Mikeitz; Sunday begins Vayigash).
        #
        # When the RC's parsha is the SAME as the Mevorchim Shabbos's
        # own parsha (e.g. Sivan, where mevorchim Shabbos and the RC
        # day are both Bamidbar), we OMIT the redundant parsha label
        # — printed luachs don't repeat the parsha you just announced.
        mevorchim_parsha = he.parsha_current_for_date(
            ev.civil_date,
            diaspora=config.diaspora,
            metzora_display=config.metzora_display,
        )
        rc_parshas = [
            he.parsha_for_mevorchim_rc_day_he(
                rd,
                diaspora=config.diaspora,
                metzora_display=config.metzora_display,
            ) or ""
            for rd in ev.rc_civil_days
        ]
        # SF-style compact abbreviation for double-parsha names in the
        # Mevorchim line (e.g. 'תזריע-מצורע' → 'תז״מ'). Apply BEFORE
        # the suppression check so a Mevorchim Shabbos that happens to
        # be the same double parsha as the RC week still cancels
        # correctly. (In 5786 this matters: Iyar's Mevorchim Shabbos
        # = Shemini, but if a future year has both the same, the
        # equality check needs consistent forms on both sides.)
        mevorchim_parsha = _sf_abbrev_mevorchim_parsha(
            mevorchim_parsha, config.compact_mevorchim_parsha,
        )
        rc_parshas = [
            _sf_abbrev_mevorchim_parsha(p, config.compact_mevorchim_parsha)
            for p in rc_parshas
        ]
        # If every RC day's parsha equals the Mevorchim Shabbos's
        # parsha, suppress all parsha labels (use the no-parsha form).
        if rc_parshas and all(p == mevorchim_parsha for p in rc_parshas):
            rc_clause = ev.rc_phrase_he
        else:
            rc_clause = he.format_rc_days_with_parshas_he(
                ev.rc_civil_days, rc_parshas,
            )

        molad_text = ""
        if molad_provider is not None:
            try:
                m = molad_provider(ev.civil_date)
            except Exception:
                m = None
            if m is not None:
                molad_text = he.format_molad_short(
                    m,
                    diaspora=config.diaspora,
                    metzora_display=config.metzora_display,
                    style=config.molad_style,
                )
                # Same SF double-parsha abbreviation in the molad clause
                # (post-process; format_molad_short embeds the parsha
                # name internally).
                molad_text = _sf_abbrev_mevorchim_parsha(
                    molad_text, config.compact_mevorchim_parsha,
                )

        # Assemble: month + RC phrase (space-joined) · molad
        # SF convention: a colon after the month name before the RC
        # clause (e.g. "מבה״ח טבת: ר״ח שב״ק מקץ ויום א׳ ויגש").
        head = f"מבה״ח {month_he}: {rc_clause}"
        if molad_text:
            text = f"{head} {INFO_SEP} המולד: {molad_text}"
        else:
            text = head
        # Date the annotation to Erev Shabbos Mevorchim (Friday) so the
        # merge sorts it BEFORE the Mevorchim Shabbos row at that same
        # date, matching the printed luach convention of placing the
        # announcement immediately ABOVE the parsha row it announces:
        #     מבה״ח חשון ר״ח יום ד׳ וה׳ נח
        #     המולד: יום ד׳ נח בשעה 1:54 וח׳ חלקים אחה״צ
        #     בראשית, כ״ה תשרי                          ← Mevorchim Shabbos row
        #     נח, ב׳ חשון                                ← next row
        # If we dated the annotation to the Shabbos itself (Saturday),
        # it would sort BETWEEN the row and the following Friday's row,
        # which neither SF nor MSM does.
        erev_mevorchim = ev.civil_date - timedelta(days=1)
        out.append(AnnotationRow(
            civil_date=erev_mevorchim,
            kind="mevorchim",
            text_he=text,
            position="before",
        ))
    return out


def _annotations_tekufah(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """Tekufah (solar-quarter) announcement annotations.

    Anchor selection (matches MSM convention):
      • If the tekufah's civil date is a YT or chol-hamoed day → use
        the Hebrew date as the anchor (e.g. 'ט״ו תשרי', 'כ״א ניסן').
      • Otherwise → use the parsha of the upcoming Shabbos.

    Time-of-day suffix (matches MSM):
      • Parsha anchor → always include the time-of-day suffix from
        ``format_tekufah_time`` (e.g. 'בשעה 2:30 באשה״ב').
      • Hebrew-date anchor with hour ≥ 5 → include the suffix.
      • Hebrew-date anchor with hour < 5 → use the 'אור ל' prefix
        instead, and OMIT the time-of-day suffix (the prefix encodes
        the time context).
    """
    out: list[AnnotationRow] = []
    for tk in he.compute_tekufos_in_range(start=start, end=end, tz=config.tz):
        # Weekday label — Shabbos shows as 'שב״ק' rather than 'יום ז׳'
        wd_he = he.HE_WEEKDAY.get(tk.dt_local.weekday(), "שב״ק")
        tk_date = tk.dt_local.date()

        # Anchor selection
        ph_tk = PHebrewDate.from_pydate(tk_date)
        is_tk_yt = HDateInfo(tk_date, diaspora=config.diaspora).is_yom_tov
        is_tk_chol = _is_chol_hamoed(ph_tk, diaspora=config.diaspora)
        use_hebrew_date_anchor = is_tk_yt or is_tk_chol

        parsha = ""
        if not use_hebrew_date_anchor:
            # Try parsha for the upcoming Shabbos
            if tk_date.weekday() == 5:
                shabbos = tk_date
            else:
                shabbos = tk_date + timedelta(days=(5 - tk_date.weekday()) % 7)
            try:
                parsha = he.parsha_name(
                    shabbos, diaspora=config.diaspora,
                    metzora_display=config.metzora_display,
                )
            except Exception:
                parsha = ""
            if not parsha:
                # Upcoming Shabbos has no parsha (rare — e.g., chol hamoed
                # Shabbos when tekufah itself is on a weekday).
                use_hebrew_date_anchor = True

        if use_hebrew_date_anchor:
            hebrew_date_str = he.hebrew_date_str(tk_date, rc_emphasis=config.hebrew_date_rc_emphasis)
            # SF convention for tekufos that land on a YT day: place
            # the YT name between the weekday and the Hebrew date, with
            # a comma separating them; use the "ליל" prefix (with the
            # bare weekday letter, no "יום") for the night portion, or
            # no prefix and "יום <letter>" for the daytime portion.
            #
            #   תקופת ניסן: ליל ד׳ שביעי של פסח, כ״א ניסן בשעה 1:00
            #   תקופת תשרי: יום ה׳ סוכות, ט״ו תשרי בשעה 4:30 אחה״צ
            #
            # When the tekufah lands on a chol-hamoed (non-YT-proper)
            # day, the YT name isn't available from ``major_yt_name``;
            # we fall back to the original luach format ("אור ל" +
            # weekday + Hebrew date).
            yt_name = (
                he.major_yt_name(ph_tk, diaspora=config.diaspora)
                if is_tk_yt else None
            )
            if yt_name:
                # SF YT format
                # Strip "יום " from the weekday to get the bare letter
                # ("יום ד׳" → "ד׳"); special weekday names like "עש״ק"
                # / "שב״ק" stay unchanged.
                wd_bare = wd_he.removeprefix("יום ")
                if tk.dt_local.hour < 5:
                    prefix = "ליל "
                    time_str = f"{tk.dt_local.hour}:{tk.dt_local.minute:02d}"
                else:
                    prefix = "יום "
                    time_str = he.format_tekufah_time(tk.dt_local)
                anchor = f"{wd_bare} {yt_name}, {hebrew_date_str}"
                text = f"{tk.label_he}: {prefix}{anchor} בשעה {time_str}"
                out.append(AnnotationRow(
                    civil_date=tk_date - timedelta(days=1),
                    kind="tekufah",
                    text_he=text,
                    position="before",
                ))
                continue
            # Legacy format (chol-hamoed or unmatched YT): "אור ל" /
            # plain-weekday prefix; no YT name.
            anchor = hebrew_date_str
            if tk.dt_local.hour < 5:
                prefix = "אור ל"
                time_str = f"{tk.dt_local.hour}:{tk.dt_local.minute:02d}"
            else:
                prefix = ""
                time_str = he.format_tekufah_time(tk.dt_local)
        else:
            anchor = parsha
            prefix = ""
            time_str = he.format_tekufah_time(tk.dt_local)

        text = (
            f"{tk.label_he}: {prefix}{wd_he} {anchor} בשעה {time_str}"
        )
        # SF places the tekufah announcement BEFORE the closest
        # row that follows the tekufah's eve, not after the tekufah
        # day itself. Concretely:
        #   • Yom-tov anchor (Tishrei → Erev Sukkos, Nissan → Erev
        #     Shvii Shel Pesach): the row at ``tk_date - 1`` is the
        #     "Erev" row for that block — we want to sit just above
        #     it.
        #   • Parsha anchor (Teves → Shemot, Tammuz → Matos-Masei):
        #     ``tk_date - 1`` falls in the empty gap between two
        #     parsha Fridays, so the announcement appears in that
        #     gap (same placement as before, just shifted one day).
        # ``position="before"`` guarantees we sort ahead of any row
        # at the new civil_date.
        out.append(AnnotationRow(
            civil_date=tk_date - timedelta(days=1),
            kind="tekufah",
            text_he=text,
            position="before",
        ))
    return out


def _annotations_erev_pesach(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """Erev Pesach chametz deadlines.

    Two formats:
      • **Erev Pesach on a weekday (normal case):** single line with
        eating + biyul-and-burning combined, anchored AFTER the Erev
        Pesach row.
      • **Erev Pesach on Shabbos (rare case):** three day-labeled
        items — שריפה on עש״ק (burning moves to Friday since you
        can't kindle fire on Shabbos), אכילה + ביטול on שב״ק. Anchored
        BEFORE the Friday Tzav-Shabbos-Hagadol row, since the burning
        deadline falls on that Friday morning.
    """
    out: list[AnnotationRow] = []
    start_hy = PHebrewDate.from_pydate(start).year
    end_hy = PHebrewDate.from_pydate(end).year
    for hy in range(start_hy - 1, end_hy + 2):
        try:
            erev_pesach = PHebrewDate(hy, 1, 14).to_pydate()
        except Exception:
            continue
        if not (start <= erev_pesach <= end):
            continue

        if erev_pesach.weekday() == 5:
            # Erev Pesach on Shabbos: burning moves to Friday; eating
            # and nullifying remain on Shabbos.
            friday = erev_pesach - timedelta(days=1)
            _, fri_sriefes = compute_chametz_zmanim(
                geo=config.geo, tz=config.tz, base_date=friday,
                havdalah_offset=config.havdalah_offset,
                sriefes_round="floor",
            )
            sat_achilas, sat_sriefes = compute_chametz_zmanim(
                geo=config.geo, tz=config.tz, base_date=erev_pesach,
                havdalah_offset=config.havdalah_offset,
                sriefes_round="floor",
            )
            fs_str = _weekly_fmt_time(fri_sriefes, config.time_format)
            sa_str = _weekly_fmt_time(sat_achilas, config.time_format)
            ss_str = _weekly_fmt_time(sat_sriefes, config.time_format)
            text = (
                f"סוף זמן שריפת חמץ עש״ק {fs_str} - "
                f"סוף זמן אכילת חמץ שב״ק {sa_str} - "
                f"סוף זמן ביטול חמץ שב״ק {ss_str}"
            )
            # Anchor BEFORE the Friday Tzav-Shabbos-Hagadol row so the
            # burning deadline is shown above its day.
            # The SF SHEET prints this as TWO lines, abbreviated, with
            # the seder-night chatzos folded onto line 2 (verified vs
            # the printed 5785 sheet). It lives in text_sheet_he, so
            # the weekly card still gets the plain single-line text_he.
            try:
                _ch = _pesach_chatzos_str(erev_pesach, config)
            except Exception:
                _ch = ""
            _sheet = (
                f"סו״ז שריפת ומכירת חמץ: ביום עש״ק בשעה {fs_str} - "
                f"סו״ז אכילת חמץ: בש״ק בשעה {sa_str}\n"
                f"סו״ז ביעור חמץ: ואמירת כל חמירא בש״ק בשעה {ss_str}"
            )
            if _ch:
                _sheet += f" - זמן חצות: בלילי פסח {_ch}"
            out.append(AnnotationRow(
                civil_date=friday,
                kind="erev_pesach_chametz",
                text_he=text,
                text_sheet_he=_sheet,
                position="before",
            ))
        else:
            # Weekday Erev Pesach (the common case). SF prints up to
            # two lines BEFORE the Erev Pesach row:
            #   Line 1 (always): chametz deadlines (eating + burning/
            #          selling/nullifying)
            #   Line 2 (chatzos halayla) — always shown
            #   Line 2 KL note — the REAL סוף זמן קידוש לבנה of
            #          Nissan, computed and formatted by the shared
            #          ``_szkl_anchor_when`` night/day rule (same one
            #          the weekly luach uses, verified vs the printed
            #          ZMAN Table-3). Always shown at Pesach now that
            #          it carries the actual value (e.g.
            #          'ליל א׳ דפסח כל הלילה' on a day-1-daytime year,
            #          or an explicit night time / anchor otherwise) —
            #          no longer a static phrase gated to ~1/3 years.
            achilas, sriefes = compute_chametz_zmanim(
                geo=config.geo, tz=config.tz, base_date=erev_pesach,
                havdalah_offset=config.havdalah_offset,
                sriefes_round="floor",
            )
            # Chatzos halayla for the first night of Pesach (the
            # night that begins at sunset of Erev Pesach). Uses the
            # same MGA midpoint algorithm as YidCal's
            # ``ChatzosHaLailaSensor`` so the luach text and the
            # sensor's displayed value agree to the minute.
            chatzos_night = chatzos_halayla_for_night(
                geo=config.geo, tz=config.tz, base_date=erev_pesach,
            )

            # Determine whether to include the KL note. The note's
            # halachic claim ("the first night of Pesach is the last
            # chance, said the whole night") is only valid when SZKL
            # Nissan falls during the DAY of 15 Nissan (alos through
            # sunset) — i.e., after night-1 of Pesach ends but before
            # night-2 begins.
            # Real סוף זמן קידוש לבנה of Nissan, formatted by the same
            # night/day rule as the weekly luach (Table-3-verified).
            szkl_nissan = sof_zman_kiddush_levana_rama_local(
                hy, 1, config.tz,
            )
            szkl_he = _szkl_anchor_when(
                szkl_nissan, geo=config.geo, tz=config.tz,
                diaspora=config.diaspora,
                time_fmt=config.time_format,
            )

            a_str = _weekly_fmt_time(achilas, config.time_format)
            s_str = _weekly_fmt_time(sriefes, config.time_format)
            c_str = _weekly_fmt_time(chatzos_night, config.time_format)
            line1 = (
                f"סוף זמן אכילת חמץ {a_str} - "
                f"סוף זמן שריפת חמץ {s_str}"
            )
            line2 = (
                f"זמן חצות: בלילי פסח {c_str} - "
                f"סוף זמן קידוש לבנה {szkl_he}"
            )
            # Two separate "before" annotations on the SAME date
            # render as two stacked lines immediately above the Erev
            # Pesach row, in the order they're appended (the stable
            # sort in _merge_in_order preserves insertion order within
            # (civil_date, position) ties).
            out.append(AnnotationRow(
                civil_date=erev_pesach,
                kind="erev_pesach_chametz",
                text_he=line1,
                position="before",
            ))
            out.append(AnnotationRow(
                civil_date=erev_pesach,
                kind="erev_pesach_chametz",
                text_he=line2,
                position="before",
            ))
    return out


# Weekday-anchor form used in annotation lines (minor days, fasts,
# tekufos): Sun–Thu = יום X׳, Fri = עש״ק (erev Shabbos), Sat = שב״ק.
_HE_WEEKDAY_ANCHOR = {
    0: "יום ב׳", 1: "יום ג׳", 2: "יום ד׳", 3: "יום ה׳",
    4: "עש״ק",   5: "שב״ק",   6: "יום א׳",
}


def _weekday_parsha_anchor(
    d: date_cls, *, diaspora: bool, metzora_display: str,
) -> str:
    """Return '<weekday-form> <parsha-current-for-week>' for ``d``.

    Empty string if no parsha could be resolved.
    """
    wd = _HE_WEEKDAY_ANCHOR.get(d.weekday(), "")
    parsha = he.parsha_current_for_date(
        d, diaspora=diaspora, metzora_display=metzora_display,
    )
    if parsha:
        return f"{wd} {parsha}" if wd else parsha
    return wd


def _annotations_fasts(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """Minor fasts (17 Tammuz, Tzom Gedaliah, Asara B'Teves, Ta'anis
    Esther) and Tisha B'Av annotations.

    Formats (match Monroe printed luach):
      Minor fasts (single line):
        '<label> <wd> <parsha> - עלה״ש H:MM צאה״כ H:MM'
      Tisha B'Av line A (fast bounds):
        '<label> <wd> <parsha> - התחלת זמן התענית ערב תשעה באב H:MM ·
         צאה״כ מוצאי ת״ב H:MM'
      Tisha B'Av line B (mid-day; chatzos kept as YidCal addition):
        '<label> · חצות H:MM · מנחה גדולה H:MM · מנחה קטנה H:MM'
    """
    out: list[AnnotationRow] = []
    for fast in he.fasts_in_range(start=start, end=end):
        actual = fast.actual_date
        anchor = _weekday_parsha_anchor(
            actual,
            diaspora=config.diaspora,
            metzora_display=config.metzora_display,
        )
        label_anchor = (
            f"{fast.label_he} {anchor}" if anchor else fast.label_he
        )

        if fast.kind in (
            "17_tammuz", "taanis_esther",
            "asara_btevet", "tzom_gedaliah",
        ):
            zmanim = compute_zmanim_for_date(
                geo=config.geo, tz=config.tz, base_date=actual,
                tallis_offset=config.tallis_offset,
                havdalah_offset=config.havdalah_offset,
            )
            alos = next(z for z in zmanim if z.label == "עלות השחר").dt_local
            tzeis = next(z for z in zmanim if z.label == "צאת הכוכבים").dt_local
            # Fast start alos: HALF-UP, matching the printed SF 5786
            # sheet (all four printed fast-alos values fit half-up;
            # floor loses Ta'anis Esther 5786: raw 5:19:36 prints as
            # 5:20, not 5:19). The `alos` fetched above is already the
            # half-up display value (same source as the sheet's alos
            # column, verified 61/61 vs the print), so reuse it.
            # Sensors keep zman_compute's floor-lechumra fast start —
            # this only changes the printed luach line.
            a_str = f"{alos.hour % 12 or 12}:{alos.minute:02d}"
            t_str = f"{tzeis.hour % 12 or 12}:{tzeis.minute:02d}"
            out.append(AnnotationRow(
                civil_date=actual,
                kind=fast.kind,
                text_he=f"{label_anchor} - עלה״ש {a_str} צאה״כ {t_str}",
                position="before",
            ))
        elif fast.kind == "tisha_bav":
            erev = actual - timedelta(days=1)
            z_erev = compute_zmanim_for_date(
                geo=config.geo, tz=config.tz, base_date=erev,
                tallis_offset=config.tallis_offset,
                havdalah_offset=config.havdalah_offset,
            )
            z_day = compute_zmanim_for_date(
                geo=config.geo, tz=config.tz, base_date=actual,
                tallis_offset=config.tallis_offset,
                havdalah_offset=config.havdalah_offset,
            )
            shkia_erev = next(z for z in z_erev if z.label == "שקיעת החמה").dt_local
            tzeis_day = next(z for z in z_day if z.label == "צאת הכוכבים").dt_local
            chatzos = next(z for z in z_day if z.label == "חצות היום").dt_local
            mincha_gd = next(z for z in z_day if z.label == "מנחה גדולה").dt_local
            mincha_ket = next(z for z in z_day if z.label == "מנחה קטנה").dt_local
            # Fast start: floor lechumra (fast begins BEFORE astronomical
            # shkia, never after). Single source of truth:
            # zman_compute.format_fast_start_clock. Pass the EREV civil
            # date (erev) since T"B begins at sunset of Erev T"B.
            s_str = format_fast_start_clock(
                geo=config.geo, tz=config.tz, base_date=erev,
                anchor=FAST_START_SHKIA,
            )
            t_str = f"{tzeis_day.hour % 12 or 12}:{tzeis_day.minute:02d}"
            c_str = f"{chatzos.hour % 12 or 12}:{chatzos.minute:02d}"
            mg_str = f"{mincha_gd.hour % 12 or 12}:{mincha_gd.minute:02d}"
            mk_str = f"{mincha_ket.hour % 12 or 12}:{mincha_ket.minute:02d}"
            if config.tisha_bav_single_line:
                # Yearly-sheet format: one chronological T"B block,
                # wrapped at the חצות boundary purely for width (the
                # merged line cannot fit the 95 mm column at 8 pt).
                # No repeated fast label; erev abbreviated ת״ב to
                # match the מוצאי ת״ב convention later in the block.
                out.append(AnnotationRow(
                    civil_date=actual,
                    kind="tisha_bav_a",
                    text_he=(
                        f"{label_anchor} - התחלת זמן התענית ערב ת״ב {s_str}"
                    ),
                    position="before",
                ))
                out.append(AnnotationRow(
                    civil_date=actual,
                    kind="tisha_bav_b",
                    text_he=(
                        f"חצות {c_str} {INFO_SEP} מנחה גדולה {mg_str} {INFO_SEP} "
                        f"מנחה קטנה {mk_str} {INFO_SEP} צאה״כ מוצאי ת״ב {t_str}"
                    ),
                    position="before",
                ))
            else:
                out.append(AnnotationRow(
                    civil_date=actual,
                    kind="tisha_bav_a",
                    text_he=(
                        f"{label_anchor} - התחלת זמן התענית ערב תשעה באב {s_str} {INFO_SEP} "
                        f"צאה״כ מוצאי ת״ב {t_str}"
                    ),
                    position="before",
                ))
                # Line B: label only (no weekday/parsha anchor), and we
                # keep חצות as a YidCal-specific addition (Monroe drops it).
                out.append(AnnotationRow(
                    civil_date=actual,
                    kind="tisha_bav_b",
                    text_he=(
                        f"{fast.label_he} {INFO_SEP} חצות {c_str} {INFO_SEP} "
                        f"מנחה גדולה {mg_str} {INFO_SEP} מנחה קטנה {mk_str}"
                    ),
                    position="before",
                ))
    return out


def _annotations_minor_days(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """Pesach Sheni / Lag BaOmer / 15 Av / Purim / Chanukah Night 1 /
    Tu B'Shvat annotations, Monroe-style.

    Format: ``<label><sep><weekday-form> <parsha-current-for-week>``
        • Weekday Sun–Thu: 'יום א׳' .. 'יום ה׳'
        • Friday:          'עש״ק' (standard form) or 'יום ו׳' (אור ל form)
        • Shabbos:         'שב״ק'
        • Parsha is the parsha currently in effect for the week
          containing the minor day (see ``parsha_current_for_date``).
        • Separator ``-`` (no spaces) for Purim, Shushan Purim,
          Chanukah Night 1, and Tu B'Shvat; `` - `` (with spaces) for
          others.

    Some labels are spelled out per Monroe convention:
        • 'ט״ו באב' → 'חמשה עשר באב'
        • 'ט״ו בשבט' → 'חמשה עשר בשבט'

    Chanukah Night 1 uses the eve-form anchor:
        'ליל א׳ דחנוכה-אור ל<wd> <parsha>'
    """
    spelled_out = {
        "ט״ו באב":  "חמשה עשר באב",
        "ט״ו בשבט": "חמשה עשר בשבט",
    }
    # MSM convention: '-' no-space separator for these kinds; all
    # others use ' - ' with spaces.
    no_space_dash_kinds = {
        "purim", "shushan_purim",
        "chanukah_night_1", "tu_bishvat",
    }

    # Purim (14 Adar) vs Shushan Purim (15 Adar) — picked by the
    # integration-wide diaspora flag (= not is_in_israel):
    #   • diaspora=True  → render Purim on 14 Adar, skip Shushan Purim
    #   • diaspora=False → render Shushan Purim on 15 Adar, skip Purim
    # ``minor_days_in_range`` emits both as raw data so the same flag
    # can drive sensors and the planned calendar entity consistently.
    skip_kinds: set[str] = (
        {"shushan_purim"} if config.diaspora else {"purim"}
    )

    out: list[AnnotationRow] = []
    for m in he.minor_days_in_range(start=start, end=end):
        if m.kind in skip_kinds:
            continue
        display_label = spelled_out.get(m.label_he, m.label_he)

        if m.kind == "chanukah_night_1":
            # Eve-of form: 'אור ל<wd-of-Day-1> <parsha>'. civil_date is
            # the daytime of Hebrew Day 1; lighting is the prior evening.
            wd_form = _HASHALAH_WD_FORM.get(m.civil_date.weekday(), "")
            try:
                parsha = he.parsha_current_for_date(
                    m.civil_date,
                    diaspora=config.diaspora,
                    metzora_display=config.metzora_display,
                )
            except Exception:
                parsha = ""
            anchor_parts = [p for p in (wd_form, parsha) if p]
            anchor = " ".join(anchor_parts)
            text = (
                f"{display_label}-אור ל{anchor}" if anchor else display_label
            )
        else:
            anchor = _weekday_parsha_anchor(
                m.civil_date,
                diaspora=config.diaspora,
                metzora_display=config.metzora_display,
            )
            sep = "-" if m.kind in no_space_dash_kinds else " - "
            text = f"{display_label}{sep}{anchor}" if anchor else display_label

        out.append(AnnotationRow(
            civil_date=m.civil_date,
            kind=m.kind,
            text_he=text,
            position="before",
        ))
    return out


def _annotations_dst(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """DST start/end announcements, Yiddish per Monroe convention.

    Anchored on the transition date (typically Sunday) with
    ``position="before"`` — so the announcement renders just above
    the next Erev Shabbos row, matching the printed luach.
    """
    out: list[AnnotationRow] = []
    for chg in he.dst_changes_in_range(start=start, end=end, tz=config.tz):
        if chg.kind == "dst_start":
            text = "פון די וואך איז די פארגערוקטע צייט (סעיווינגס טיים)"
            sheet = ("פון די וואך אן איז די פארגעריקטע צייט\n"
                     "(דעילייט סעיווינגס טיים)")
        else:
            text = "פון די וואך איז די נארמאלע צייט (סטענדערד טיים)"
            sheet = ("פון די וואך אן איז די נארמאלע צייט\n"
                     "(איסטערן סטענדארד טיים)")
        out.append(AnnotationRow(
            civil_date=chg.civil_date,
            kind=chg.kind,
            text_he=text,
            # Yearly SINGLE SHEET only — verbatim from the printed SF
            # sheet (nikud included). text_he keeps the existing
            # wording for every other consumer.
            text_sheet_he=sheet,
            position="before",
        ))
    return out


# Hashalah weekday form: MSM uses 'יום ו׳' (NOT 'עש״ק') and 'שב״ק'
# (no יום prefix) for the day designation following 'אור ל'.
_HASHALAH_WD_FORM = {
    0: "יום ב׳", 1: "יום ג׳", 2: "יום ד׳", 3: "יום ה׳",
    4: "יום ו׳", 5: "שב״ק",   6: "יום א׳",
}


def _annotations_hashala(
    start: date_cls, end: date_cls, *, config: LuachConfig,
) -> list[AnnotationRow]:
    """Hashalah / Tal U'Matar recitation-start announcement.

    Format (matches MSM/KJ):
        השאלה: אור ל<wd-form> <parsha> <hebrew-date> מתחילין ותן טל ומטר

    Diaspora users get the Tekufas-Tishrei + 59 days start; Israel
    users get the 7 Cheshvan start. Anchored at the start day with
    ``position="before"`` so it renders just above that day's Erev
    row (the recitation begins at the prior Maariv, hence 'אור ל').
    """
    out: list[AnnotationRow] = []
    wanted = "diaspora" if config.diaspora else "israel"
    for s in he.tal_umatar_starts_in_range(
        start=start, end=end, tz=config.tz,
    ):
        if s.observance != wanted:
            continue
        wd_form = _HASHALAH_WD_FORM.get(s.civil_date.weekday(), "")
        parsha = ""
        try:
            parsha = he.parsha_current_for_date(
                s.civil_date,
                diaspora=config.diaspora,
                metzora_display=config.metzora_display,
            )
        except Exception:
            parsha = ""
        hebrew_date = he.hebrew_date_str(s.civil_date, rc_emphasis=config.hebrew_date_rc_emphasis)
        anchor_parts = [p for p in (wd_form, parsha, hebrew_date) if p]
        anchor = " ".join(anchor_parts)
        text = f"השאלה: אור ל{anchor} מתחילין ותן טל ומטר"
        out.append(AnnotationRow(
            civil_date=s.civil_date,
            kind="hashala",
            text_he=text,
            position="before",
        ))
    return out


def _merge_in_order(
    rows: list[LuachRow], annotations: list[AnnotationRow],
) -> list[LuachItem]:
    rows_sorted = sorted(rows, key=lambda r: r.civil_date)
    rows_by_date: dict[date_cls, LuachRow] = {r.civil_date: r for r in rows_sorted}

    annotations_sorted = sorted(
        annotations, key=lambda a: (a.civil_date, a.position == "after"),
    )

    all_dates: set[date_cls] = set(rows_by_date.keys())
    for a in annotations_sorted:
        all_dates.add(a.civil_date)

    out: list[LuachItem] = []
    for date in sorted(all_dates):
        for a in annotations_sorted:
            if a.civil_date == date and a.position == "before":
                out.append(a)
        if date in rows_by_date:
            out.append(rows_by_date[date])
        for a in annotations_sorted:
            if a.civil_date == date and a.position == "after":
                out.append(a)
    return out

# ════════════════════════════════════════════════════════════════════════
# Weekly luach (KY-style single-card layout)
# ════════════════════════════════════════════════════════════════════════
#
# The weekly luach is a single Sun→Shabbos card with one row per civil
# day showing that day's full daily zmanim, plus a header block for the
# upcoming Shabbos / Yom Tov (parsha or YT name, Pirkei-Avos perek,
# candle-lighting + motzei time boxes, and molad / mevorchim / tekufah
# info lines).
#
# Data sourcing is intentionally identical to the yearly luachs:
#   • daily zmanim  →  zman_compute.compute_zmanim_for_date()
#   • header / candle / motzei / annotations  →  build_luach() over the
#     week's civil range (the exact same row+annotation stream the
#     yearly-multi-page and yearly-sheet renderers consume)
#
# This function is purely additive — it does not change any existing
# build_luach / _build_rows / _build_annotations behaviour, so the
# yearly luachs are unaffected (no regression risk).


@dataclass
class WeeklyDayRow:
    """One civil day (Sun..Shabbos) of the weekly card."""
    civil_date: date_cls
    weekday_he: str            # 'יום א׳' … 'יום ו׳' / 'עש״ק' / 'שב״ק'
    hebrew_dom_he: str         # 'כג אייר' (plain day + month, no RC emphasis)
    dom_sublabel_he: str       # '' or 'ערב פסח' / 'א׳ דפסח' / 'ר״ח' / 'אסרו חג' …
    omer_letters_he: str       # '' or 'ל״ח'  (sefiras-haomer day, letter form)
    zmanim: dict[str, datetime]  # canonical-label → aware local datetime
    # Unrounded astronomical value per zman (aware local) — used only
    # by the weekly service's add_seconds option to print H:MM:SS in
    # the grid. Defaults empty so existing construction stays valid.
    zmanim_raw: dict[str, datetime] = field(default_factory=dict)
    is_shabbos: bool = False
    is_yomtov: bool = False


@dataclass
class WeeklyBox:
    """One header time-box (the two big boxes + any extra small boxes)."""
    label_he: str              # 'הדלקת הנרות' / 'מוצאי שב״ק' / 'הדלה״נ ליל ב׳ דיו״ט' …
    time_str: str              # 'H:MM' (12-hour, AM/PM implicit)
    big: bool = False          # True for the two primary boxes


@dataclass
class WeeklyData:
    """Everything a weekly-card renderer needs for one week."""
    week_start: date_cls       # Sunday
    week_end: date_cls         # Shabbos
    title_main_he: str         # hero label — parsha name or 'ערב שבועות' …
    title_sub_he: str          # second line — 'מבה״ח, פרק ו' / 'פרק ב' / 'עירוב תבשילין'
    boxes: list[WeeklyBox]
    info_lines_he: list[str]   # molad / mevorchim / tekufah lines (under the boxes)
    chametz_lines_he: list[str]  # Erev-Pesach only: achilas/sriefes-chametz lines
    days: list[WeeklyDayRow]   # exactly 7 rows, Sunday→Shabbos
    diaspora: bool
    # Optional STACKED sub-lines (each on its own row, under the hero).
    # Used only where the printed luach stacks the sub vertically —
    # currently just the הושענא רבה card, which prints the YT names
    # 'שמיני עצרת' / 'שמחת תורה' on two separate lines (verified vs the
    # printed Brooklyn/KY 5786 הושענא רבה card). When non-empty the
    # renderer draws these instead of title_sub_he; when empty the
    # renderer falls back to the single-line title_sub_he (all other
    # cards, e.g. 'מבה״ח, פרק ב'' stays comma-joined on one line).
    title_sub_stack_he: list[str] = field(default_factory=list)
    # Optional STACKED *BIG* title lines — each rendered at the SAME
    # large hero size (not the smaller sub size). Used where the
    # printed luach prints a two-line title at equal prominence,
    # e.g. the Erev-Shabbos-Chol-HaMoed card:
    #   ערב שבת
    #   חוה״מ סוכות      (both lines big, verified vs printed KY card)
    # When non-empty the renderer draws THESE (big, stacked) instead
    # of title_main_he + any sub line(s).
    title_main_stack_he: list[str] = field(default_factory=list)
    # OPEN ITEMS flagged for halachic sourcing (Yoel verifies against the
    # printed KY luach — see release notes). Empty when nothing applies.
    open_notes: list[str] = field(default_factory=list)
    # Weekly service `add_seconds` option: when True the renderer
    # prints the GRID zman columns as H:MM:SS from the unrounded
    # astronomical value (zmanim_raw). Candle/havdalah/motzei boxes
    # are unaffected (they keep their halachic rounding). Default
    # False — sensors, yearly luachs and normal calls are untouched.
    add_seconds: bool = False
    # Clock format for renderer-side time strings (the 12 grid
    # columns): "12" (default) or "24". Box/chametz/KL strings are
    # already formatted by the builder; this lets the renderers
    # match them in the grid.
    time_format: str = "12"
    # True iff title_main_he is the week's PARSHA name (the hero
    # renderer prefixes 'פרשת' only then — YT titles like
    # 'שביעי של פסח' must stay bare).
    title_is_parsha: bool = False
    # Parsha of the Shabbos that CLOSES this card's no-melacha block,
    # when the hero doesn't already name it (הושענא רבה → בראשית,
    # ער״ה Thu+Fri → האזינו). Empty when the block doesn't end on a
    # Shabbos, or that Shabbos has no parsha (חוה״מ / YT). The Weekly
    # YidCal renderer prints it above the מברכים/מולד panel lines;
    # the legacy renderer ignores the field.
    block_parsha_he: str = ""


# Canonical zman labels (exactly as emitted by zman_compute) for the 12
# time columns of the KY weekly table, in the printed right→left order.
# The KY card uses MGA for סוף-זמן-קר״ש / תפלה / פלג and GRA for the
# secondary קר״ש / תפלה columns — verified against the printed
# פרשת במדבר card (5786, Kiryas Yoel).
WEEKLY_ZMAN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("עלות השחר",            "עלות השחר"),
    ("הנץ החמה",             "הנץ החמה"),
    ("סוף זמן קר״ש",         "סוף זמן קריאת שמע מג״א"),   # boxed/emphasised column
    ("סוזק״ש גר״א",          "סוף זמן קריאת שמע גר״א"),
    ("סוף זמן תפלה",         "סוף זמן תפילה מג״א"),
    ("סוז״ת גר״א",           "סוף זמן תפילה גר״א"),
    ("חצות היום",            "חצות היום"),
    ("מנחה גדולה",           "מנחה גדולה"),
    ("מנחה קטנה",            "מנחה קטנה"),
    ("פלג המנחה",            "פלג המנחה מג״א"),
    ("שקיעת החמה",           "שקיעת החמה"),
    ("צאה״כ",                "צאת הכוכבים"),
)

# Which header column gets the inverted "boxed" emphasis treatment.
WEEKLY_BOXED_COLUMN = "סוף זמן קר״ש"


def _weekly_fmt_time(dt: datetime, fmt: str = "12") -> str:
    """'H:MM' clock string for the weekly card.

    fmt="12" (default) → 12-hour, no AM/PM (the printed luachs
    leave it implicit). fmt="24" → zero-padded 24-hour 'HH:MM'
    (the integration's time_format option).
    """
    if fmt == "24":
        return f"{dt.hour:02d}:{dt.minute:02d}"
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}"


def _szkl_day_letter(dd: date_cls) -> str:
    """'א׳'…'ו׳' for Sun–Fri, 'שב״ק' for Shabbos (KL/ז״ש anchor)."""
    if dd.weekday() == 5:
        return "שב״ק"
    n = (dd.weekday() + 1) % 7 + 1            # Sun=1 … Fri=6
    try:
        return he.int_to_hebrew_letters(n)
    except Exception:
        return he.HE_WEEKDAY.get(dd.weekday(), "")


def _szkl_anchor_when(
    sk: datetime, *, geo, tz: ZoneInfo, diaspora: bool,
    time_fmt: str = "12",
) -> str:
    """Anchor + when for a ס״ז-קידוש-לבנה deadline, applying ZMAN's
    "show times at day" rule (verified vs the printed Williamsburg
    5786 Table-3, 12/12 months):

      • If the deadline lands in daytime (between נץ and שקיעה) it
        can't be said then, so the practical last opportunity is the
        PRECEDING night — printed '<anchor> כל הלילה'.
      • If it lands at night, the exact time is given with a night
        anchor: 'ליל <intra>' on a YT / Chol-HaMoed night (e.g.
        'ליל א׳ דפסח'), 'אור ליום <X>' on an ordinary pre-dawn night,
        or 'ליל <X>' on an ordinary evening night.

    Returns just the fragment (e.g. 'ליל א׳ דפסח כל הלילה',
    'אור ליום ו׳ 12:39', 'ליל ב׳ 2:07') so callers can prepend their
    own label ('ס״ז קידוש לבנה: …' weekly / 'סוף זמן קידוש לבנה …'
    yearly). ``sk`` is a naive local-clock datetime.
    """
    def _anchor(dd: date_cls, *, pre_dawn: bool) -> str:
        try:
            _intra = he.intra_block_day_label(
                PHebrewDate.from_pydate(dd), diaspora=diaspora)
        except Exception:
            _intra = ""
        if _intra:
            return f"ליל {_intra}"
        return (
            f"אור ליום {_szkl_day_letter(dd)}"
            if pre_dawn else f"ליל {_szkl_day_letter(dd)}"
        )

    d = sk.date()
    try:
        netz, shkia = sun_events_for_date(geo=geo, tz=tz, base_date=d)
        sk_a = sk.replace(tzinfo=tz)
        is_day = netz <= sk_a <= shkia
    except Exception:
        is_day = False          # fail safe → show the exact time
        netz = None
    if is_day:
        # Daytime → roll to the night that BEGAN this civil day.
        return f"{_anchor(d, pre_dawn=False)} כל הלילה"
    pre_dawn = (netz is None) or (sk.replace(tzinfo=tz) < netz)
    anchor_d = d if pre_dawn else (d + timedelta(days=1))
    return (
        f"{_anchor(anchor_d, pre_dawn=pre_dawn)} "
        f"{_weekly_fmt_time(sk, time_fmt)}"
    )


def _zsh_anchor_when(
    zs: datetime, *, geo, tz: ZoneInfo, diaspora: bool,
    time_fmt: str = "12",
) -> str:
    """Anchor + time for ז׳ שלמים, per the printed לכל-זמן booklet
    (verified EXACT on 5/5 sampled months):

      • DAY (between נץ and שקיעה) → 'יום <X> <H:MM>'
      • PRE-DAWN (before נץ)      → 'אור ליום <X> <H:MM>'
      • EVENING (after שקיעה)     → 'ליל <X> <H:MM>'
      • on a YT / Chol-HaMoed night → 'ליל <intra> <H:MM>'

    Unlike ס״ז ק״ל, ז׳ שלמים never rolls to 'כל הלילה' — a daytime
    value is shown as 'יום <X> <time>'. ``zs`` is naive local time.
    """
    d = zs.date()
    try:
        netz, shkia = sun_events_for_date(geo=geo, tz=tz, base_date=d)
        zs_a = zs.replace(tzinfo=tz)
        is_day = netz <= zs_a <= shkia
        pre_dawn = zs_a < netz
    except Exception:
        is_day = False
        pre_dawn = True
    t = _weekly_fmt_time(zs, time_fmt)
    try:
        _intra = he.intra_block_day_label(
            PHebrewDate.from_pydate(d), diaspora=diaspora)
    except Exception:
        _intra = ""
    if not is_day and _intra:
        return f"ליל {_intra} {t}"
    if is_day:
        return f"יום {_szkl_day_letter(d)} {t}"
    if pre_dawn:
        return f"אור ליום {_szkl_day_letter(d)} {t}"
    return f"ליל {_szkl_day_letter(d)} {t}"


def _strip_geresh(s: str) -> str:
    """Drop geresh/gershayim so the day-of-month column reads like the
    printed card ('כג אייר' not 'כ״ג אייר')."""
    return s.replace("\u05F4", "").replace("\u05F3", "")


def _weekly_weekday_label(d: date_cls) -> str:
    """'יום א׳'…'יום ו׳', with Friday→'עש״ק' and Shabbos→'שב״ק'
    (matching the printed KY weekly day tabs)."""
    wd = d.weekday()
    if wd == 5:
        return "שב״ק"
    if wd == 4:
        return "עש״ק"
    return he.HE_WEEKDAY.get(wd, "")


def _selichos_day1_for_rh_year(target_year: int) -> date_cls:
    """First Selichos day (the Sunday) for the High-Holiday cycle of
    ``target_year``. Reuses the EXACT canonical formula from
    ``slichos_sensor.py`` (the integration's selichos sensor) so the
    weekly card and the live sensor never disagree:

      • alef-Shabbos = the Shabbos on/before Erev Rosh Hashana
      • if RH day-1 is Monday or Tuesday → one week earlier
      • Selichos starts Motzaei that Shabbos; day-1 is the next day
        (Sunday).
    """
    tishrei1 = PHebrewDate(target_year, 7, 1).to_pydate()
    rh_wd = tishrei1.weekday()                 # Mon=0 … Sun=6
    pre_rh = tishrei1 - timedelta(days=1)       # 29 Elul
    alef_shabbos = pre_rh - timedelta(days=((pre_rh.weekday() - 5) % 7))
    if rh_wd in (0, 1):                         # Mon/Tue RH → a week earlier
        alef_shabbos -= timedelta(days=7)
    return alef_shabbos + timedelta(days=1)     # the Sunday = day-1


def _is_selichos_day1(d: date_cls) -> bool:
    """True iff ``d`` is the first day of Selichos (single-day label,
    per the KY printed card which marks only 'א׳ דסליחות')."""
    ph = PHebrewDate.from_pydate(d)
    # Selichos day-1 is always in Elul (or, for Mon/Tue RH, late Elul).
    if ph.month != 6:
        return False
    return d == _selichos_day1_for_rh_year(ph.year + 1)


def _is_yom_kippur_katan(d: date_cls) -> bool:
    """True iff the KY luach prints 'יום כפור קטן' on ``d``.

    Rule (sourced from the printed Kiryas-Yoel luach, 5786 — verified
    on כז אייר / כט סיון / כט תמוז / כט אב, and the no-tachanun
    Nisan exclusion verified on כט ניסן):

      • Yom Kippur Katan is Erev Rosh Chodesh — the 29th, i.e. the day
        before Rosh-Chodesh-day-1.
      • When that day is Shabbos it is pulled back to the preceding
        Thursday; when it is Friday, to Thursday as well.
      • NOT observed for Rosh Chodesh Tishrei (its Erev is 29 Elul =
        Erev Rosh Hashana, which carries its own label).
      • NOT observed when the preceding (= current) Hebrew month
        doesn't say tachanun:
          – Erev Rosh Chodesh Iyar (29 Nisan): Nisan is an
            all-no-tachanun month (Pesach prep / Pesach / sefirah
            lead-in) — printed KY luach OMITS YKK on כט ניסן,
            confirmed by Yoel.

    Open / flagged for Yoel's spot-check against the full printed
    luach: the classic exclusion of Erev-RC Teves (29 Kislev, during
    Chanukah — also no tachanun) is NOT yet special-cased here. See
    the WeeklyData.open_notes entry emitted by build_weekly_data.
    """
    rc1 = None
    for delta in (1, 2, 3, 4):
        cand = d + timedelta(days=delta)
        pos = he.rc_day_position_for_date(cand)
        if pos is not None and pos[0] == 1:    # cand = Rosh-Chodesh day-1
            rc1 = cand
            break
    if rc1 is None:
        return False
    ph_rc = PHebrewDate.from_pydate(rc1)
    # Exclude Rosh Chodesh Tishrei (Erev = 29 Elul = Erev Rosh Hashana).
    # Note: pyluach month numbers run Nisan=1 … Elul=6, Tishrei=7 …
    # Adar=12 (Adar II=13 in leap years). ``rc1`` is the FIRST day of
    # the RC; for a 2-day RC it is day 30 of the PRECEDING month, so
    # ``ph_rc.month`` is the ending month — not necessarily the new
    # month. The Tishrei exclusion is unambiguous here because RC
    # Tishrei is always 1 day (Elul is always 29), so ph_rc is
    # always (7, 1) = 1 Tishrei.
    if ph_rc.month == 7 and ph_rc.day == 1:
        return False
    nominal_erev = rc1 - timedelta(days=1)      # the 29th
    # Exclude Erev Rosh Chodesh Iyar (= 29 Nisan): Nisan is an
    # all-no-tachanun month, so the minhag skips YKK there — matches
    # the printed KY luach. The 29th's own Hebrew month is the
    # unambiguous identifier of the ending month (pyluach Nisan=1).
    try:
        if PHebrewDate.from_pydate(nominal_erev).month == 1:
            return False
    except Exception:
        pass
    wd = nominal_erev.weekday()                 # Mon=0 … Sat=5, Sun=6
    if wd == 5:                                 # Shabbos → Thursday
        actual = nominal_erev - timedelta(days=2)
    elif wd == 4:                               # Friday → Thursday
        actual = nominal_erev - timedelta(days=1)
    else:
        actual = nominal_erev
    return d == actual


def _weekly_dom_sublabel(
    d: date_cls, *, diaspora: bool, open_notes: list[str],
) -> str:
    """Small label printed under the Hebrew date in the יום-החודש
    column (Erev-YT / YT-day / Chol-HaMoed / Rosh-Chodesh / Chanukah /
    minor day / fast). Sourced entirely from existing halacha_events
    helpers — no new calendar rules invented here.

    Yom Kippur Katan is intentionally NOT emitted: there is no
    halacha_events helper for it and its KY observance rule (which
    Erev-RC, and the Thursday-pull when Erev-RC is Shabbos/Fri) needs
    sourcing. Flagged via ``open_notes`` so Yoel can supply the rule.
    """
    ph = PHebrewDate.from_pydate(d)

    # Erev YT (weekday before a major YT, not itself YT/Shabbos)
    ey = he.erev_yt_name(d, diaspora=diaspora)
    if ey:
        # Special case: 'ערב שביעי של פסח' fires on 20 Nisan, but
        # 20 Nisan is ALREADY a CH"M day. The printed KY card shows
        # the CH"M numbering ('ד׳ דחוה״מ'), NOT the Erev-YT label.
        # If intra_block_day_label gives a CH"M label for this day,
        # fall through to the intra-block branch below.
        _intra_chk = he.intra_block_day_label(ph, diaspora=diaspora)
        if not (_intra_chk and "דחוה״מ" in _intra_chk):
            return f"ערב {ey}"

    # Erev-YT that falls on SHABBOS. he.erev_yt_name() returns None
    # for ANY no-melacha day by design — it is the structural
    # predicate shared with the yearly luach and the sensors — so
    # ערב פסח / ערב שבועות on Shabbos come back empty. The printed
    # weekly card still labels the day: on שבת הגדול 5785, יד ניסן
    # reads ערב פסח. Weekly-only; halacha_events is left untouched.
    # Only Pesach and Shavuos can start on a Sunday (לא בד״ו ראש
    # keeps ר״ה/סוכות/יו״כ/שמ״ע off it), so only their Erev can
    # land on Shabbos — and neither 14 Nisan nor 5 Sivan is ever
    # itself a YT/חוה״מ day, so nothing else can be shadowed here.
    if d.weekday() == 5:
        _tph = PHebrewDate.from_pydate(d + timedelta(days=1))
        if (_tph.month, _tph.day) == (1, 15):
            return "ערב פסח"
        if (_tph.month, _tph.day) == (3, 6):
            return "ערב שבועות"

    # YT day / Chol HaMoed (intra-block label, e.g. 'א׳ דפסח',
    # 'ב׳ דחוה״מ פסח', 'שמיני עצרת')
    intra = he.intra_block_day_label(ph, diaspora=diaspora)
    if intra:
        # In the WEEKLY per-day cell the printed KY luach drops the
        # festival from the chol-hamoed label — 'ב׳ דחוה״מ' not
        # 'ב׳ דחוה״מ סוכות' (the chag is already named in the hero).
        # YT-day labels ('א׳ דסוכות', 'שמיני עצרת', …) keep theirs.
        # Weekly-only: halacha_events.intra_block_day_label is shared
        # with the yearly luach and is left untouched.
        if "דחוה״מ" in intra:
            for _suf in (" סוכות", " פסח"):
                if intra.endswith(_suf):
                    intra = intra[: -len(_suf)]
                    break
        return intra

    # Isru chag — the weekday right after a YT block ends
    yday = d - timedelta(days=1)
    if (
        he.is_no_melacha(yday, diaspora=diaspora)
        and not he.is_no_melacha(d, diaspora=diaspora)
        and d.weekday() != 5
        and not he.is_no_melacha(d, diaspora=diaspora)
    ):
        yph = PHebrewDate.from_pydate(yday)
        # Isru Chag follows ONLY the three regalim (Pesach, Shavuos,
        # Sukkos/Shmini Atzeres). It is NOT observed after the Tishrei
        # Yamim-Noraim YTs:
        #   • Rosh Hashana (1–2 Tishrei) — ג׳ תשרי is blank / Tzom
        #     Gedaliah territory, not אסרו חג.
        #   • Yom Kippur (10 Tishrei)    — יא תשרי has NO Isru Chag.
        # Both are ``is_yom_tov`` so the generic "day after a YT
        # block" test would wrongly tag them; suppress 1/2/10 Tishrei.
        # (Plain Shabbos already excluded by the is_yom_tov check.)
        if (
            HDateInfo(yday, diaspora=diaspora).is_yom_tov
            and not (yph.month == 7 and yph.day in (1, 2, 10))
        ):
            return "אסרו חג"

    # Chanukah day that coincides with Rosh Chodesh (ל׳ כסלו, and
    # א׳ טבת in a full-Kislev year) — the printed KY card combines
    # them, e.g. 'ו׳ דחנוכה-ר״ח', rather than the bare RC-position
    # label. Must be checked BEFORE the plain Rosh-Chodesh branch.
    # The 8th day prints as 'זאת חנוכה' (not 'ח׳ דחנוכה'); this is a
    # weekly-card display rule only — the shared
    # halacha_events.chanukah_day_label_he() is left unchanged.
    def _chanukah_lbl(dd):
        _c = he.chanukah_day_label_he(dd)
        if _c and _c.startswith("ח׳"):      # 8th day
            return "זאת חנוכה"
        return _c

    _ch_rc = _chanukah_lbl(d)
    if _ch_rc and he.rc_day_position_for_date(d) is not None:
        return f"{_ch_rc}-ר״ח"

    # Rosh Chodesh
    rc = he.rc_day_position_for_date(d)
    if rc is not None:
        pos, total = rc
        if total == 2:
            return "א׳ דראש חודש" if pos == 1 else "ב׳ דראש חודש"
        return "ראש חודש"

    # Chanukah
    ch = _chanukah_lbl(d)
    if ch:
        return ch

    # Minor days (Lag BaOmer / Pesach Sheni / Tu B'Av / Tu BiShvat /
    # Purim / Shushan Purim — the KY weekly card labels BOTH on their
    # dates regardless of locale (in diaspora 14 Adar is observed as
    # Purim; 15 Adar still gets the 'שושן פורים' label even though
    # observance is in walled Israeli cities only). The helper's
    # ``adar_last`` already handles the leap-year deferral (in a
    # leap year Shushan Purim falls on 15 Adar II, not Adar I).
    skip = set() if diaspora else {"purim"}
    for m in he.minor_days_in_range(start=d, end=d):
        if m.kind in skip or m.kind == "chanukah_night_1":
            continue
        if m.civil_date == d:
            # Tu B'Av — printed KY card leaves the day-cell BLANK
            # (no 'ט״ו באב' sub-label). Suppress weekly-only; the
            # shared minor_days_in_range still emits it for the
            # yearly luach / sensors.
            if m.kind == "tu_bav":
                continue
            # Tu B'Shvat — printed KY card spells the date out
            # ('חמשה עשר בשבט', not 'ט״ו בשבט'). Mirrors the
            # MSM/yearly-luach spelled_out convention.
            if m.kind == "tu_bishvat":
                return "חמשה עשר בשבט"
            return m.label_he

    # BeHaB minhag fasts — the Mon/Thu/Mon cycle after Rosh Chodesh
    # Cheshvan & Iyar. The printed KY card prints them in the
    # day-of-month cell as תענית שני קמא / תענית חמישי /
    # תענית שני בתרא (Yoel's spelling for the 2nd Monday — kept
    # weekly-only; the shared helper's tuple is left untouched).
    for _cyc in he.compute_behab_in_range(
        start=d - timedelta(days=9), end=d,
    ):
        if d == _cyc.fast_mon_1:
            return "תענית שני קמא"
        if d == _cyc.fast_thu:
            return "תענית חמישי"
        if d == _cyc.fast_mon_2:
            return "תענית שני בתרא"

    # Fasts (actual, post-נדחה date) — צום גדליה / עשרה בטבת /
    # תענית אסתר / י״ז בתמוז / תשעה באב. Tzom Gedaliah IS printed
    # on the KY card (ג׳ תשרי) — confirmed by Yoel.
    for f in he.fasts_in_range(start=d, end=d):
        if f.actual_date == d:
            return f.label_he

    # Selichos — day 1 only (KY prints just 'א׳ דסליחות')
    if _is_selichos_day1(d):
        return "א׳ דסליחות"

    # Yom Kippur Katan — Erev Rosh Chodesh (pulled off Shabbos/Friday
    # to Thursday). Last, so any specific label above wins.
    if _is_yom_kippur_katan(d):
        return "יום כפור קטן"

    return ""


def _weekly_resolve_week(anchor: date_cls) -> tuple[date_cls, date_cls]:
    """Return (Sunday, Shabbos) of the civil week that contains the
    upcoming Shabbos relative to ``anchor``.

    The card always runs Sunday→Shabbos; the Shabbos is the first
    Saturday on or after ``anchor``.
    """
    # Python weekday(): Mon=0 … Sat=5, Sun=6
    days_to_sat = (5 - anchor.weekday()) % 7
    shabbos = anchor + timedelta(days=days_to_sat)
    sunday = shabbos - timedelta(days=6)
    return sunday, shabbos


def build_weekly_data(
    *,
    anchor_date: date_cls,
    config: LuachConfig,
    molad_provider=None,
    block_erev_date: date_cls | None = None,
    trailing_year_sub: int | None = None,
    add_seconds: bool = False,
) -> WeeklyData:
    """Build one KY-style weekly card.

    ``anchor_date`` is any civil date in (or before) the desired week;
    the card is generated for the Sunday→Shabbos week containing the
    first Saturday on/after it.

    A single Sun→Shabbos week can produce MORE THAN ONE card when it
    contains the Erev of a Yom-Tov *and* the Erev of a Shabbos (the
    Tishrei weeks: e.g. ערב יום כפור + האזינו share one civil week;
    ערב סוכות + ערב שבת חוה״מ; הושענא רבה + בראשית). The printed KY
    luach prints one card per such Erev. ``build_weekly_cards()`` is
    the public entry that performs that split and calls this function
    once per block; callers should normally use that.

    ``block_erev_date`` scopes the hero / header-boxes / title to the
    single candle-lighting block whose Erev (``erev_before_sunset``)
    row is on that civil date. When None (legacy single-card path) the
    function behaves as before: it auto-selects the LAST in-week Erev
    block, which for a plain week is the only one.

    Data sourcing mirrors the yearly luachs exactly:
      • daily zmanim per day  →  compute_zmanim_for_date()
      • header / candle / motzei / molad / mevorchim / tekufah /
        Erev-Pesach-chametz  →  build_luach() over [Sunday, Shabbos]
        (same LuachRow / AnnotationRow stream the yearly renderers use)
    """
    week_start, week_end = _weekly_resolve_week(anchor_date)
    open_notes: list[str] = []

    # ── Reuse the shared pipeline for the header / candle / annotations ──
    # Build over a slightly padded range so an Erev row that sits on the
    # Sunday-1 (rare) or trailing motzei is still captured; we filter to
    # the week below.
    items = build_luach(
        start_date=week_start,
        end_date=week_end,
        config=config,
        molad_provider=molad_provider,
    )
    rows = [it for it in items if isinstance(it, LuachRow)]
    anns = [it for it in items if isinstance(it, AnnotationRow)]

    # ── Per-day zmanim rows (Sun..Shabbos) ──
    days: list[WeeklyDayRow] = []
    d = week_start
    while d <= week_end:
        zlist = compute_zmanim_for_date(
            geo=config.geo, tz=config.tz, base_date=d,
            tallis_offset=config.tallis_offset,
            havdalah_offset=config.havdalah_offset,
        )
        zmap = {e.label: e.dt_local for e in zlist}
        zmap_raw = {
            e.label: (e.dt_raw_local or e.dt_local) for e in zlist
        }
        col_zmanim: dict[str, datetime] = {}
        col_zmanim_raw: dict[str, datetime] = {}
        for _disp, canonical in WEEKLY_ZMAN_COLUMNS:
            if canonical in zmap:
                col_zmanim[canonical] = zmap[canonical]
                col_zmanim_raw[canonical] = zmap_raw.get(
                    canonical, zmap[canonical])
        omer = he.omer_day_for(d)
        days.append(WeeklyDayRow(
            civil_date=d,
            weekday_he=_weekly_weekday_label(d),
            hebrew_dom_he=_strip_geresh(
                he.hebrew_date_str(d, rc_emphasis=False)
            ),
            dom_sublabel_he=_weekly_dom_sublabel(
                d, diaspora=config.diaspora, open_notes=open_notes,
            ),
            omer_letters_he=(
                he.int_to_hebrew_letters(omer) if omer else ""
            ),
            zmanim=col_zmanim,
            zmanim_raw=col_zmanim_raw,
            is_shabbos=(d.weekday() == 5),
            is_yomtov=HDateInfo(d, diaspora=config.diaspora).is_yom_tov,
        ))
        d += timedelta(days=1)

    # ── Block split (one card per Erev of a YT *or* a Shabbos) ──
    # build_luach already classifies each candle row by candle_kind:
    #   • 'erev_before_sunset' → an Erev of a YT or of a Shabbos.
    #     Each such row is its OWN card.
    #   • 'between_yt' / 'motzaei_sh_to_yt' / 'trailing' → an intra-
    #     block 2nd-night-YT candle. These DO NOT get their own card;
    #     they fold into the preceding Erev card as a small box.
    # The set of cards for the week = the 'erev_before_sunset' rows,
    # in chronological order (so a YT card prints before the Shabbos
    # card of the same civil week — matches the printed KY booklet).
    _in_week = [r for r in rows if week_start <= r.civil_date <= week_end]
    _erev_rows = [
        r for r in _in_week
        if r.candle_kind == "erev_before_sunset"
    ]
    # Which block this card is for. If the caller named one, use the
    # erev row on that civil date; else default to the LAST in-week
    # erev block (legacy single-card behaviour — for a plain week the
    # only block; the Shabbos parsha is last in a mixed week).
    hero_row: LuachRow | None = None
    if block_erev_date is not None:
        hero_row = next(
            (r for r in _erev_rows if r.civil_date == block_erev_date),
            None,
        )
    if hero_row is None:
        hero_row = _erev_rows[-1] if _erev_rows else (
            _in_week[-1] if _in_week else None
        )

    # Block bounds: this card covers [hero_row.civil_date ..
    # next-erev-row.civil_date - 1] (or week_end for the last block).
    # EXCEPTION: an Erev-Shabbos-Chol-HaMoed row whose Shabbos is the
    # 3rd rest-day directly attached to THIS YT block does NOT start a
    # new block — it folds in (its candle becomes a small box on this
    # card, the Pesach-p28 case). So when scanning for the next block
    # boundary we skip such folding rows; the block then extends
    # through that Shabbos. A Chol-HaMoed-weekday-separated Erev-
    # Shabbos (the Sukkos case) is NOT skipped → it ends this block
    # and starts its own card.
    def _esc_folds(r: LuachRow) -> bool:
        # Same rule as build_weekly_cards._folds_into_yt: a Friday
        # 'ערב שבת …' row whose Shabbos is itself a YT or attached to
        # the YT block (day-before is no-melacha) folds into THIS
        # block (it does not start a new card), so extend the block
        # boundary through it. Covers ערב שבת שבועות / ערב שבת פסח
        # (YT-day-2 on Shabbos) and the attached ערב שבת חוה״מ.
        if r.civil_date.weekday() != 4:
            return False
        # NB: NO title test. The row's title is now the PARSHA when the
        # Friday is itself a YT day (printed-sheet convention), so any
        # 'ערב שבת' pre-filter would miss exactly the case this rule
        # exists for. The no-melacha adjacency below IS the rule.
        try:
            return (
                he.is_no_melacha(
                    r.civil_date, diaspora=config.diaspora)
                or (
                    he.is_no_melacha(
                        r.civil_date - timedelta(days=1),
                        diaspora=config.diaspora)
                    and he.is_chol_hamoed(
                        r.civil_date, diaspora=config.diaspora)
                )
            )
        except Exception:
            return False

    _blk_start = hero_row.civil_date if hero_row else week_start
    _nb = next(
        (r.civil_date for r in _erev_rows
         if hero_row is not None
         and r.civil_date > hero_row.civil_date
         and not _esc_folds(r)),
        None,
    )
    _blk_end = (_nb - timedelta(days=1)) if _nb is not None else week_end

    def _in_block(dt: date_cls) -> bool:
        return _blk_start <= dt <= _blk_end

    # True when this week resolves to ONE card (a plain parsha week:
    # the only erev row is its Friday, no YT-block split). The
    # parsha-anchored tekufos (תקופת טבת → שמות, תקופת תמוז →
    # its parsha) fall in the mid-week gap, BEFORE the Friday hero
    # row, so block-scoping (Fri..Sat) would drop them. On a
    # single-block week the card covers the whole Sun-Sat table, so
    # the tekufah is WEEK-scoped instead. Multi-block weeks
    # (Tishrei/Nisan, where the tekufah is YT-anchored at the erev
    # row) keep strict block-scoping unchanged — no regression.
    _single_block = (
        hero_row is not None
        and _nb is None
        and not any(
            r.civil_date < hero_row.civil_date for r in _erev_rows
        )
    )

    # Rows belonging to THIS block (the erev row + any trailing /
    # 2nd-night rows before the next erev row).
    block_rows = [
        r for r in _in_week if _in_block(r.civil_date)
    ]

    # ── Hero title + perek line ──
    title_main = ""
    title_sub = ""
    sub_stack: list[str] = []
    main_stack: list[str] = []
    # Is THIS block a Yom-Tov block? (Its erev row introduces a YT —
    # i.e. erev_yt_name fires on the erev date, OR the block contains
    # a YT day.) Scoped to the block, NOT the whole week, so the
    # Shabbos card of a Tishrei week is correctly a *Shabbos* card.
    _block_yt_days = [
        dd for dd in days
        if dd.is_yomtov and _in_block(dd.civil_date)
    ]
    erev_name_in_week = ""
    erev_civil = None
    if hero_row is not None:
        _ey = he.erev_yt_name(
            hero_row.civil_date, diaspora=config.diaspora)
        if _ey:
            erev_name_in_week = _ey
            erev_civil = hero_row.civil_date
    is_block_yt = bool(erev_name_in_week) or bool(_block_yt_days)
    if hero_row is not None:
        if erev_name_in_week:
            # Printed KY convention: the Erev Shvi'i-shel-Pesach card is
            # titled simply 'שביעי של פסח' (no 'ערב' prefix). All other
            # Erev-YT cards keep the 'ערב' prefix.
            if erev_name_in_week == "שביעי של פסח":
                title_main = erev_name_in_week
            else:
                title_main = f"ערב {erev_name_in_week}"
        else:
            title_main = hero_row.title_main_he or hero_row.title_he
        # ── Friday-is-YT + Saturday-is-parsha-Shabbos override ──
        # When the Friday row is itself a Yom-Tov (RH day 2 on Fri,
        # Simchas Torah on Fri, etc.), _build_row_title returns the
        # bare 'ערב שבת' label and the parsha name is dropped — fine
        # for the yearly luach (it's labeling a row), but the weekly
        # card needs the PARSHA in the hero (the whole table is
        # about the upcoming Shabbos). Detected by: hero is the bare
        # 'ערב שבת' string AND the following day (Saturday) is a
        # plain parsha-Shabbos (not YT, not Chol-HaMoed) for which
        # parsha_name() returns a non-empty value. Examples (תשפ״ט):
        #   • Fri Sep 22 2028 = 2 Tishrei RH d2 → Sat = האזינו
        #   • Fri Oct 13 2028 = 23 Tishrei Simchas Torah → Sat = בראשית
        # Weekly-only — the yearly luach's row label is untouched.
        if (title_main == "ערב שבת" and hero_row is not None
                and hero_row.civil_date.weekday() == 4):
            try:
                _sat = hero_row.civil_date + timedelta(days=1)
                _sat_is_yt = HDateInfo(
                    _sat, diaspora=config.diaspora,
                ).is_yom_tov
                if not _sat_is_yt:
                    _parsha = he.parsha_name(
                        _sat,
                        diaspora=config.diaspora,
                        metzora_display=config.metzora_display,
                    )
                    if _parsha:
                        title_main = _parsha
            except Exception:
                pass
        # ── Hoshana-Rabba block (Erev Shemini Atzeres) ──
        # erev_yt_name() has no Tishrei-22 rule, so the hero falls
        # through to the row's own title_main_he = 'הושענא רבה'. The
        # printed KY card titles this block 'הושענא רבה' with a
        # two-line sub-stack of the YT(s) it introduces:
        #   שמיני עצרת
        #   שמחת תורה
        # (Verified against the printed Brooklyn/KY 5786 הושענא רבה
        # card.) This is the ONLY YT block whose Erev name differs
        # from the YT name and therefore needs a derived YT sub —
        # ערב סוכות / ערב יו״כ / ערב פסח / ערב שבועות print no sub
        # (confirmed against the printed cards).
        _is_hoshana_block = False
        if not erev_name_in_week and hero_row is not None:
            try:
                _hp = PHebrewDate.from_pydate(hero_row.civil_date)
                _is_hoshana_block = (_hp.month == 7 and _hp.day == 21)
            except Exception:
                _is_hoshana_block = False
        # ── Erev-Shabbos-Chol-HaMoed block ──
        # _build_row_title emits a bare "ערב שבת חוה״מ" hero (no chag
        # named). The printed KY card names the festival and stacks it
        # on two lines:
        #   ערב שבת
        #   חוה״מ סוכות   (or  חוה״מ פסח)
        # Chol HaMoed only occurs in Sukkos (Tishrei) and Pesach
        # (Nisan), so the hero_row's Hebrew month is definitive.
        _esh_chm_fest = ""
        if title_main == "ערב שבת חוה״מ" and hero_row is not None:
            try:
                _cp = PHebrewDate.from_pydate(hero_row.civil_date)
                if _cp.month == 7:
                    _esh_chm_fest = "סוכות"
                elif _cp.month == 1:
                    _esh_chm_fest = "פסח"
            except Exception:
                _esh_chm_fest = ""
        # Perek line: Pirkei-Avos perek, prefixed with מבה״ח when this
        # week benches the coming month (a mevorchim annotation exists
        # *in this block* — scoped so the Shabbos card of a Tishrei
        # week still benches Cheshvan from the בראשית block's ann).
        perek = hero_row.pirkei_avos_he or ""
        is_mevorchim = any(
            a.kind == "mevorchim" and _in_block(a.civil_date)
            for a in anns
        )
        # Special-Shabbos name(s) for this week's Shabbos, taken from
        # the canonical specials source via halacha_events (no change
        # to specials.py — sensor-safe). The printed KY card shows the
        # bare name (no 'שבת ' prefix) before the perek, e.g.
        # 'חזון, פרק ב' / 'נחמו, פרק ג'. Standard list per Yoel's
        # decision (he will spot-check); combined precedence with
        # מבה״ח is flagged in open_notes.
        try:
            _spec_raw = he.special_shabbos_labels(
                week_end, diaspora=config.diaspora)
        except Exception:
            _spec_raw = []
        _MEV_RC_MARK = ("מברכים", "מבה", "ר״ח", "ראש חודש", "ראש חדש")

        def _spec_disp(x: str) -> str:
            # The printed luach shows most special-Shabbos names WITHOUT
            # the 'שבת ' prefix in the hero sub (e.g. 'חזון, פרק ב'',
            # 'נחמו, פרק ג'' — verified, KY 5786 דברים/ואתחנן cards).
            # שבת שובה is the exception: it prints IN FULL as
            # 'שבת שובה' (per Yoel). Keep that one verbatim.
            if "שובה" in x:
                return "שבת שובה"
            if x.startswith("שבת "):
                return x[len("שבת "):].strip()
            return x

        special_names = [
            _spec_disp(x)
            for x in _spec_raw
            if not any(mk in x for mk in _MEV_RC_MARK)
        ]
        # Eruv tavshilin: true if THIS block's erev row needs it
        # (block-scoped — a YT block's Erev, not the week's Shabbos).
        eruv = any(
            r.eruv_tavshilin for r in block_rows
        )
        sub_parts: list[str] = []
        if _is_hoshana_block:
            # YT names this block introduces, in calendar order.
            # Derived from the block's own YT day-rows (Tishrei 22
            # = שמיני עצרת, 23 = שמחת תורה in diaspora) so it stays
            # year-agnostic and sensor-safe.
            _sht: list[str] = []
            for dd in days:
                if not _in_block(dd.civil_date):
                    continue
                try:
                    _pp = PHebrewDate.from_pydate(dd.civil_date)
                except Exception:
                    continue
                if _pp.month == 7 and _pp.day == 22:
                    _sht.append("שמיני עצרת")
                elif _pp.month == 7 and _pp.day == 23 and config.diaspora:
                    _sht.append("שמחת תורה")
            if not config.diaspora and "שמיני עצרת" in _sht:
                # In E"Y ShA & ST are the same day — printed as both.
                _sht = ["שמיני עצרת", "שמחת תורה"]
            sub_parts = _sht or ["שמיני עצרת", "שמחת תורה"]
            # The printed הושענא רבה card stacks these two YT names on
            # SEPARATE lines (not comma-joined). Carry them as a stack
            # so the renderer draws one per row.
            sub_stack = list(sub_parts)
        elif _esh_chm_fest:
            # Two-line title, BOTH lines at the big hero size
            # (verified vs the printed KY card):
            #   ערב שבת
            #   חוה״מ <chag>
            title_main = "ערב שבת"          # sane single-line fallback
            main_stack = ["ערב שבת", f"חוה״מ {_esh_chm_fest}"]
        else:
            # ── 3-day rest-block hero stack (Shabbos + 2-day YT) ──
            # When the parsha-Shabbos itself is the Erev of a 2-day
            # diaspora YT (Sun-Mon), the parsha card carries ALL the
            # candle-lighting info for the whole rest block (verified
            # earlier: 4 boxes — 2 big + 2 small for ליל א׳/ליל ב׳).
            # Without surfacing the YT name in the hero, "שבועות" /
            # "פסח" / "סוכות" would never appear prominently on the
            # whole 7-day card even though the YT is the bulk of its
            # content. Stack the parsha name + YT name on two lines
            # — same mechanism the Hoshana-Rabba block uses for
            # 'שמיני עצרת' / 'שמחת תורה'. Detection mirrors the
            # small-box trigger one section down (Friday Erev row +
            # Sun & Mon both YT in the Hebrew calendar).
            _sh_yt_name: str = ""
            if hero_row is not None and hero_row.civil_date.weekday() == 4:
                try:
                    _yt1_d = hero_row.civil_date + timedelta(days=2)
                    _yt2_d = hero_row.civil_date + timedelta(days=3)
                    if (HDateInfo(
                            _yt1_d, diaspora=config.diaspora,
                        ).is_yom_tov
                        and HDateInfo(
                            _yt2_d, diaspora=config.diaspora,
                        ).is_yom_tov):
                        _nm = he.major_yt_name(
                            PHebrewDate.from_pydate(_yt1_d),
                            diaspora=config.diaspora,
                        )
                        if _nm:
                            _sh_yt_name = _nm
                except Exception:
                    pass
            if _sh_yt_name:
                # Two-line BIG hero: parsha on top, YT name below.
                # Single-line fallback keeps the parsha as the
                # primary label for any caller that ignores the
                # stack.
                main_stack = [title_main, _sh_yt_name]
                # Run through the normal Shabbos sub-parts pipeline
                # below so Rosh-Chodesh / מבה״ח / perek still print
                # (e.g. the שבועות-Sun-Mon week could also be a
                # mevorchim Shabbos or include other sub-parts).
            # ── Rosh Chodesh on Shabbos ──
            # The printed KY card shows 'ראש חודש' as a sub-hero part
            # whenever this week's Shabbos is itself Rosh Chodesh,
            # e.g. 'ראש חודש, ו׳ דחנוכה' (Mikeitz 5786, ל׳ כסלו
            # Shabbos) or 'ראש חודש, פרק ב' (Tazria-Metzora 5786,
            # א׳ אייר Shabbos). Detected via the canonical RC
            # helper, so weekly + yearly stay in sync. Placed FIRST
            # so it leads the sub line per the printed convention.
            try:
                _shab_rc = he.rc_day_position_for_date(week_end)
            except Exception:
                _shab_rc = None
            if _shab_rc is not None and \
                    "ראש חודש" not in sub_parts:
                sub_parts.append("ראש חודש")

            # ── Chanukah day on Shabbos ──
            # If this week's Shabbos is itself a day of Chanukah,
            # the printed KY card adds the Chanukah-day label
            # ('ו׳ דחנוכה' / 'ז׳ דחנוכה' / 'ח׳ דחנוכה') as a
            # sub-hero part — placed RIGHT AFTER 'ראש חודש' when
            # both apply (Mikeitz 5786). Day 8 still says 'ח׳
            # דחנוכה' here (not 'זאת חנוכה') per the printed
            # evidence; 'זאת חנוכה' applies only to the day-cell
            # sub-label.
            try:
                _shab_ch = he.chanukah_day_label_he(week_end)
            except Exception:
                _shab_ch = None
            if _shab_ch and _shab_ch not in sub_parts:
                sub_parts.append(_shab_ch)

            if is_mevorchim:
                sub_parts.append("מבה״ח")
            # 'מברכים בה״ב' — the BeHaB-fast announcement Shabbos
            # (the first non-Rosh-Chodesh Shabbos of Cheshvan / Iyar).
            # Detected via the SAME canonical helper the yearly luach
            # uses (compute_behab_in_range → mevorchim_shabbos), so
            # the weekly and yearly stay in sync. Placed right after
            # מבה״ח, before special-Shabbos names / perek.
            try:
                _is_behab_shabbos = bool(
                    he.compute_behab_in_range(
                        start=week_end, end=week_end,
                    )
                )
            except Exception:
                _is_behab_shabbos = False
            if _is_behab_shabbos and "מברכים בה״ב" not in sub_parts:
                sub_parts.append("מברכים בה״ב")
            for _sn in special_names:
                # 'שבת ר״ח' is conveyed by the day-of-month column on
                # the KY card, not the hero sub — skip the bare 'ר״ח'.
                if _sn and _sn not in ("ר״ח",) and _sn not in sub_parts:
                    sub_parts.append(_sn)
            if perek:
                # Printed KY card prints the perek WITHOUT the
                # gershayim ('פרק ב' not 'פרק ב׳' — also for
                # multi-perek like 'פרק ה-ו'). Weekly-only — the
                # shared helper still returns the gershayim form
                # for the yearly luach / sensors.
                sub_parts.append(perek.replace("׳", ""))
            if eruv:
                # Erev-YT weeks print 'עירוב תבשילין' on the sub-line
                # (verified: Erev-Shavuos / Erev-Pesach KY cards).
                sub_parts = ["עירוב תבשילין"]
        title_sub = ", ".join(sub_parts)
        # Year sub-line ('שנת תשפ״ז'): the printed luach carries the
        # incoming Hebrew year as a hero SUB only on the Erev-Rosh-
        # Hashanah card. Every page already shows the year in the top
        # community strip (handled by the renderer), so NO other card
        # — including Shabbos-Shuva / וילך / האזינו — gets a year sub.
        # (Verified against the printed Brooklyn/KY 5786 וילך &
        # האזינו cards: hero is the bare parsha, no שנת sub-line.)
        if erev_name_in_week and "ראש השנה" in erev_name_in_week \
                and erev_civil is not None:
            try:
                _yr_next = PHebrewDate.from_pydate(
                    erev_civil + timedelta(days=2)).year
                title_sub = f"שנת {he.hebrew_year_letters(_yr_next)}"
            except Exception:
                pass

        # Trailing next-year preview pages: when generating a
        # `hebrew_year` booklet, the printed luach carries the first
        # weeks of the NEXT year after the last parsha — the
        # ערב-ראש-השנה week AND the האזינו week — each sub-titled with
        # the incoming year (שנת …), OVERRIDING the normal
        # parsha / שבת-שובה sub on those pages. ``trailing_year_sub``
        # is the incoming Hebrew year; it is only ever passed for
        # those trailing pages (the service computes which weeks are
        # "next year"), so the main body and sensors are unaffected.
        if trailing_year_sub is not None:
            try:
                title_sub = (
                    f"שנת {he.hebrew_year_letters(int(trailing_year_sub))}"
                )
                # A trailing page never uses the 2-line ShA/ST stack.
                sub_stack = []
            except Exception:
                pass

    # ── Header time boxes (block-scoped) ──
    # Big boxes:
    #   • RIGHT  = הדלקת הנרות of THIS block's Erev row.
    #   • LEFT   = THIS block's final motzei, with the block's own
    #              label (מוצאי יו״ט for a YT block, מוצאי שב״ק for a
    #              Shabbos block — NOT forced from a week-wide flag).
    # Small box (only for a multi-day YT block):
    #   • ליל ב׳ דיו״ט — the block's 2nd-night-YT candle. build_luach
    #     emits it as a trailing/between-yt row inside the block:
    #       – its civil row carries the candle (2nd night = Shabbos →
    #         lit before sunset, an 'ערב שבת <YT>' row) OR
    #       – no candle on the row (2nd night is a weeknight, lit from
    #         an existing flame) → use צאת הכוכבים of the 1st YT day.
    #   A single-day YT block (יום כיפור) has NO trailing row and so
    #   gets NO small box (fixes the spurious Erev-YK 2nd box).
    # Verified against the printed Brooklyn/KY 5786 Tishrei cards:
    #   ערב יו״כ (no small) · ערב סוכות (1 small 7:40) · האזינו /
    #   ערב שבת חוה״מ / בראשית (no small) · הושענא רבה (1 small 7:29).
    boxes: list[WeeklyBox] = []
    # Candle / motzei rows that belong to THIS block only.
    blk_candle_rows = sorted(
        (r for r in block_rows if r.candle_lighting is not None),
        key=lambda r: r.civil_date,
    )
    if hero_row is not None and hero_row.candle_lighting is not None:
        # RIGHT = this block's Erev candle.
        boxes.append(WeeklyBox(
            label_he="הדלקת הנרות",
            time_str=_weekly_fmt_time(hero_row.candle_lighting, config.time_format),
            big=True,
        ))

        # LEFT = this block's final motzei (last motzei within block).
        motzei_dt = None
        motzei_label = "מוצאי שב״ק"
        for r in block_rows:
            if r.motzei is not None:
                motzei_dt = r.motzei
                if r.motzei_label_he:
                    motzei_label = r.motzei_label_he
        if is_block_yt:
            motzei_label = "מוצאי יו״ט"
            # ...unless the block RUNS INTO Shabbos (ר״ה Thu+Fri →
            # שבת שובה, שמע״צ+שמח״ת → שבת בראשית, פסח → שבת חוה״מ,
            # 2nd-day-שבועות on Shabbos). There is no havdalah until
            # Motzei Shabbos, and that single one closes BOTH — so
            # the card names both (Yoel).
            try:
                _mfy = next(
                    (dd.civil_date for dd in days
                     if dd.is_yomtov and _in_block(dd.civil_date)),
                    None,
                )
                if _mfy is not None:
                    _mse = he.no_melacha_block(
                        _mfy, diaspora=config.diaspora)
                    if _mse is not None and _mse[1].weekday() == 5:
                        motzei_label = "מוצאי יו״ט ושב״ק"
            except Exception:
                pass
        motzei_label = motzei_label.replace(
            "מוצאי יום טוב", "מוצאי יו״ט")
        # Trailing 2-day-YT case (the ערב ר״ה 5787 trailing page, and
        # any Erev card whose 2-day diaspora YT starts on Saturday so
        # the 2nd YT day falls in the FOLLOWING week). The motzei
        # loop above only saw block_rows IN the week, so it picked
        # the Friday-Erev row's motzei = Sat tzeis (= the 1st YT
        # day's tzeis) — but the real מוצאי יו״ט is Sunday's tzeis,
        # i.e. the END of the 2nd YT day. Detect via Hebrew calendar
        # (same probe as the ליל ב׳ דיו״ט small box below) and
        # recompute motzei from the ACTUAL no-melacha block end via
        # no_melacha_block(), matching the formula _build_rows uses
        # for its own trailing rows.
        if is_block_yt:
            _ft_for_motzei = next(
                (dd for dd in days
                 if dd.is_yomtov and _in_block(dd.civil_date)),
                None,
            )
            if (_ft_for_motzei is not None
                    and _ft_for_motzei.civil_date == week_end):
                try:
                    _blk_se = he.no_melacha_block(
                        _ft_for_motzei.civil_date,
                        diaspora=config.diaspora,
                    )
                    if _blk_se is not None and _blk_se[1] > week_end:
                        _real_end = _blk_se[1]
                        _sunset_real = sunset_for_date(
                            geo=config.geo, tz=config.tz, base_date=_real_end,
                        )
                        motzei_dt = _round_ceil(
                            _sunset_real + timedelta(
                                minutes=config.havdalah_offset)
                        )
                except Exception:
                    pass
        if motzei_dt is not None:
            boxes.append(WeeklyBox(
                label_he=motzei_label,
                time_str=_weekly_fmt_time(motzei_dt, config.time_format),
                big=True,
            ))

        # ── Small box: ליל ב׳ דיו״ט (multi-day YT block only) ──
        smalls: list[WeeklyBox] = []
        if is_block_yt:
            # The 2nd-night candle row = a block row, after the Erev,
            # that is itself a trailing / 2nd-day-YT row. If its 2nd
            # night is Shabbos it's an 'ערב שבת <YT>' row carrying a
            # before-sunset candle.
            second_night_row = next(
                (r for r in block_rows
                 if r is not hero_row
                 and r.candle_lighting is not None
                 and r.candle_kind in (
                     "between_yt", "motzaei_sh_to_yt")),
                None,
            )
            # First YT day of THIS block — decides what a Friday
            # candle MEANS. Friday = the block's 1st YT day → its
            # before-sunset candle IS ליל ב׳ (the 2nd YT night falls
            # on Shabbos: ערב שבת שבועות / ערב שבת פסח). Friday =
            # the block's 2nd YT day (ר״ה Thu+Fri, שמח״ת Fri) → that
            # candle is the SHABBOS lighting and ליל ב׳ is the 1st YT
            # day's tzeis (lit from a flame) — see the עש״ק box below.
            _blk_yt_dates = [
                dd.civil_date for dd in days
                if dd.is_yomtov and _in_block(dd.civil_date)
            ]
            _first_yt_date = _blk_yt_dates[0] if _blk_yt_dates else None
            erev_shabbos_yt = next(
                (r for r in blk_candle_rows
                 if r is not hero_row
                 and r.civil_date.weekday() == 4
                 and "חוה" not in r.title_main_he
                 and "חול" not in r.title_main_he
                 and _first_yt_date is not None
                 and r.civil_date == _first_yt_date),
                None,
            )
            if erev_shabbos_yt is not None:
                smalls.append(WeeklyBox(
                    label_he="הדלה״נ (עש״ק) ליל ב׳ דיו״ט",
                    time_str=_weekly_fmt_time(
                        erev_shabbos_yt.candle_lighting, config.time_format),
                    big=False,
                ))
            elif second_night_row is not None:
                smalls.append(WeeklyBox(
                    label_he="הדלה״נ ליל ב׳ דיו״ט",
                    time_str=_weekly_fmt_time(
                        second_night_row.candle_lighting, config.time_format),
                    big=False,
                ))
            else:
                # 2nd night is a weeknight with no explicit candle row
                # → only fabricate from צאה״כ of the 1st YT day if the
                # block actually HAS a 2nd YT day (2-day YT). A 1-day
                # YT (יום כיפור / שמיני עצרת in E"Y) gets no small box.
                _has_2nd_yt_day = False
                _first_yt = None
                for dd in days:
                    if not _in_block(dd.civil_date):
                        continue
                    if dd.is_yomtov:
                        if _first_yt is None:
                            _first_yt = dd
                        else:
                            _has_2nd_yt_day = True
                # Edge case: the 2nd YT day can fall OUTSIDE this
                # week. Specifically, when a 2-day diaspora YT starts
                # on Saturday (RH 5787, Pesach/Sukkos/Shavuos cycles
                # that land Sat-Sun), the Erev card's week ends on
                # the 1st YT Shabbos and the 2nd YT Sunday is in the
                # next week — so the `days` loop above sees only one
                # YT day and `_has_2nd_yt_day` stays False, even
                # though halachically there IS a 2nd YT day. Detect
                # this via Hebrew calendar: if the 1st YT day sits at
                # week_end, check the Hebrew date of the FOLLOWING
                # day for YT status. (This is the printed KY ערב ר״ה
                # 5787 case — verified by Yoel; the printed card
                # carries the ליל ב׳ דיו״ט small box.)
                if (not _has_2nd_yt_day
                        and _first_yt is not None
                        and _first_yt.civil_date == week_end):
                    try:
                        _next_civil = week_end + timedelta(days=1)
                        if HDateInfo(
                            _next_civil,
                            diaspora=config.diaspora,
                        ).is_yom_tov:
                            _has_2nd_yt_day = True
                    except Exception:
                        pass
                if _has_2nd_yt_day and _first_yt is not None:
                    tz_dt = _first_yt.zmanim.get("צאת הכוכבים")
                    if tz_dt is not None:
                        smalls.append(WeeklyBox(
                            label_he="הדלה״נ ליל ב׳ דיו״ט",
                            time_str=_weekly_fmt_time(tz_dt, config.time_format),
                            big=False,
                        ))
                        open_notes.append(
                            "2nd-night YT candle taken as צאת "
                            "הכוכבים of the 1st YT day — verify "
                            "the KY convention."
                        )
            # ── Folded Erev-Shabbos-Chol-HaMoed candle ──
            # When the Chol-HaMoed Shabbos is the 3rd rest-day
            # directly attached to this YT block (no Chol-HaMoed
            # weekday gap — the Pesach-p28 case), build_weekly_cards
            # does NOT give it its own card; instead its Erev candle
            # is absorbed here as a 2nd small box, AFTER ליל ב׳ דיו״ט
            # (verified order, printed KY 5786 Erev-Pesach card).
            esc_row = next(
                (r for r in block_rows
                 if r is not hero_row
                 and r.candle_lighting is not None
                 and r.civil_date.weekday() == 4
                 and ((r.title_main_he or "").startswith("ערב שבת חוה")
                      or (r.title_main_he or "").startswith(
                          "ערב שבת חול"))),
                None,
            )
            if esc_row is not None:
                smalls.append(WeeklyBox(
                    label_he="הדלה״נ עש״ק חוה״מ",
                    time_str=_weekly_fmt_time(
                        esc_row.candle_lighting, config.time_format),
                    big=False,
                ))

            # A 2-day YT whose SECOND day is Friday, running into a
            # plain Shabbos (ר״ה Thu+Fri → שבת שובה; שמיני עצרת +
            # שמח״ת Thu+Fri → שבת בראשית). ליל ב׳ is already boxed
            # above (1st YT day's tzeis); the Friday candle is the
            # SHABBOS lighting and gets its own box, labelled with the
            # coming Shabbos's parsha when it has one.
            esp_row = next(
                (r for r in block_rows
                 if r is not hero_row
                 and r.candle_lighting is not None
                 and r.civil_date.weekday() == 4
                 and "חוה" not in (r.title_main_he or "")
                 and "חול" not in (r.title_main_he or "")
                 and _first_yt_date is not None
                 and r.civil_date != _first_yt_date),
                None,
            )
            if esp_row is not None:
                _esp_lbl = "הדלה״נ עש״ק"
                try:
                    _esp_parsha = he.parsha_name(
                        esp_row.civil_date + timedelta(days=1),
                        diaspora=config.diaspora,
                        metzora_display=config.metzora_display,
                    )
                except Exception:
                    _esp_parsha = None
                if _esp_parsha:
                    _esp_lbl = f"{_esp_lbl} {_esp_parsha}"
                smalls.append(WeeklyBox(
                    label_he=_esp_lbl,
                    time_str=_weekly_fmt_time(
                        esp_row.candle_lighting, config.time_format),
                    big=False,
                ))
        # ── 3-day rest-block: Shabbos + 2-day YT (Sun-Mon) ──
        # When Erev YT falls on Shabbos (e.g. Shavuos 5789: Sat May
        # 19 Erev → Sun May 20 day 1 → Mon May 21 day 2; same pattern
        # for any Pesach/Sukkos 2-day YT starting on a Sunday after a
        # Shabbos with no weekday gap), the entire weekend is one
        # no-melacha block, but the Friday-Erev-Shabbos row's own
        # `is_block_yt` is FALSE (its hero is the parsha-Shabbos, not
        # a YT). The big-box pair is already correct: Friday's
        # candle-lighting + Monday-night motzei (the existing
        # no_melacha_block end logic carries Monday's tzeis as the
        # row's motzei). What's MISSING is the two intra-block
        # candles — Sat-night (1st YT-night, lit from tzeis Shabbos)
        # and Sun-night (ליל ב׳, lit from tzeis day-1 YT). Both are
        # taken from צאת הכוכבים of their respective preceding day
        # (the existing הדלה״נ ליל ב׳ convention) — no candle_offset
        # subtraction (this is the "light from an existing flame"
        # case, not a pre-sunset lighting). The ליל א׳ candle gets
        # an explicit 'במוצש״ק' label so it isn't confused with the
        # main Friday-night Shabbos candle above.
        if (hero_row is not None
                and hero_row.candle_kind == "erev_before_sunset"
                and hero_row.civil_date.weekday() == 4  # Friday
                and not is_block_yt):
            try:
                _shab = hero_row.civil_date + timedelta(days=1)  # Sat
                _yt1 = hero_row.civil_date + timedelta(days=2)   # Sun
                _yt2 = hero_row.civil_date + timedelta(days=3)   # Mon
                # Is the Shabbos an Erev YT? he.erev_yt_name() returns
                # None on Shabbos (its purpose is to label weekday-Erev
                # cells, not Shabbos), so probe the Hebrew calendar
                # directly: Shabbos is the Erev of YT iff the FOLLOWING
                # day is YT. Combined with `not is_block_yt`, this
                # specifically catches the Erev-Shabbos card whose
                # Shabbos opens a YT block.
                _yt1_is_yt = HDateInfo(
                    _yt1, diaspora=config.diaspora,
                ).is_yom_tov
                _yt2_is_yt = HDateInfo(
                    _yt2, diaspora=config.diaspora,
                ).is_yom_tov
                _is_shab_erev_yt = _yt1_is_yt
                if _is_shab_erev_yt and _yt1_is_yt and _yt2_is_yt:
                    # Sat-night candle = tzeis Shabbos (no offset
                    # subtraction — it's a "transfer from existing
                    # flame" lighting, same convention used for
                    # ליל ב׳ דיו״ט).
                    _shab_sunset = sunset_for_date(
                        geo=config.geo, tz=config.tz, base_date=_shab,
                    )
                    _shab_tzeis = _round_ceil(
                        _shab_sunset + timedelta(
                            minutes=config.havdalah_offset)
                    )
                    smalls.append(WeeklyBox(
                        label_he="הדלה״נ במוצש״ק ליל א׳ דיו״ט",
                        time_str=_weekly_fmt_time(_shab_tzeis, config.time_format),
                        big=False,
                    ))
                    # Sun-night candle = tzeis day-1 YT.
                    _yt1_sunset = sunset_for_date(
                        geo=config.geo, tz=config.tz, base_date=_yt1,
                    )
                    _yt1_tzeis = _round_ceil(
                        _yt1_sunset + timedelta(
                            minutes=config.havdalah_offset)
                    )
                    smalls.append(WeeklyBox(
                        label_he="הדלה״נ ליל ב׳ דיו״ט",
                        time_str=_weekly_fmt_time(_yt1_tzeis, config.time_format),
                        big=False,
                    ))
                    open_notes.append(
                        "3-day rest-block (Shabbos+2-day YT): two "
                        "small candle boxes fabricated from תzeis "
                        "Shabbos + tzeis day-1 YT. Verify against "
                        "the printed KY luach when available."
                    )
            except Exception:
                pass
        boxes.extend(smalls)

    # ── Info band (gradient strip between boxes & table) ──
    # Holds molad / mevorchim / tekufah, then ז׳ שלמים, then the
    # kiddush-levana / chatzos line. The renderer centres these.
    # The Erev-Pesach chametz *deadline* line goes to its own black
    # box (chametz_lines); the chatzos+KL line goes to the info band.
    #
    # SCOPING (verified against the printed Brooklyn/KY 5786 cards):
    #   • molad / mevorchim / tekufah / hashala / chametz → BLOCK-
    #     scoped: they print on the card of the block whose dates
    #     contain them. (The בראשית card carries 'מולד מרחשון…';
    #     the same-week הושענא רבה card carries NO molad line.)
    #   • ז׳ שלמים and the general ס״ז-קידוש-לבנה → WEEK-scoped: they
    #     print on EVERY card of the week. (Both the ערב יו״כ card
    #     and the same-week האזינו card show 'ז׳ שלמים: יום ב׳ 1:10'.)
    info_molad: list[str] = []
    info_kl: list[str] = []
    chametz_lines: list[str] = []
    kl_from_pesach = False
    for a in sorted(anns, key=lambda x: x.civil_date):
        _ok = _in_block(a.civil_date)
        if (
            not _ok
            and a.kind == "tekufah"
            and _single_block
            and week_start <= a.civil_date <= week_end
        ):
            # Parsha-anchored tekufah on a plain single-card week —
            # week-scope it so it lands on the (only) parsha card.
            _ok = True
        if not _ok:
            continue
        if a.kind == "erev_pesach_chametz":
            if "אכילת חמץ" in a.text_he:
                chametz_lines.append(a.text_he)          # → black box
            else:
                # Keep ONLY the kiddush-levana clause; the KY weekly
                # card does not carry the 'זמן חצות …' note here.
                parts = [p.strip() for p in a.text_he.split(" - ")]
                kl = next(
                    (p for p in parts if "קידוש לבנה" in p), None)
                if kl:
                    info_kl.append(kl)
                    kl_from_pesach = True
            continue
        if a.kind in ("mevorchim", "molad_tishrei", "tekufah",
                      "hashala"):
            _txt = a.text_he
            if a.kind == "tekufah":
                # The shared _annotations_tekufah() emits the SF
                # yearly-luach form for a YT-day anchor, e.g.
                #   'תקופת תשרי: יום ג׳ סוכות, ט״ו תשרי בשעה …'
                # The printed Brooklyn/KY WEEKLY card instead anchors
                # such a tekufah by the YT-day ordinal only (the same
                # convention it uses for Pesach / שביעי של פסח), e.g.
                #   'תקופת תשרי: א׳ דסוכות בשעה …'
                # Reformat weekly-side only (the yearly luachs keep the
                # SF form — _annotations_tekufah is untouched). The
                # tekufah civil_date is the day BEFORE the tekufah day,
                # so the YT-day is a.civil_date + 1.
                try:
                    _tk_day = a.civil_date + timedelta(days=1)
                    _tk_ph = PHebrewDate.from_pydate(_tk_day)
                    _tk_intra = he.intra_block_day_label(
                        _tk_ph, diaspora=config.diaspora)
                    if _tk_intra and ":" in _txt:
                        _label, _rest = _txt.split(":", 1)
                        # Keep only the time-of-day clause
                        # ('בשעה … באשה״ב' / 'אחה״צ' / 'בבוקר'); drop
                        # the SF weekday+hebrew-date anchor entirely.
                        _m = re.search(r"בשעה.*$", _rest)
                        _time_clause = (
                            _m.group(0).strip() if _m else _rest.strip()
                        )
                        _txt = (
                            f"{_label.strip()}: {_tk_intra} "
                            f"{_time_clause}"
                        )
                except Exception:
                    pass
            info_molad.append(_txt)

    # ז׳ שלמים / general סוף-זמן-קידוש-לבנה for the week's Hebrew
    # month(s). (Flagged: KY phrasing + which-week rule not formally
    # sourced — emitted when the molad-derived date lands in-week,
    # which matches the printed Erev-Shavuos / Erev-Pesach cards.)
    def _wd_he(dd: date_cls) -> str:
        if dd.weekday() == 5:
            return "יום שב״ק"
        return he.HE_WEEKDAY.get(dd.weekday(), "")

    def _day_anchor_he(dd: date_cls, *, night: bool) -> str:
        """Day anchor for a ז׳-שלמים / ס״ז-קידוש-לבנה line.

        On a YT day the printed Brooklyn/KY weekly card anchors by the
        YT-day ordinal (e.g. 'ליל א׳ דסוכות' for KL, 'א׳ דסוכות' for
        ז׳ שלמים) — same convention it uses for Pesach. On an ordinary
        day it uses the plain weekday ('יום ג׳' / 'יום שב״ק').
        """
        try:
            _ph = PHebrewDate.from_pydate(dd)
            _intra = he.intra_block_day_label(
                _ph, diaspora=config.diaspora)
        except Exception:
            _intra = ""
        if _intra:
            return f"ליל {_intra}" if night else _intra
        return _wd_he(dd)

    def _kl_line(sk: datetime) -> str:
        """Weekly 'ס״ז קידוש לבנה' line, via the shared night/day
        formatter (verified vs printed Table-3, 12/12 months)."""
        return (
            "ס״ז קידוש לבנה: "
            + _szkl_anchor_when(
                sk, geo=config.geo, tz=config.tz,
                diaspora=config.diaspora,
                time_fmt=config.time_format,
            )
        )
    seen_hm: set[tuple[int, int]] = set()
    info_shleimim: list[str] = []
    for dd in days:
        ph = PHebrewDate.from_pydate(dd.civil_date)
        key = (ph.year, ph.month)
        if key in seen_hm:
            continue
        seen_hm.add(key)
        try:
            zs = zayin_shleimim_local(ph.year, ph.month, config.tz)
            if week_start <= zs.date() <= week_end:
                info_shleimim.append(
                    "ז׳ שלמים: "
                    + _zsh_anchor_when(
                        zs, geo=config.geo, tz=config.tz,
                        diaspora=config.diaspora,
                        time_fmt=config.time_format,
                    )
                )
        except Exception:
            pass
        if not kl_from_pesach:
            try:
                sk = sof_zman_kiddush_levana_rama_local(
                    ph.year, ph.month, config.tz)
                if week_start <= sk.date() <= week_end:
                    info_kl.append(_kl_line(sk))
            except Exception:
                pass

    info_lines: list[str] = info_molad + info_shleimim + info_kl

    # ── DST (clock-change) note on the ribbon ──
    # The printed weekly luach carries a Yiddish clock-change note on
    # the card of the week whose MOTZAEI-SHABBOS is immediately before
    # the change (i.e. the DST transition falls on the Sunday right
    # after this card's Shabbos = week_end + 1 day). Spring-forward
    # and fall-back have different wording. The note is appended to
    # the ribbon joined with ' • ' to whatever else is on it (molad /
    # ז׳ שלמים / KL), or stands alone if the ribbon is otherwise empty.
    # DST dates are computed from the configured tz (not hardcoded),
    # via the same canonical he.dst_changes_in_range used by the
    # yearly luachs — only the wording differs here.
    try:
        _dst_sunday = week_end + timedelta(days=1)
        for _chg in he.dst_changes_in_range(
            start=_dst_sunday, end=_dst_sunday, tz=config.tz,
        ):
            if _chg.kind == "dst_start":
                _dst_txt = (
                    'דעם מוצש"ק רוקט מען פאראויס '
                    "דעם זייגער מיט איין שעה"
                )
            else:
                _dst_txt = (
                    'דעם מוצש"ק רוקט מען צוריק '
                    "דעם זייגער מיט איין שעה"
                )
            if info_lines:
                info_lines[-1] = f"{info_lines[-1]} {INFO_SEP} {_dst_txt}"
            else:
                info_lines = [_dst_txt]
    except Exception:
        pass

    # ── Spot-check flags (Yoel verifies against the full printed luach) ──
    if any(r.dom_sublabel_he == "יום כפור קטן" for r in days):
        open_notes.append(
            "יום כפור קטן printed every Erev-RC (decision: match the "
            "print). The classic exclusions of Erev-RC Cheshvan "
            "(29 תשרי) and Erev-RC Teves (29 כסלו / Chanukah) are NOT "
            "special-cased — verify those two weeks against the "
            "printed luach."
        )
    if title_sub and "מבה״ח" in title_sub and any(
        x not in ("מבה״ח",) and "פרק" not in x
        for x in title_sub.split(", ")
    ):
        open_notes.append(
            "Week is both מבה״ח and a special-Shabbos — sub-line "
            "ordering/precedence (מבה״ח vs the special-Shabbos name) "
            "is a guess; verify against the printed card."
        )
    # Tishrei fast coverage: צום גדליה IS now printed on the KY
    # card (ג׳/ד׳ תשרי) per Yoel. עשרה בטבת / תענית אסתר are also
    # emitted from fasts_in_range — spot-check those two against
    # the printed card.
    if any(
        PHebrewDate.from_pydate(r.civil_date).month == 7
        and PHebrewDate.from_pydate(r.civil_date).day in (3, 4)
        for r in days
    ):
        open_notes.append(
            "צום גדליה now printed (confirmed). Spot-check KY's "
            "handling of עשרה בטבת / תענית אסתר as well."
        )

    _title_is_parsha = False
    try:
        _title_is_parsha = bool(title_main) and title_main == \
            he.parsha_name(
                week_end, diaspora=config.diaspora,
                metzora_display=config.metzora_display)
    except Exception:
        _title_is_parsha = False

    # Parsha of the Shabbos that CLOSES a YT block (see the field's
    # doc above). Skipped when the hero already names the parsha.
    _block_parsha = ""
    try:
        if is_block_yt and not _title_is_parsha:
            _bfy = next(
                (dd.civil_date for dd in days
                 if dd.is_yomtov and _in_block(dd.civil_date)),
                None,
            )
            if _bfy is not None:
                _bse = he.no_melacha_block(
                    _bfy, diaspora=config.diaspora)
                if _bse is not None and _bse[1].weekday() == 5:
                    _block_parsha = he.parsha_name(
                        _bse[1], diaspora=config.diaspora,
                        metzora_display=config.metzora_display,
                    ) or ""
    except Exception:
        _block_parsha = ""

    return WeeklyData(
        week_start=week_start,
        week_end=week_end,
        title_main_he=title_main,
        title_sub_he=title_sub,
        title_sub_stack_he=sub_stack,
        title_main_stack_he=main_stack,
        boxes=boxes,
        info_lines_he=info_lines,
        chametz_lines_he=chametz_lines,
        days=days,
        diaspora=config.diaspora,
        open_notes=open_notes,
        add_seconds=add_seconds,
        time_format=config.time_format,
        title_is_parsha=_title_is_parsha,
        block_parsha_he=_block_parsha,
    )


def build_weekly_cards(
    *,
    anchor_date: date_cls,
    config: LuachConfig,
    molad_provider=None,
    trailing_year_sub: int | None = None,
    add_seconds: bool = False,
) -> list[WeeklyData]:
    """Build every KY card for the Sun→Shabbos week of ``anchor_date``.

    A plain week → a single card. A Tishrei-type week that contains
    the Erev of a Yom-Tov *and* the Erev of a Shabbos → multiple
    cards, one per Erev (the rule confirmed against the printed
    Brooklyn/KY 5786 booklet: e.g. ערב יום כפור + האזינו; ערב סוכות +
    ערב שבת חוה״מ; הושענא רבה + בראשית). Cards are returned in
    chronological Erev order, i.e. the YT card prints BEFORE the
    same-week Shabbos card — matching the printed booklet order.

    This is the entry point callers should use. It discovers the
    block boundaries from the shared build_luach stream (every
    ``candle_kind == 'erev_before_sunset'`` row is its own card; the
    intra-block 2nd-night-YT rows fold into their block's card as a
    small box) and delegates each block to build_weekly_data().
    """
    week_start, week_end = _weekly_resolve_week(anchor_date)
    items = build_luach(
        start_date=week_start,
        end_date=week_end,
        config=config,
        molad_provider=molad_provider,
    )
    rows_iw = [
        it for it in items
        if isinstance(it, LuachRow)
        and week_start <= it.civil_date <= week_end
    ]
    erev_rows = [
        r for r in rows_iw if r.candle_kind == "erev_before_sunset"
    ]

    def _folds_into_yt(r: LuachRow) -> bool:
        # A Friday Erev row whose Shabbos is itself a Yom-Tov, OR is a
        # rest-day directly ATTACHED to a YT block (the day before the
        # row is itself a no-melacha YT/Shabbos day, with no
        # intervening Chol-HaMoed weekday), is NOT its own card — it
        # folds into the preceding YT card as a small box. This is the
        # single year-agnostic rule Yoel confirmed:
        #   • 2nd-day-Shavuos-on-Shabbos  (ערב שבת שבועות)  → fold
        #   • 2nd-day-Pesach-on-Shabbos   (ערב שבת פסח)     → fold
        #   • Chol-HaMoed Shabbos attached to the YT block
        #     (Pesach p28: ערב שבת חוה״מ, no weekday gap)    → fold
        #   • Chol-HaMoed Shabbos separated by a Chol-HaMoed weekday
        #     (Sukkos img5: ערב שבת חוה״מ, weekday gap)       → own card
        # Detected purely from the title being an 'ערב שבת …' Friday
        # row AND the calendar's no-melacha adjacency (the day before
        # this row is a YT/no-melacha day). A plain parsha Erev-Shabbos
        # (no YT adjacency) is unaffected → still its own card.
        if r.civil_date.weekday() != 4:           # must be Friday
            return False
        # NB: NO title test. The row's title is now the PARSHA when the
        # Friday is itself a YT day (printed-sheet convention), so any
        # 'ערב שבת' pre-filter would miss exactly the case this rule
        # exists for. The no-melacha adjacency below IS the rule.
        # Fold when the Shabbos is itself a Yom-Tov day (the Friday
        # row is itself a no-melacha YT day — 2nd-day-Shavuos /
        # 2nd-day-Pesach on Shabbos), OR is directly attached to the
        # YT block (the day before the row is a no-melacha YT/Shabbos
        # day — Pesach-p28 ערב שבת חוה״מ with no weekday gap). It is
        # its OWN card only when BOTH the Friday and its day-before
        # are non-YT — a true Chol-HaMoed-weekday gap (Sukkos img5).
        try:
            return (
                he.is_no_melacha(
                    r.civil_date, diaspora=config.diaspora)
                or (
                    he.is_no_melacha(
                        r.civil_date - timedelta(days=1),
                        diaspora=config.diaspora)
                    and he.is_chol_hamoed(
                        r.civil_date, diaspora=config.diaspora)
                )
            )
        except Exception:
            return False

    erev_dates = sorted(
        r.civil_date for r in erev_rows if not _folds_into_yt(r)
    )
    if not erev_dates:
        # No Erev rows at all (degenerate) — fall back to the legacy
        # single-card behaviour so callers never get an empty list.
        return [build_weekly_data(
            anchor_date=anchor_date, config=config,
            molad_provider=molad_provider,
            trailing_year_sub=trailing_year_sub,
            add_seconds=add_seconds,
        )]
    cards: list[WeeklyData] = []
    for ed in erev_dates:
        cards.append(build_weekly_data(
            anchor_date=anchor_date, config=config,
            molad_provider=molad_provider,
            block_erev_date=ed,
            trailing_year_sub=trailing_year_sub,
            add_seconds=add_seconds,
        ))
    return cards
