"""
custom_components/yidcal/yidcal_lib/luach_weekly_yidcal_pdf.py

Renders the "Weekly" luach in the NEW portrait design as a PDF.

Visual fidelity strategy (same pipeline as luach_weekly_pdf.py)
---------------------------------------------------------------
The page chrome (the rounded outer card, the two ornate header boxes,
the hero panel, the mid ribbon/panels, the 14-column table grid with
its baked header labels and zebra rows, the day-letter column frames)
is the original Adobe-Illustrator artwork with every week-specific
sample glyph removed. Two blank variants ship as pre-rasterised
300-dpi assets:

  * ``weekly_yidcal_template_regular`` — full-width molad ribbon, no small box
  * ``weekly_yidcal_template_yt1``     — one extra candle box + right info panel

(The 2-extra-candle / Erev-Pesach variant is pending its corrected SVG
export; see ``_YT2_PENDING`` below for the interim fallback.)

Everything week-specific is drawn live by this renderer at the exact
coordinates the designer placed the originals (measured from the
design mock PDFs, which carry a live text layer). Text uses the
bundled Frank Ruehl CLM serif — the original BAFranknatan face is
outlined in the artwork and not redistributable.

The design is a full-bleed Letter-PORTRAIT page drawn 1:1 in the SVG's
own point space (viewBox 0 0 612 792 == the page), so unlike the
legacy card renderer there is no fit/scale affine — the _X/_Y/_S
helpers are kept as identity pass-throughs purely so the two weekly
renderers read the same.

Input is the same ``WeeklyData`` from ``luach_data.build_weekly_data()``
the legacy weekly renderer consumes — no data-model changes.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from fpdf import FPDF
from fpdf import drawing as _fdraw
from PIL import Image

from .luach_data import WeeklyData, WEEKLY_ZMAN_COLUMNS, WEEKLY_BOXED_COLUMN
from .luach_pdf_common import (
    register_fonts, bidi,
    FONT_FAMILY, INFO_SEP, _fonts_dir,
)


def _assets_dir() -> Path:
    return Path(__file__).parent / "assets"


@lru_cache(maxsize=3)
def _page_image(tpl: str) -> Image.Image:
    """Full-page 300-dpi template raster (drawn 1:1 over the page)."""
    png = _assets_dir() / f"weekly_yidcal_template_{tpl}.png"
    return Image.open(png).convert("RGB")


def template_available() -> bool:
    """True iff the Weekly-YidCal template rasters AND design fonts
    are all present (the yt2 raster is optional — Erev-Pesach weeks
    fall back to the yt1 layout without it)."""
    a, f = _assets_dir(), _fonts_dir()
    need = [
        a / "weekly_yidcal_template_regular.png",
        a / "weekly_yidcal_template_yt1.png",
        f / _HEB_FILE, f / _GRID_FILE, f / _LAT_FILE,
        f / _MARK_FILE, f / _MONO_FILE,
    ]
    return all(p.exists() for p in need)


def _tpl_exists(tpl: str) -> bool:
    return (_assets_dir() / f"weekly_yidcal_template_{tpl}.png").exists()


# ── Exact SVG-space anchors (points; viewBox 0 0 612 792 == page) ──
#
# All values below are measured from (a) the clean template SVGs'
# own vector geometry (panels, pills, grid lines, zebra bands) and
# (b) the designer's mock PDFs' live text layer (font sizes and text
# anchor positions). Where the mock's hand-placed samples drift off
# the uniform chrome grid (the sample table rows drift up to ~3 pt),
# the CHROME geometry wins — rows/pills anchor on the band centres.

# Top strip — community / year line, centred on its pill
# (pill rect 155.79,34.29 → 456.19,62.45).
_STRIP_CX, _STRIP_CY = 306.0, 48.4
_STRIP_BASE = 55.7               # mock text baseline
_STRIP_MW = 288.0

# Big boxes (top-left, stacked): light time-pills + label zones.
#   candle pill  38.3,85.9  → 230.4,162.2
#   motzei pill  38.3,186.8 → 230.4,263.2
#   label zone   x 230.4 → 363.8 (both boxes)
_BIG_TIME_CX = 137.45            # mock span center (optical, not pill c.)
_BIG_CANDLE_CY = 124.05
_BIG_MOTZEI_CY = 225.0
_BIG_TIME_SIZE = 73.0            # mock-exact: 73 pt Cascadia Code
_BIG_TIME_MW = 178.0
_BIG_LABEL_CX = 297.1
_BIG_LABEL_SIZE = 40.0           # mock-exact (BAFranknatan)
_BIG_LABEL_PITCH = 31.0          # mock baselines 123.4 → 154.4
_BIG_LABEL_BASE0 = 123.4
_BIG_LABEL_MW = 126.0

# Hero panel (right): 386.2,77.1 → 578.2,271.9.
_HERO_CX = 482.2
_HERO_MID = 174.5
_HERO_MW = 178.0
_HERO_SMALL_SIZE = 29.0          # the 'פרשת' / 'ערב' pre-line
_HERO_BIG_SIZE = 52.0
_HERO_SUB_SIZE = 29.0            # mock-exact
_HERO_SMALL_H, _HERO_BIG_H, _HERO_SUB_H = 24.0, 40.0, 22.0
# Fixed BASELINES lifted from the mocks, keyed by title family:
#   parsha weeks (pre-line 'פרשת'):  150.2 → 204.2 (54-pt gap),
#     further lines continue at 42-pt pitch (246.2, …)
#   erev/YT weeks:                    138.4, 180.4, 222.4, … (42-pt)
#   single line (no pre, e.g. הושענא רבה): 195.0 (optical middle)
# The hero group (title + sub lines) is CENTRED as one block on the
# panel's vertical middle (174.5 — the design box is 77.1..271.9).
# Line rhythm keeps the mock's pitches: 54 after a 'פרשת' pre-line,
# 42 everywhere else. Optical extents use BAFranknatan's letter
# band (top 0.57 em, descender 0.18 em).
_HERO_PITCH = 42.0
_HERO_PARSHA_PITCH = 54.0
_HERO_ASC_EM, _HERO_DESC_EM = 0.57, 0.18

# Mid band.
# regular: one full-width ribbon panel 39.1,285.1 → 572.9,383.0
_INFO_FULL_CX, _INFO_FULL_MID = 306.0, 334.0
_INFO_FULL_MW = 500.0
_INFO_FULL_SIZE = 24.0           # mock-exact
_INFO_FULL_PITCH = 25.0
_INFO_BASE_ONE = 339.8           # mock single molad line baseline
_INFO_BASE_TWO = (327.3, 352.3)  # pair centred on the same anchor
# yt1: small candle box (pill 38.3,291.7 → 230.4,323.7; label zone
# x 230.4 → 363.8 within outer 284.8 → 330.5) + right info panel
# 386.2,285.1 → 578.2,383.0.
_SM_TIME_CX, _SM_TIME_CY = 134.35, 309.3   # CY: mock digit baseline
_SM_TIME_SIZE = 35.0             # mock: 35 pt Acumin (Heebo stand-in)
_SM_TIME_MW = 178.0
_SM_LABEL_CX = 297.1
_SM_LABEL_BASE0 = 305.9          # mock baselines 305.9 / 324.9
_SM_LABEL_SIZE = 22.0            # mock-exact
_SM_LABEL_PITCH = 19.0
_SM_LABEL_MW = 126.0

# ── שהחיינו marker (service option; Weekly-YidCal only) ──
# Printed small UNDER a candle box's label. The design leaves the label
# sitting low in its panel (23.5 pt of air above, 9.4 pt below), so when
# a marker is present the label group is LIFTED and the marker drops into
# the freed space. Un-marked boxes keep the mock's baselines EXACTLY.
_SHE_SIZE_BIG, _SHE_LIFT_BIG, _SHE_GAP_BIG = 13.0, 7.0, 18.0
_SHE_SIZE_SM = 9.0
_SHE_CLEAR = 2.0          # min clear air between label ink and marker ink
# A small box's PANEL is only 45.6 pt tall (mock: 284.9→330.5, and the
# second slot 52.1 lower), and the design's 22-pt two-line label already
# fills 35.5 of that. So a marked small box SHRINKS its label until label
# + marker fit the panel, then centres the pair inside it — rather than
# lifting, which pushed the marker out through the panel floor.
_SM_PANEL_TOP, _SM_PANEL_H, _SM_PANEL_DY = 285.0, 45.6, 52.1
_SM_PANEL_PAD = 2.2
# BAFranknatan's TALLEST letter is ל at 0.832 em — and every candle label
# ('הדלה״נ …') has one. _P2_ASC_EM (0.57) is the typical-letter value and
# badly under-measures these, so the containment fit uses the real max.
_HEB_ASC_MAX, _HEB_DESC_MAX = 0.832, 0.185
_PANEL_CX, _PANEL_MID = 482.2, 334.05
_PANEL_MW = 178.0
_PANEL_SIZE = 16.0               # mock announcement: 21 pt over 3 lines
_PANEL_PITCH = 20.0
# yt2 (2-small-box Erev-Pesach template; chrome rasterised from the
# designer's pesach mock PDF). Box 2 sits one full slot below box 1
# (pill (38.3,343.9)-(230.4,375.9)); the mock shifts the DIGITS by
# 52.0 pt but the LABEL stack by only 49.8 pt (designer nudge).
_SM2_DY_TIME = 52.0
_SM2_DY_LABEL = 49.8
# Right panel on yt2: four designed 18-pt line slots (chametz pair
# on the dark inset band, kiddush-levana pair below), continuing at
# the same pitch for any further info lines. Values are the mock
# text BASELINES; the renderer converts to its mid-anchor at draw
# time via _BASE2MID (calibrated: baseline ≈ mid + 0.366·size for
# the serif face in fpdf cells).
_P2_LINE_BASES = (305.6, 322.6, 353.7, 370.7)
# Chametz band — the dark inset behind the panel's first two lines.
# Mock-measured; PESACH-ONLY chrome, so it is no longer baked into
# the yt2 template: the renderer draws it per-card, exactly when the
# card has chametz lines (same pattern as the omer boxes). A
# 2-small-box week WITHOUT chametz (ר״ה Thu+Fri, הושענא רבה, ערב
# סוכות) therefore gets a clean panel instead of an empty dark band.
_CH_BAND_X, _CH_BAND_Y = 391.0, 288.9
_CH_BAND_W, _CH_BAND_H = 182.3, 40.4
_CH_BAND_R = 8.49
_CH_BAND_FILL = (165, 167, 170)
# Panel layout is DERIVED, not hard-coded, so the band grows with the
# number of chametz lines (Erev-Pesach-on-Shabbos has THREE: שריפה
# עש״ק + אכילה שב״ק + ביטול שב״ק) and everything still lands inside
# the panel. At 2 band lines + 2 below these values reproduce the
# mock's baselines exactly (305.6 / 322.6 / 353.7 / 370.7).
_P2_PAD_BOT = 6.7        # band bottom below its last baseline
_P2_GROUP_GAP = 24.4     # band bottom → first baseline below it
_P2_LAST_MAX = 375.0     # deepest baseline that still clears the panel
_P2_ASC_EM = 0.57        # BAFranknatan ink height above the baseline
_P2_DESC_EM = 0.18       # ...and below it
_P2_PANEL_TOP, _P2_PANEL_BOT = 285.0, 383.0
_P2_SIZE = 18.0
_P2_PITCH = 17.0
_P2_MW = 182.0
_P2_HEAD_SIZE = 22.0     # the 'פרשת …' panel heading (body is 18)
# The whole panel group (band + lines) is centred on the DESIGN's own
# optical centre — derived so the mock's shape (2 chametz + 2 lines)
# lands on its exact baselines, while a short panel (just מברכים +
# מולד) no longer floats at the top with a hole beneath it.
_P2_GROUP_CY = (_CH_BAND_Y
                + _P2_LINE_BASES[-1] + _P2_DESC_EM * _P2_SIZE) / 2.0
_BASE2MID = 0.299                # mid_y = baseline − _BASE2MID·size (measured)
_KL_HEAD = "סוף זמן קידוש לבנה"   # mock splits the KL line after this

# Table grid (shared by all templates). 14 columns; the day-letter
# strip and the omer strip sit OUTSIDE the grid to its right.
_COL_EDGES = [
    55.60, 86.74, 117.89, 149.05, 180.20, 211.36, 242.52, 273.67,
    304.83, 335.98, 367.14, 398.30, 429.45, 460.61, 506.30,
]
_ROW_TOP, _ROW_H = 468.23, 34.77
_ROW_CY = [_ROW_TOP + _ROW_H * i + _ROW_H / 2.0 for i in range(7)]

# The emphasised סוף-זמן-קר״ש cell: light pill behind the time,
# measured from the baked sample pills (29.46 × 13.75, #d3d5d6).

# Hebrew-date column: date line + optional event sub-label on a pill
# (pill 463.38 → 501.88, 9.23 tall, centre ≈ row-centre + 8.0).
_DATE_SIZE = 14.0
_DATE_RISE = 6.0                 # date centre = ry − rise when sub present
_SUB_PILL_W, _SUB_PILL_H = 42.0, 9.23   # widened + column-centred
                                        # (was the mock's 38.5 at a
                                        # hand-placed 482.63)
_SUB_PILL_DY = 8.0   # mean of the mock's hand-placed pills (+7.2..+8.4)
_SUB_SIZE = 9.0                  # design: 9 pt inside the pill
_SUB_FILL = (211, 213, 214)

_CIVIL_SIZE = 11.0

# Column headers — REAL renderer text (not baked chrome), from
# WEEKLY_ZMAN_COLUMNS short labels. Layout mock-measured: 12 pt,
# two stacked baselines 439.6/450.65, single-line at 444.6.
_HDR_SIZE = 12.0
_HDR_BASE1 = 439.6
_HDR_BASE2 = 450.65
_HDR_BASE_ONE = 444.6
_ZMAN_SIZE = 11.0

# Day-letter strip (outside the grid): cells 510.3 → 556.4 per row.
_DAY_CX = 533.35
_DAY_SIZE = 20.0
_DAY_MW = 39.0

# Omer strip (dynamic; drawn only during sefirah — geometry from the
# designer's Pesach mock): grey stadium tab + rounded box per omer day.
# קר"ש-מג"א emphasis pill — design-verified in BOTH mocks (7 grey
# rects, x 336.7-366.2 = the מג"א column, r 2.43 rounded corners).
# The renderer draws it per row, centred on the GRID row (the baked
# mock samples drift ~3pt with the designer's hand-placed text).
_KRSH_PILL_W, _KRSH_PILL_H = 29.46, 13.75
_KRSH_PILL_FILL = (211, 213, 214)
_KRSH_PILL_R = 2.43
_KRSH_PILL_DY = -0.18            # design: pill rides 0.54 above the
                                 # digit ink center (mock-measured)
_OMER_CX = 574.75
# Design chrome: rounded SQUARES (30.52 sq, r 7.30), grid-aligned —
# NOT circles. Drawn per-row only when the day has a count (so the
# first sefirah week shows boxes only from the night it starts).
_OMER_BOX = 30.52
_OMER_BOX_R = 7.30
_OMER_FILL = (134, 136, 138)
_OMER_STROKE = (35, 31, 32)
_OMER_LETTER_SIZE = 19.0         # design: 19 pt
_OMER_LETTER_RISE = 0.278        # BAFranknatan ink-centre (em) above
                                 # baseline — centres letters in boxes
_TAB_X0, _TAB_Y0, _TAB_X1, _TAB_Y1 = 559.5, 390.0, 590.0, 463.8
_TAB_TEXT_SIZE = 22.0

# Footer — the notes line, wrapped onto the two baked footer rows.
_FOOT_CX = 306.0
_FOOT_Y1, _FOOT_Y2 = 735.4, 749.5
_FOOT_SIZE = 11.0
_FOOT_MW1, _FOOT_MW2 = 490.0, 330.0
_FOOT_BASE1, _FOOT_BASE2 = 738.9, 753.3   # mock footer baselines
# The ©YidCal mark is set in the GRID face (Archivo — same face as the
# 'Generated by YidCal' watermark) and at the SAME size as the Hebrew
# footnote beside it, so the footer reads as one line.
_MARK_SIZE = 11.0                # == _FOOT_SIZE
_MARK_BASE_Y = 753.3             # mock baseline of the Miriam mark

_WATERMARK_X = 19.0   # ≥5.5 mm in from the paper edge — x=7 pt
                      # sat inside most printers' unprintable
                      # hardware margin and vanished in print

# Hero titles that are NOT a plain parsha and must not get the
# 'פרשת' pre-line (the ערב-prefixed ones are split on the prefix
# instead). Flagged as an open item — Yoel confirms the rule.


# ── folded ל ─────────────────────────────────────────────────────────
# BAFranknatan ships 348 glyphs the subsetter left UNREACHABLE (no cmap
# entry, and no GSUB to select them). It holds exactly THREE lameds:
#   uni05DC          adv  844  yMax 1703   plain
#   uni05DC_less     adv  718  yMax 1354   upper stroke FOLDED over
#   uni05DC_greater  adv 1700  yMax 1703   wide swash (too big for these
#                                          labels — it sweeps sideways)
# All are exposed at U+E000–E004 in the shipped TTF. EVERY ל in a
# candle-lighting label is folded ('ליל' included); motzei labels and
# everything else keep the plain form. Folding is safe width-wise — the
# folded glyph is NARROWER than the plain one (718 vs 844), so a label
# only ever gets shorter.
_FOLDED_LAMED = "\ue004"       # uni05DC_less
_CANDLE_TOKENS = ("הדלקת", "הדלה״נ")

# The ל is the TALLEST ink in every candle line ('הדלקת', 'הדלה״נ',
# 'ליל …' — taller even than the fallback ״). Folding it drops the line's
# ascent 0.832em → 0.661em, so with the baselines fixed the ink block
# loses height at the TOP only and its optical centre SINKS by half the
# loss. Lift the label group back by that much so a folded label sits
# exactly where the plain one did.
_LAM_ASC_PLAIN, _LAM_ASC_FOLD = 0.832, 0.661
_FOLD_LIFT_EM = (_LAM_ASC_PLAIN - _LAM_ASC_FOLD) / 2.0      # 0.0855


# ── ASCII quotes inside Hebrew words ──────────────────────────────────
# BAFranknatan has neither ״ nor ׳, so both fall back to Miriam Libre —
# where the PROPER marks sit at the Hebrew cap height (0.579 em, lowered
# to match the old Miriam) but the ASCII " and ' sit at LATIN quote height
# (0.695 em), floating well above the letters. Callers hand us raw ASCII in
# places (the service builds its strip as 'לפ"ק'), so normalise here — _t
# is the single text choke point for the whole card.
_HEB_DQ = re.compile(r'(?<=[\u05d0-\u05ea])"(?=[\u05d0-\u05ea])')
_HEB_SQ = re.compile(r"(?<=[\u05d0-\u05ea])'")


def _heb_marks(s: str) -> str:
    """ASCII " / ' inside a Hebrew word → gershayim ״ / geresh ׳."""
    if not s:
        return s
    return _HEB_SQ.sub("\u05f3", _HEB_DQ.sub("\u05f4", s))


def _fold_lameds(text: str) -> str:
    """Fold EVERY ל in a candle-lighting label.

    Gated on the label actually being a candle box ('הדלקת' / 'הדלה״נ')
    — the check runs BEFORE the substitution, since the token itself
    contains a ל."""
    if not text or not any(tok in text for tok in _CANDLE_TOKENS):
        return text
    return text.replace("ל", _FOLDED_LAMED)


def _shecheyanu(w, box) -> str:
    """The שהחיינו / א״א שהחיינו marker to print under ``box``.

    Empty unless the service option is on AND the data layer put a marker
    on this box (candle boxes that bring in a Yom Tov). Motzei boxes and
    plain-Shabbos lightings never carry one."""
    if not getattr(w, "show_shehecheyanu", False):
        return ""
    return (getattr(box, "shehecheyanu_he", "") or "").strip()


def _col_center(idx_from_right: int) -> tuple[float, float]:
    """(centre-x, width). idx 0 = rightmost (יום החודש),
    1 = למספ׳, 2.. = the 12 zmanim columns."""
    n = len(_COL_EDGES) - 1
    cell = n - 1 - idx_from_right
    x0, x1 = _COL_EDGES[cell], _COL_EDGES[cell + 1]
    return (x0 + x1) / 2.0, (x1 - x0)


def render_weekly_yidcal_pdf(
    *,
    weekly: WeeklyData,
    output_path: Path,
    title_he: str = "",
    subtitle_he: str = "",
    notes_he: str = "",
) -> None:
    """Render ``weekly`` onto the new-design template at ``output_path``."""
    pdf = _Weekly2PDF(community_strip_he=title_he, notes_he=notes_he)
    pdf.set_auto_page_break(auto=False)
    register_fonts(pdf)
    register_mono_font(pdf)
    register_design_fonts(pdf)
    pdf.add_page()
    pdf.render_card(weekly)
    pdf.output(str(output_path))


def render_weekly_yidcal_pdf_multi(
    *,
    weeks: list,
    output_path: Path,
) -> None:
    """Render a multi-page new-design weekly booklet — one page per week.

    ``weeks`` is a list of ``(WeeklyData, title_he, notes_he)`` tuples in
    print order, identical to the legacy renderer's contract.
    """
    if not weeks:
        raise ValueError("render_weekly2_pdf_multi: no weeks given")
    pdf = _Weekly2PDF(community_strip_he="", notes_he="")
    pdf.set_auto_page_break(auto=False)
    register_fonts(pdf)
    register_mono_font(pdf)
    register_design_fonts(pdf)
    for weekly, title_he, notes_he in weeks:
        pdf.community_strip_he = title_he or ""
        pdf.notes_he = notes_he or ""
        pdf.add_page()
        pdf.render_card(weekly)
    pdf.output(str(output_path))


# Big-box time digits: the design uses Cascadia Code (OFL, bundled
# unmodified under fonts/ with its license). Registered "" only —
# the mock uses the Regular weight.
FONT_FAMILY_MONO = "YidCalLuachMono"
# CascadiaYidCal = OFL Modified Version of Cascadia Code (plain
# zero, condensed colon; renamed per the OFL Reserved-Font-Name
# clause — see fonts/CascadiaYidCal-LICENSE.txt).
_MONO_FILE = "CascadiaYidCal-Bold.ttf"   # bold only — the big box
                                         # times are the sole mono
                                         # draw and they are bold
# Design fonts: BAFranknatan for all Hebrew text, Archivo for the
# grid/civil/small-box digits + watermark (Acumin stand-in), Miriam
# Libre for the \u00a9YidCal mark. BAFranknatan is a subset face — it
# lacks geresh/gershayim/hyphen/digits, so Miriam Libre then Heebo are
# registered as glyph fallbacks (mirroring the designer's own
# MyriadHebrew substitutions in the mock).
#
# Archivo is instanced at wght 600 / wdth 78: the grid columns were
# sized for Bahnschrift, a narrow DIN-ish face, and Archivo at normal
# width runs ~35%% wider. wdth 78 tracks Bahnschrift's advances to
# within ~6%%. Both faces are OFL (licences ship alongside them).
FONT_HEB = "YidCalHeb"
FONT_GRID = "YidCalGrid"
FONT_MARK = "YidCalMark"
FONT_LAT = "YidCalLat"      # Archivo, optically matched to the Hebrew
_HEB_FILE = "BAFranknatan-Artext.ttf"
_GRID_FILE = "Archivo-SemiCondensed-SemiBold.ttf"  # every
                                          # grid/date/time draw and
                                          # the watermark use it
_MARK_FILE = "MiriamLibre-Regular.ttf"
# A SECOND cut of Archivo for Latin/digits that sit INSIDE Hebrew text.
# At a given point size Archivo's caps and digits stand 1.24x the height of
# BAFranknatan's Hebrew letters (the old Miriam fallback was 1.01x, i.e.
# matched) — so the English place name and the molad's digits looked
# oversized beside the Hebrew. Archivo-Text is the same face with its
# unitsPerEm raised, so it renders at 1.00x. The GRID keeps the full-size
# cut: there Latin stands alone and the design's balance (Bahnschrift was
# 1.27x) is unchanged.
_LAT_FILE = "Archivo-Text.ttf"


def register_mono_font(pdf: FPDF) -> None:
    """Register the Cascadia Code digits face (big-box times)."""
    pdf.add_font(FONT_FAMILY_MONO, "B", str(_fonts_dir() / _MONO_FILE))


def register_design_fonts(pdf: FPDF) -> None:
    """Register the Weekly-YidCal design faces + glyph fallbacks."""
    fonts = _fonts_dir()
    pdf.add_font(FONT_HEB, "", str(fonts / _HEB_FILE))
    pdf.add_font(FONT_HEB, "B", str(fonts / _HEB_FILE))
    pdf.add_font(FONT_GRID, "B", str(fonts / _GRID_FILE))
    pdf.add_font(FONT_LAT, "B", str(fonts / _LAT_FILE))
    pdf.add_font(FONT_MARK, "", str(fonts / _MARK_FILE))
    pdf.add_font(FONT_MARK, "B", str(fonts / _MARK_FILE))
    # Fallback order matters, and FONT_HEB MUST come first.
    #
    # fpdf resolves a fallback per RUN, not per glyph: once a run switches
    # away (e.g. for the digits in 'סוף זמן שריפת חמץ 11:41'), the Hebrew
    # beside them goes with it. Listing FONT_HEB first means any such run
    # re-checks BAFranknatan before anything else, so Hebrew can never be
    # stolen by a Latin face. Then:
    #   FONT_LAT (Archivo Text) — Latin + digits, in the cut that is
    #                          optically matched to the Hebrew beside it
    #   FONT_MARK (M.Libre)  — ״ ׳ (neither BAFranknatan nor Archivo has
    #                          them), at the corrected cap height
    #   FONT_FAMILY (Heebo)  — last resort
    pdf.set_fallback_fonts(
        [FONT_HEB, FONT_LAT, FONT_MARK, FONT_FAMILY],
        exact_match=False)


class _Weekly2PDF(FPDF):
    """Composites the full-page template raster with a live text +
    vector overlay in the SVG's own point space (1:1)."""

    PAGE_W = 612   # Letter portrait
    PAGE_H = 792
    FONT = FONT_HEB

    def __init__(self, *, community_strip_he="", notes_he=""):
        super().__init__(orientation="portrait", unit="pt", format="Letter")
        self.community_strip_he = community_strip_he
        self.notes_he = notes_he
        self.set_margins(0, 0, 0)
        self.set_auto_page_break(False)
        self.set_title("YidCal Weekly Luach")

    # identity affine — kept so this renderer reads like the legacy one
    # and a page margin / fit mode can be added later without retouching
    # every anchor.
    def _X(self, x): return x
    def _Y(self, y): return y
    def _S(self, v): return v

    def _t(self, cx, mid_y, s, *, size, color=(0, 0, 0), anchor="C",
           rtl=True, max_w=None, bold=True, track=None, font=None,
           baseline=None):
        """Draw ``s`` centred on (cx, mid_y) — or, when ``baseline``
        is given, with its BASELINE at exactly that y (mid_y is then
        ignored). Baseline mode is font-metric-independent, so mock
        baseline targets survive any font swap unchanged."""
        if s is None or s == "":
            return
        s = _heb_marks(s)
        size = self._S(size)
        mw = self._S(max_w) if max_w else None
        sty = "B" if bold else ""
        fam = font or self.FONT
        self.set_font(fam, sty, size)
        disp = bidi(s) if rtl else s
        if track is not None:
            self.set_stretching(track)
        # NB: fpdf's get_string_width already reflects set_stretching,
        # so the measured width below is the true rendered width.
        if mw:
            _guard = 0
            while size > 2.5 and _guard < 60 and \
                    self.get_string_width(disp) > mw:
                size *= 0.94
                self.set_font(fam, sty, size)
                _guard += 1
        wd = self.get_string_width(disp)
        if baseline is not None:
            # fpdf's cell places the text BASELINE at mid + 0.3*size
            # for every font (measured K = 0.3000 across all five
            # faces). Anchor through the CELL path rather than
            # pdf.text(): text() bypasses fpdf's glyph-fallback
            # machinery, silently dropping characters missing from
            # the subset BAFranknatan face (digits, ״, ׳, -, •).
            # NB: this renderer is strictly 1:1 letter-portrait, so
            # ``size`` (already _S-scaled) shares baseline's units.
            mid_y = baseline - 0.3 * size
        px, py = self._X(cx), self._Y(mid_y)
        box_w = wd + self._S(1.0)
        if anchor == "C":
            x, align = px - box_w / 2, "C"
        elif anchor == "R":
            x, align = px - box_w, "R"
        else:
            x, align = px, "L"
        self.set_text_color(*color)
        self.set_xy(x, py - size * 0.5)
        self.cell(box_w, size, disp, align=align)
        if track is not None:
            self.set_stretching(100)

    def _adaptive_track(
        self, text: str, *, size: float, max_w: float,
        base: int = 100, floor: int = 62,
        font: str | None = None, style: str = "B", rtl: bool = True,
    ) -> int:
        """Pick a set_stretching value so ``text`` fits at FULL ``size``
        (condense-before-shrink), mirroring the legacy hero logic.
        ``font``/``style``/``rtl`` default to the serif-bold Hebrew
        path; digit slots pass their own face so 24-hour (5-char)
        times condense at full point size instead of shrinking."""
        if not text:
            return base
        disp = bidi(text) if rtl else text
        self.set_font(font or self.FONT, style, self._S(size))
        self.set_stretching(100)
        w100 = self.get_string_width(disp)
        self.set_stretching(100)
        if w100 <= 0:
            return base
        needed = int(round(100.0 * self._S(max_w) / w100))
        return max(floor, min(base, needed))

    def _round_rect(self, x, y, w, h, r, *, fill=None,
                    stroke=None, sw=1.0):
        """Rounded rect as ONE closed path (top-left anchored,
        page pt). fpdf's rect(round_corners=True) composes arcs +
        body rects whose seams rasterize as hairlines on solid
        fills — a single 4-bezier path paints seamlessly."""
        X, Y = self._X(x), self._Y(y)
        W, H, R = self._S(w), self._S(h), self._S(r)
        K = R * 0.5522847
        pp = _fdraw.PaintedPath()
        if fill is not None:
            pp.style.fill_color = _fdraw.DeviceRGB(
                fill[0] / 255.0, fill[1] / 255.0, fill[2] / 255.0)
        if stroke is not None:
            pp.style.stroke_color = _fdraw.DeviceRGB(
                stroke[0] / 255.0, stroke[1] / 255.0, stroke[2] / 255.0)
            pp.style.stroke_width = self._S(sw)
            pp.style.paint_rule = (
                _fdraw.PathPaintRule.STROKE_FILL_NONZERO
                if fill is not None else _fdraw.PathPaintRule.STROKE)
        else:
            pp.style.paint_rule = _fdraw.PathPaintRule.FILL_NONZERO
        pp.move_to(X + R, Y)
        pp.line_to(X + W - R, Y)
        pp.curve_to(X + W - R + K, Y, X + W, Y + R - K, X + W, Y + R)
        pp.line_to(X + W, Y + H - R)
        pp.curve_to(X + W, Y + H - R + K,
                    X + W - R + K, Y + H, X + W - R, Y + H)
        pp.line_to(X + R, Y + H)
        pp.curve_to(X + R - K, Y + H, X, Y + H - R + K, X, Y + H - R)
        pp.line_to(X, Y + R)
        pp.curve_to(X, Y + R - K, X + R - K, Y, X + R, Y)
        pp.close()
        with self.drawing_context() as dc:
            dc.add_item(pp)

    def _pill(self, cx, cy, w, h, fill, *, stroke=None, sw=0.8,
              r=None):
        """Rounded rect centred on (cx, cy). ``r`` is the corner
        radius; default h/2 → a full stadium."""
        self._round_rect(cx - w / 2.0, cy - h / 2.0, w, h,
                         h / 2.0 if r is None else r,
                         fill=fill, stroke=stroke, sw=sw)

    # ── page ──
    def render_card(self, weekly: WeeklyData) -> None:
        n_small = sum(1 for b in weekly.boxes if not b.big)
        if n_small == 0:
            tpl = "regular"
        elif n_small >= 2 and _tpl_exists("yt2"):
            tpl = "yt2"
        else:
            tpl = "yt1"
        self.set_fill_color(255, 255, 255)
        self.rect(0, 0, self.PAGE_W, self.PAGE_H, style="F")
        self.image(_page_image(tpl), 0, 0, self.PAGE_W, self.PAGE_H)
        self._header(weekly, tpl)
        self._headers()
        self._table(weekly)
        self._omer_strip(weekly)
        self._footer()
        self._draw_watermark()

    # ── watermark (identical text/colour to the other luach styles) ──
    def _mark_w(self) -> float:
        """Rendered width of the ©YidCal mark."""
        self.set_font(FONT_LAT, "B", self._S(_MARK_SIZE))
        return self.get_string_width("\u00a9YidCal")

    def _fit_w(self, s, *, size, max_w=None, font=None,
               bold=True, rtl=True) -> float:
        """Width at which ``_t`` will actually draw ``s`` — mirrors
        its shrink-to-fit, so callers can place things beside it."""
        if not s:
            return 0.0
        size = self._S(size)
        mw = self._S(max_w) if max_w else None
        sty = "B" if bold else ""
        fam = font or self.FONT
        self.set_font(fam, sty, size)
        disp = bidi(s) if rtl else s
        if mw:
            _g = 0
            while size > 2.5 and _g < 60 and \
                    self.get_string_width(disp) > mw:
                size *= 0.94
                self.set_font(fam, sty, size)
                _g += 1
        return self.get_string_width(disp)

    def _yidcal_mark(self, right_x: float) -> None:
        """Stamp the \u00a9YidCal brand mark (Miriam 12, per the mock:
        baseline 753.3) ending at ``right_x``."""
        self.set_font(FONT_LAT, "B", self._S(_MARK_SIZE))
        w = self.get_string_width("\u00a9YidCal")
        self.set_text_color(0, 0, 0)
        self.text(self._X(right_x - w), self._Y(_MARK_BASE_Y),
                  "\u00a9YidCal")

    def _draw_watermark(self) -> None:
        text = "Generated by YidCal"
        with self.local_context():
            self.set_font(FONT_GRID, "B", 7)
            # lighter than the other renderers' 150: this one is
            # SemiBold (the regular face was dropped), so the same
            # grey reads heavier here
            self.set_text_color(175, 175, 175)
            text_w = self.get_string_width(text)
            cx = _WATERMARK_X
            cy = (self.h + text_w) / 2.0
            with self.rotation(90, cx, cy):
                self.text(cx, cy, text)

    # ── header: strip, big boxes, hero, mid band ──
    def _two_line_split(self, label: str) -> tuple[str, str]:
        """Split a box label onto the two stacked label lines. The
        הדלה״נ small-box labels keep their 'הדלה״נ (…)' head on line 1
        (same rule the legacy renderer uses); everything else splits
        on the midpoint word boundary."""
        toks = label.split()
        if not toks:
            return label, ""
        if toks[0].startswith("הדלה"):
            head = toks[0]
            if len(toks) > 1 and toks[1].startswith("("):
                head += " " + toks[1]
                rest = " ".join(toks[2:])
            else:
                rest = " ".join(toks[1:])
            return head, rest
        if len(toks) == 1:
            return label, ""
        mid = (len(toks) + 1) // 2
        return " ".join(toks[:mid]), " ".join(toks[mid:])

    def _stacked(self, cx, cy_mid, lines, *, size, pitch, max_w,
                 color=(0, 0, 0), track=None, base0=None,
                 condense=False, floor=62):
        """Draw ``lines`` vertically centred as a group on ``cy_mid``
        — or, when ``base0`` is given, line i at BASELINE
        ``base0 + i*pitch`` (cy_mid ignored).

        ``condense=True`` gives each line its own tracking so a long
        one keeps the FULL point size instead of shrinking (the box
        labels are set to one size by the design)."""
        lines = [ln for ln in lines if ln]
        if not lines:
            return

        def _tk(ln):
            if not condense:
                return track
            return self._adaptive_track(
                ln, size=size, max_w=max_w, base=100, floor=floor)

        if base0 is not None:
            for i, ln in enumerate(lines):
                self._t(cx, 0, ln, size=size, color=color,
                        max_w=max_w, track=_tk(ln),
                        baseline=base0 + i * pitch)
            return
        n = len(lines)
        top = cy_mid - pitch * (n - 1) / 2.0
        for i, ln in enumerate(lines):
            self._t(cx, top + i * pitch, ln, size=size,
                    color=color, max_w=max_w, track=_tk(ln))

    def _hero_lines(self, w: WeeklyData) -> tuple[list, list]:
        """→ ([(text, size, slot_h)…] title lines, [sub lines])."""
        title: list[tuple[str, float, float]] = []
        stack = list(getattr(w, "title_main_stack_he", None) or [])
        if stack:
            # e.g. 'ערב שבת' / 'חוה״מ סוכות' — first line small,
            # second big (open item: Yoel confirms this treatment).
            title.append((stack[0], _HERO_SMALL_SIZE, _HERO_SMALL_H))
            for ln in stack[1:]:
                title.append((ln, _HERO_BIG_SIZE, _HERO_BIG_H))
        else:
            main = w.title_main_he or ""
            if main.startswith("ערב "):
                title.append(("ערב", _HERO_SMALL_SIZE, _HERO_SMALL_H))
                title.append((main[len("ערב "):],
                              _HERO_BIG_SIZE, _HERO_BIG_H))
            elif main and getattr(w, "title_is_parsha", False):
                # 'פרשת' pre-line ONLY for real parsha titles — the
                # data layer flags them (YT hero titles like
                # 'שביעי של פסח' or 'הושענא רבה' stay bare).
                title.append(("פרשת", _HERO_SMALL_SIZE, _HERO_SMALL_H))
                title.append((main, _HERO_BIG_SIZE, _HERO_BIG_H))
            elif main:
                title.append((main, _HERO_BIG_SIZE, _HERO_BIG_H))
        subs = list(getattr(w, "title_sub_stack_he", None) or [])
        if not subs and w.title_sub_he:
            subs = [w.title_sub_he]
        return title, subs

    def _header(self, w: WeeklyData, tpl: str) -> None:
        # place / year strip on the top pill
        self._t(_STRIP_CX, 0, self.community_strip_he,
                size=19, anchor="C", max_w=_STRIP_MW,
                baseline=_STRIP_BASE)  # mock: 19 pt

        # big boxes: candle → top, motzei → bottom (data order)
        big = [b for b in w.boxes if b.big]
        small = [b for b in w.boxes if not b.big]
        # (cy, time_dy): labels sit on the pill line ``cy``; the mock
        # places the DIGIT baselines a touch lower (149.7 / 249.4),
        # so the time alone gets a per-slot optical nudge.
        slots = [(_BIG_CANDLE_CY, 3.7), (_BIG_MOTZEI_CY, 2.45)]
        for i, b in enumerate(big[:2]):
            cy, t_dy = slots[i]
            _tk = self._adaptive_track(
                b.time_str, size=_BIG_TIME_SIZE, max_w=_BIG_TIME_MW,
                base=100, floor=72, font=FONT_FAMILY_MONO,
                style="B", rtl=False)
            self._t(_BIG_TIME_CX, cy + t_dy, b.time_str, size=_BIG_TIME_SIZE,
                    rtl=False, max_w=_BIG_TIME_MW, track=_tk,
                    font=FONT_FAMILY_MONO, bold=True)
            l1, l2 = self._two_line_split(_fold_lameds(b.label_he))
            _lb0 = _BIG_LABEL_BASE0 + (_BIG_MOTZEI_CY - _BIG_CANDLE_CY) * i
            if _FOLDED_LAMED in l1:
                _lb0 -= _FOLD_LIFT_EM * _BIG_LABEL_SIZE
            _she = _shecheyanu(w, b)
            if _she:
                _lb0 -= _SHE_LIFT_BIG
            self._stacked(_BIG_LABEL_CX, 0, [l1, l2],
                          size=_BIG_LABEL_SIZE, pitch=_BIG_LABEL_PITCH,
                          max_w=_BIG_LABEL_MW, base0=_lb0,
                          condense=True, floor=58)
            if _she:
                self._t(_BIG_LABEL_CX, 0, _she, size=_SHE_SIZE_BIG,
                        max_w=_BIG_LABEL_MW,
                        baseline=_lb0 + _BIG_LABEL_PITCH + _SHE_GAP_BIG)

        # hero panel — title line(s) + sub line(s), one centred group
        title, subs = self._hero_lines(w)
        _hlines = [(t2, s2) for t2, s2, _h in title]
        _hlines += [(ln, _HERO_SUB_SIZE) for ln in subs]
        bases = [0.0]
        for k in range(1, len(_hlines)):
            pitch = (_HERO_PARSHA_PITCH
                     if k == 1 and _hlines[0][0] == "פרשת"
                     else _HERO_PITCH)
            bases.append(bases[-1] + pitch)
        top = bases[0] - _HERO_ASC_EM * _hlines[0][1]
        bot = bases[-1] + _HERO_DESC_EM * _hlines[-1][1]
        off = _HERO_MID - (top + bot) / 2.0
        bases = [b + off for b in bases]
        for (text, size), b_y in zip(_hlines, bases):
            tk = self._adaptive_track(text, size=size, max_w=_HERO_MW)
            self._t(_HERO_CX, 0, text, size=size,
                    max_w=_HERO_MW, track=tk, baseline=b_y)

        # mid band
        info: list[str] = []
        for raw in w.info_lines_he:
            info.extend(p.strip() for p in raw.split(" - ") if p.strip())
        # When a YT block closes on a parsha-Shabbos the hero names the
        # YT, so the parsha would otherwise only appear buried in a
        # small-box label. Head the side panel with it (Weekly-YidCal
        # only — the field is ignored by the legacy renderer).
        _bp = (getattr(w, "block_parsha_he", "") or "").strip()
        panel_info = ([f"פרשת {_bp}"] if _bp else []) + info
        # chametz lines (Erev Pesach) — pending the dedicated template,
        # they print as the leading right-panel lines (open item).
        panel_extra: list[str] = []
        for raw in w.chametz_lines_he:
            panel_extra.extend(
                p.strip() for p in raw.split(" - ") if p.strip())

        if tpl == "regular" and not panel_extra:
            # full-width ribbon: everything on one centred line; wrap
            # to two only when a single line would over-shrink.
            one = f"  {INFO_SEP}  ".join(info)
            if one:
                self.set_font(self.FONT, "B", _INFO_FULL_SIZE)
                w1 = self.get_string_width(bidi(one))
                if w1 <= _INFO_FULL_MW or len(info) < 2:
                    self._t(_INFO_FULL_CX, 0, one,
                            size=_INFO_FULL_SIZE, max_w=_INFO_FULL_MW,
                            baseline=_INFO_BASE_ONE)
                else:
                    mid = (len(info) + 1) // 2
                    self._stacked(
                        _INFO_FULL_CX, 0,
                        [f"  {INFO_SEP}  ".join(info[:mid]),
                         f"  {INFO_SEP}  ".join(info[mid:])],
                        size=_INFO_FULL_SIZE,
                        pitch=_INFO_BASE_TWO[1] - _INFO_BASE_TWO[0],
                        max_w=_INFO_FULL_MW, base0=_INFO_BASE_TWO[0])
        else:
            # yt1 / yt2 band: small candle box slot(s) + right panel
            n_slots = 2 if tpl == "yt2" else 1
            for i, b in enumerate(small[:n_slots]):
                _lbl = b.label_he
                if _bp and _lbl.endswith(" " + _bp):
                    _lbl = _lbl[: -(len(_bp) + 1)].rstrip()
                self._t(_SM_TIME_CX, 0,
                        b.time_str, size=_SM_TIME_SIZE, rtl=False,
                        max_w=_SM_TIME_MW, font=FONT_GRID,
                        baseline=320.0 + _SM2_DY_TIME * i)
                l1, l2 = self._two_line_split(_fold_lameds(_lbl))
                _she = _shecheyanu(w, b)
                if not _she:
                    # mock baselines, lifted for the folded ל
                    _b0 = _SM_LABEL_BASE0 + _SM2_DY_LABEL * i
                    if _FOLDED_LAMED in l1:
                        _b0 -= _FOLD_LIFT_EM * _SM_LABEL_SIZE
                    self._stacked(_SM_LABEL_CX, 0,
                                  [l1, l2], size=_SM_LABEL_SIZE,
                                  pitch=_SM_LABEL_PITCH,
                                  max_w=_SM_LABEL_MW,
                                  base0=_b0,
                                  condense=True, floor=58)
                else:
                    _L, _P, _M, _G, _H = self._sm_marked_layout()
                    _ptop = _SM_PANEL_TOP + _SM_PANEL_DY * i
                    _top = _ptop + (_SM_PANEL_H - _H) / 2.0
                    _b0 = _top + _LAM_ASC_FOLD * _L
                    self._stacked(_SM_LABEL_CX, 0, [l1, l2],
                                  size=_L, pitch=_P,
                                  max_w=_SM_LABEL_MW, base0=_b0,
                                  condense=True, floor=58)
                    self._t(_SM_LABEL_CX, 0, _she, size=_M,
                            max_w=_SM_LABEL_MW,
                            baseline=_b0 + _P + _G)
            # any small box beyond the template's slots degrades to a
            # panel text line (only possible on the yt1 fallback)
            overflow = [f"{b.label_he}: {b.time_str}"
                        for b in small[n_slots:]]
            if tpl == "yt2":
                # designed panel: chametz pair on the dark inset band,
                # kiddush-levana pair below (the mock splits the KL
                # line after its head), then any remaining lines at
                # the same 18-pt pitch. The band is drawn only when
                # there ARE chametz lines to sit on it.
                # (text, size-factor). The 'פרשת …' heading is set
                # larger than the body lines; everything else is 1.0.
                items: list[tuple[str, float]] = [
                    (ln, 1.0) for ln in panel_extra]
                if _bp:
                    items.append(
                        (f"פרשת {_bp}", _P2_HEAD_SIZE / _P2_SIZE))
                items.extend(
                    (ln, 1.0) for ln in self._panel_info_lines(info))
                items.extend((ln, 1.0) for ln in overflow)
                n_band = len(panel_extra)
                n_rest = len(items) - n_band
                first = _P2_LINE_BASES[0]

                def _layout(size, pitch, gap):
                    """Baselines + band bottom. Leading between two
                    lines scales with the LARGER of them, so the
                    heading gets the room it needs."""
                    ys: list[float] = []
                    band_bot = None
                    for i, (_, f) in enumerate(items):
                        if i == 0:
                            y = first
                        elif n_band and i == n_band:
                            y = band_bot + gap
                        else:
                            y = ys[-1] + pitch * max(items[i - 1][1], f)
                        ys.append(y)
                        if n_band and i == n_band - 1:
                            band_bot = (
                                y + _P2_PAD_BOT * (size / _P2_SIZE))
                    return ys, band_bot

                # ── fit ── give up slack in order: the group gap, the
                # leading, then the type size — so the common shapes
                # keep the mock's 18 pt.
                size, pitch, gap = _P2_SIZE, _P2_PITCH, _P2_GROUP_GAP
                ys, band_bot = _layout(size, pitch, gap)
                for _ in range(400):
                    if not ys or ys[-1] <= _P2_LAST_MAX or size <= 9.0:
                        break
                    gap_min = _P2_ASC_EM * size + 3.0
                    if n_band and n_rest and gap > gap_min:
                        gap = max(gap_min, gap - 1.0)
                    elif pitch > size * 0.85:
                        pitch *= 0.97
                    else:
                        size *= 0.97
                        pitch *= 0.97
                        gap = _P2_ASC_EM * size + 3.0
                    ys, band_bot = _layout(size, pitch, gap)

                # centre the group vertically
                dy = 0.0
                if ys:
                    top = (_CH_BAND_Y if n_band else
                           ys[0] - _P2_ASC_EM * size * items[0][1])
                    bot = ys[-1] + _P2_DESC_EM * size * items[-1][1]
                    dy = _P2_GROUP_CY - (top + bot) / 2.0
                    dy = min(dy, (_P2_PANEL_BOT - 3.0) - bot)
                    dy = max(dy, (_P2_PANEL_TOP + 3.0) - top)
                if n_band and band_bot is not None:
                    self._round_rect(
                        _CH_BAND_X, _CH_BAND_Y + dy, _CH_BAND_W,
                        band_bot - _CH_BAND_Y, _CH_BAND_R,
                        fill=_CH_BAND_FILL)
                for (ln, f), b_y in zip(items, ys):
                    self._t(_PANEL_CX, 0, ln, size=size * f,
                            max_w=_P2_MW, baseline=b_y + dy)
            else:
                lines = (list(panel_extra) + overflow
                         + self._panel_info_lines(panel_info))
                self._stacked(_PANEL_CX, _PANEL_MID, lines,
                              size=_PANEL_SIZE, pitch=_PANEL_PITCH,
                              max_w=_PANEL_MW)

    def _panel_info_lines(self, info) -> list[str]:
        """Info lines for the NARROW (yt1/yt2) side panel.

        The regular card's full-width ribbon fits
        'מבה״ח … {sep} המולד: …' on one line; in this panel it has to
        break or it condenses down to nothing. So: one line per
        bullet-separated part (מברכים on its own line, המולד on the
        next), and the kiddush-levana line still splits after its
        head (per the mock)."""
        out: list[str] = []
        for ln in info:
            for part in str(ln).split(INFO_SEP):
                part = part.strip()
                if not part:
                    continue
                if (part.startswith(_KL_HEAD + " ")
                        and len(part) > len(_KL_HEAD) + 1):
                    out.append(_KL_HEAD)
                    out.append(part[len(_KL_HEAD) + 1:])
                else:
                    out.append(part)
        return out

    def _sm_marked_layout(self) -> tuple:
        """(label_size, pitch, marker_size, gap, group_height) for a small
        box that carries a שהחיינו marker — shrunk to fit its 45.6 pt
        panel. The label keeps the design's size:pitch ratio."""
        ratio = _SM_LABEL_PITCH / _SM_LABEL_SIZE
        avail = _SM_PANEL_H - 2 * _SM_PANEL_PAD
        L, M = _SM_LABEL_SIZE, _SHE_SIZE_SM

        def _m(L):
            pitch = L * ratio
            # marker ('שהחיינו' — no ל) uses the typical ascent; the LABEL
            # uses the true max, or a ל pokes out through the panel roof.
            gap = _P2_ASC_EM * M + _HEB_DESC_MAX * L + _SHE_CLEAR
            h = _LAM_ASC_FOLD * L + pitch + gap + _P2_DESC_EM * M
            return pitch, gap, h

        pitch, gap, h = _m(L)
        for _ in range(80):
            if h <= avail or L <= 9.0:
                break
            L *= 0.97
            pitch, gap, h = _m(L)
        return L, pitch, M, gap, h

    # ── table ──
    def _table(self, w: WeeklyData) -> None:
        cols = WEEKLY_ZMAN_COLUMNS
        tf = getattr(w, "time_format", "12") or "12"

        def _hh(dt) -> str:
            if tf == "24":
                return f"{dt.hour:02d}"
            return str(dt.hour % 12 or 12)
        for ri, day in enumerate(w.days):
            ry = _ROW_CY[ri]
            # Hebrew date (+ event sub-label on its pill)
            cx, cw = _col_center(0)
            _dcx = _col_center(0)[0]
            if day.dom_sublabel_he:
                self._t(cx, ry - _DATE_RISE, day.hebrew_dom_he,
                        size=_DATE_SIZE, max_w=cw - 3)
                self._pill(_dcx, ry + _SUB_PILL_DY,
                           _SUB_PILL_W, _SUB_PILL_H, _SUB_FILL,
                           r=2.84)
                _stk = self._adaptive_track(
                    day.dom_sublabel_he, size=_SUB_SIZE,
                    max_w=_SUB_PILL_W - 3, base=100, floor=40)
                self._t(_dcx, ry + _SUB_PILL_DY, day.dom_sublabel_he,
                        size=_SUB_SIZE, track=_stk)
            else:
                self._t(cx, ry, day.hebrew_dom_he, size=_DATE_SIZE,
                        max_w=cw - 3)
            # civil date — 'jul 5' per the design mock
            cx, cw = _col_center(1)
            civ = day.civil_date.strftime("%b") \
                + f" {day.civil_date.day}"
            self._t(cx, ry, civ, size=_CIVIL_SIZE, rtl=False,
                    max_w=cw - 3, font=FONT_GRID)
            # 12 zman columns
            for i, (disp, canon) in enumerate(cols):
                cx, cw = _col_center(2 + i)
                if getattr(w, "add_seconds", False):
                    dt = day.zmanim_raw.get(canon) or day.zmanim.get(canon)
                    txt = "" if dt is None else (
                        f"{_hh(dt)}:"
                        f"{dt.minute:02d}:{dt.second:02d}")
                else:
                    dt = day.zmanim.get(canon)
                    txt = "" if dt is None else (
                        f"{_hh(dt)}:{dt.minute:02d}")
                if disp == WEEKLY_BOXED_COLUMN and txt:
                    self._pill(cx, ry + _KRSH_PILL_DY,
                               _KRSH_PILL_W, _KRSH_PILL_H,
                               _KRSH_PILL_FILL, r=_KRSH_PILL_R)
                _tk = self._adaptive_track(
                    txt, size=_ZMAN_SIZE, max_w=cw - 3, base=100,
                    floor=70, font=FONT_GRID, rtl=False)
                self._t(cx, ry, txt, size=_ZMAN_SIZE, rtl=False,
                        max_w=cw - 3, track=_tk, font=FONT_GRID)
            # day letter — bare form ('יום ה׳' → 'ה'; עש״ק/שב״ק as-is)
            lbl = day.weekday_he
            if lbl.startswith("יום "):
                lbl = lbl[len("יום "):].rstrip("׳'")
            self._t(_DAY_CX, ry, lbl, size=_DAY_SIZE, max_w=_DAY_MW,
                    track=None)

    # ── column headers (our own text over the baked band) ──
    def _headers(self) -> None:
        heads = ["יום החודש", "למספ׳"] \
            + [c[0] for c in WEEKLY_ZMAN_COLUMNS]
        for idx, label in enumerate(heads):
            cx, cw = _col_center(idx)
            parts = label.rsplit(" ", 1)
            if len(parts) == 2:
                self._t(cx, 0, parts[0], size=_HDR_SIZE,
                        max_w=cw - 2, baseline=_HDR_BASE1)
                self._t(cx, 0, parts[1], size=_HDR_SIZE,
                        max_w=cw - 2, baseline=_HDR_BASE2)
            else:
                self._t(cx, 0, label, size=_HDR_SIZE,
                        max_w=cw - 2, baseline=_HDR_BASE_ONE)

    # ── omer strip (dynamic — only during sefirah) ──
    def _omer_strip(self, w: WeeklyData) -> None:
        if not any(d.omer_letters_he for d in w.days):
            return
        # ספירה tab (stadium) above the first row
        tw, th = _TAB_X1 - _TAB_X0, _TAB_Y1 - _TAB_Y0
        tcx = (_TAB_X0 + _TAB_X1) / 2.0
        tcy = (_TAB_Y0 + _TAB_Y1) / 2.0
        self._round_rect(_TAB_X0, _TAB_Y0, tw, th, 7.18,
                         fill=_OMER_FILL, stroke=_OMER_STROKE,
                         sw=1.0)
        with self.rotation(90, self._X(tcx), self._Y(tcy)):
            self._t(tcx, tcy, "ספירה", size=_TAB_TEXT_SIZE,
                    max_w=th - 6, track=None)
        for ri, day in enumerate(w.days):
            if not day.omer_letters_he:
                continue
            ry = _ROW_CY[ri]
            self._round_rect(_OMER_CX - _OMER_BOX / 2.0,
                             ry - _OMER_BOX / 2.0,
                             _OMER_BOX, _OMER_BOX, _OMER_BOX_R,
                             fill=_OMER_FILL, stroke=_OMER_STROKE,
                             sw=1.0)
            letters = day.omer_letters_he
            for g in ("\u05f3", "\u05f4", "'", '"', "\u2032", "\u2033"):
                letters = letters.replace(g, "")
            self._t(_OMER_CX, 0, letters, size=_OMER_LETTER_SIZE,
                    max_w=_OMER_BOX - 5,
                    baseline=ry + _OMER_LETTER_SIZE * _OMER_LETTER_RISE)

    # ── footer ──
    def _footer(self) -> None:
        if not self.notes_he:
            self._yidcal_mark(_FOOT_CX + _FOOT_MW2 / 2.0)
            return
        segs = [s.strip() for s in self.notes_he.split("|") if s.strip()]
        # The \u00a9YidCal brand mark is stamped separately in Miriam
        # (per the design) — drop any such tail the service composed.
        segs = [s for s in segs
                if s not in ("YidCal\u00a9", "\u00a9YidCal", "לכל זמן \u00a9")]
        if len(segs) <= 1:
            one = segs[0] if segs else self.notes_he
            self._t(_FOOT_CX, 0, one, size=_FOOT_SIZE,
                    max_w=_FOOT_MW1, baseline=_FOOT_BASE1)
            self._yidcal_mark(_FOOT_CX + _FOOT_MW2 / 2.0)
            return
        # greedy balance: fill line 1 to ~60% of the joined width
        self.set_font(self.FONT, "B", _FOOT_SIZE)
        total = sum(self.get_string_width(bidi(s)) for s in segs)
        acc, cut = 0.0, len(segs)
        for i, s in enumerate(segs):
            acc += self.get_string_width(bidi(s))
            if acc >= total * 0.6:
                cut = i + 1
                break
        l1 = " | ".join(segs[:cut])
        l2 = " | ".join(segs[cut:])
        if l2:
            # trailing separator before the ©YidCal mark (bidi
            # places it at the visual LEFT end, beside the mark)
            l2 += " |"
        self._t(_FOOT_CX, 0, l1, size=_FOOT_SIZE,
                max_w=_FOOT_MW1, baseline=_FOOT_BASE1)
        if l2:
            # The mark sits at line 2's visual LEFT end but is NOT part
            # of the string — so centre the whole GROUP (mark + gap +
            # line) on the footer. Centring the line alone left the
            # group half-a-mark to the left. The line's own budget
            # shrinks by the mark so the group still fits _FOOT_MW2.
            _gap = 4.0
            _mw = self._mark_w()
            _l2_mw = max(40.0, _FOOT_MW2 - _mw - _gap)
            _l2_w = self._fit_w(l2, size=_FOOT_SIZE, max_w=_l2_mw)
            _l2_cx = _FOOT_CX + (_mw + _gap) / 2.0
            self._t(_l2_cx, 0, l2, size=_FOOT_SIZE,
                    max_w=_l2_mw, baseline=_FOOT_BASE2)
            self._yidcal_mark(_l2_cx - _l2_w / 2.0 - _gap)
        else:
            self._yidcal_mark(_FOOT_CX + _FOOT_MW2 / 2.0)
