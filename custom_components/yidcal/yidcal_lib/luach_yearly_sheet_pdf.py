"""
custom_components/yidcal/yidcal_lib/luach_yearly_sheet_pdf.py

Renders the "Yearly sheet" (South-Fallsburg-style) luach as a PDF.

Layout: A4 portrait, RTL Hebrew, two columns. A single page covers an
entire Hebrew year (29 Elul → 2 Tishrei of the following year). Each
LuachRow is one compact line showing the title/parsha (with Hebrew
date inlined), civil date, candle lighting, motzei, and two
zmanim columns (סוף זמן קר״ש, עלות השחר). AnnotationRows render as
full-column-width Hebrew text, right-aligned, no shading or border —
the SF aesthetic.

The column split is computed dynamically: items are pre-measured, the
break is placed just-before a LuachRow at whichever such position
minimises the difference between column heights. This keeps each row's
leading + trailing annotations grouped with the row.

The input is a list of LuachItem (LuachRow or AnnotationRow) produced
by luach_data.build_luach() — exactly the same stream the
yearly-multi-page renderer consumes.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from fpdf import FPDF

from .luach_data import LuachRow, AnnotationRow, LuachItem
from .luach_pdf_common import (
    register_serif_fonts,
    bidi,
    FONT_FAMILY_SERIF,
    INFO_SEP,
)
from . import halacha_events as he


# Default extra-zmanim columns for the yearly-sheet renderer. The SF layout
# shows exactly two extras (סוף זמן קר״ש and עלות השחר) beyond candle
# + motzei. The yearly-sheet renderer uses these two by default but accepts
# overrides via ``extra_zmanim_labels`` for parity with the weekly
# renderer's signature.
DEFAULT_EXTRA_ZMANIM = ("עלות השחר", "סוף זמן קריאת שמע מג״א")


def _fmt_time_12(dt: datetime) -> str:
    """12-hour 'H:MM' (no AM/PM — the luachs leave it implicit)."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}"


def _fmt_civil_short(d) -> str:
    """SF-style abbreviated date: 'Sep 22', 'Nov 7' (no leading zero,
    no year — the year is already in the page title).

    On Windows ``%-d`` is not portable; we strip the zero manually.
    """
    return d.strftime("%b ") + str(d.day)


def render_yearly_sheet_pdf(
    *,
    items: list[LuachItem],
    output_path: Path,
    title_he: str = "",
    subtitle_he: str = "",
    notes_he: str = "",
    hebrew_year: int | None = None,
    extra_zmanim_labels: tuple[str, ...] = DEFAULT_EXTRA_ZMANIM,
    diaspora: bool = True,
) -> None:
    """Render ``items`` to a single-page yearly PDF at ``output_path``.

    Caller is responsible for providing already-assembled items in
    chronological order (see luach_data.build_luach). The expected
    range is one full Hebrew year (Erev RH → 2 Tishrei of the next
    Hebrew year).

    ``title_he``: main banner line, typically
        ``f"לוח הזמנים לשנת {hebrew_year_letters} לפ\"ק"``.
    ``subtitle_he``: the location (e.g. ``"Brooklyn, NY"``). Used both
        as the city line under the title and inline in the
        location-aware note (``... באופק <city>``).
    ``notes_he``: the candle/havdalah convention line
        (``זמן הדלקת הנרות … לפי שיטת ר״ת``), already built by the
        service layer's ``_build_titles``.
    ``hebrew_year``: numeric Hebrew year (e.g. 5786). When provided,
        the renderer adds a ``קביעת השנה`` line using
        ``halacha_events.format_kvius_line``. Required for the kvius
        line; omit only when generating a free-form range.
    """
    pdf = _YearlySheetPDF(
        title_he=title_he,
        subtitle_he=subtitle_he,
        notes_he=notes_he,
        hebrew_year=hebrew_year,
        extra_zmanim_labels=extra_zmanim_labels,
    )
    # Disable auto page break — this is a single-page document; the
    # column split is computed explicitly and we never want fpdf2 to
    # add an unexpected page.
    pdf.set_auto_page_break(auto=False)
    register_serif_fonts(pdf)
    pdf.add_page()
    pdf.render_body(items)
    pdf.output(str(output_path))


# ────────────────────────────────────────────────────────────────────
# Internal: the FPDF subclass that drives layout + rendering
# ────────────────────────────────────────────────────────────────────

class _YearlySheetPDF(FPDF):
    """Internal: FPDF subclass that owns yearly-sheet luach geometry,
    header / column split / row drawing.
    """

    # ── Page geometry (A4 portrait, mm) ────────────────────────────
    PAGE_WIDTH_MM = 210
    PAGE_HEIGHT_MM = 297
    LEFT_MARGIN = 8
    RIGHT_MARGIN = 8
    TOP_MARGIN = 8
    BOTTOM_MARGIN = 10

    # Width consumed by the title block. The body starts below it.
    # Conservative initial value — adjusted at render time after the
    # title block actually draws (the renderer captures the y cursor
    # post-title and uses that as the body top).
    TITLE_BLOCK_MAX_HEIGHT = 42

    # ── Layout dimensions (mm) ─────────────────────────────────────
    COL_GAP = 4
    ROW_HEIGHT = 4.0
    ANN_HEIGHT = 3.5
    # Per-line height for the column-header labels. Each header is
    # rendered as TWO stacked lines (e.g. "הדלקת" / "הנרות"); the
    # full header block is 2 × HEADER_LINE_HEIGHT mm tall. Kept
    # tight so the two stacked lines read as one logical header.
    HEADER_LINE_HEIGHT = 3.2

    # ── Per-column field widths (mm). Sum = column width (95 mm).
    #    Reading right-to-left in Hebrew sense, the title cell sits
    #    at the column's right edge and the alos cell at the left.
    W_TITLE = 41         # parsha/YT label + Hebrew date (combined)
    W_CIVIL = 12         # 'Sep 22'
    W_CANDLE = 10        # candle lighting time
    W_MOTZEI = 10        # motzei time
    W_SOFZMAN = 11       # sof zman kr"sh
    W_ALOS = 11          # alos hashachar
    # Sum = 95 mm ✓ (per column)

    # Pull both body columns this many mm to the LEFT of the frame's
    # right edge (the top/bottom rules themselves stay put). The
    # right-hand boundary of every row is the right-aligned bold title
    # ending flush at the rule, while the left-hand boundary is a
    # centered time inside the W_ALOS field with ~3.3 mm of intrinsic
    # air — anchored at the rule, the ink block therefore read as
    # sitting to the RIGHT within the rules. 1.5 mm rebalances it:
    # titles ~1.75 mm off the right rule end, alos digits ~1.8 mm off
    # the left one.
    COLUMN_SHIFT_LEFT = 1.5

    # ── Font sizes ─────────────────────────────────────────────────
    TITLE_SIZE = 18         # main banner Hebrew title
    SUBTITLE_SIZE = 13      # city line
    NOTE_SIZE = 8.5         # location-aware + convention notes lines
    KVIUS_SIZE = 10         # kvius line
    HEADER_SIZE = 8         # column-header labels (הדלקת/הנרות etc.) — bold
    ROW_MAIN_SIZE = 8       # times (candle / motzei / sof zman / alos) — bold
    TITLE_MAIN_SIZE = 9     # title main label (parsha / YT name) — bold, biggest
    TITLE_SECONDARY_SIZE = 7  # title secondary (suffix + hd + special + pirkei) — regular
    ROW_TAIL_SIZE = 7       # legacy: parens (kept for fallback, no longer used)
    ANN_SIZE = 8            # annotation lines (mevorchim, fast-day, tekufah, etc.)
    DST_SIZE = 10           # DST start/end banner — bold, prominent
    DST_LINE_HEIGHT = 4.5   # per-line height for DST banner
    WATERMARK_SIZE = 7

    def __init__(
        self,
        *,
        title_he: str,
        subtitle_he: str,
        notes_he: str,
        hebrew_year: int | None,
        extra_zmanim_labels: tuple[str, ...],
    ) -> None:
        super().__init__(orientation="portrait", unit="mm", format="A4")
        self.set_margins(self.LEFT_MARGIN, self.TOP_MARGIN, self.RIGHT_MARGIN)
        self.title_he = title_he
        self.subtitle_he = subtitle_he
        self.notes_he = notes_he
        self.hebrew_year = hebrew_year
        # Keep extra_zmanim_labels as a tuple of exactly two entries
        # for the yearly layout. If the caller passed more or fewer
        # we still use the first two (or pad with empties) — the
        # column geometry has fixed slots for "סוף זמן קר״ש" and
        # "עלות השחר" only. The first two labels in the tuple are
        # treated as (sof_zman_label, alos_label) when looking them
        # up on each row's ``zmanim`` dict.
        ezl = tuple(extra_zmanim_labels)
        # Default order: (alos, sof_zman). Preserve the caller's
        # order so a custom extra_zmanim_labels still works — we
        # look both up by exact key match.
        self.extra_zmanim_labels = ezl

        # Body geometry — populated when the title block finishes.
        self._body_top: float = 0.0
        self._body_height: float = 0.0
        self._col_width: float = 0.0
        # Right-edge X for the right column and the left column. In a
        # right-to-left layout, the FIRST column (which holds the
        # beginning of the year) is the RIGHT one.
        self._col1_x_right: float = 0.0  # right column right edge
        self._col2_x_right: float = 0.0  # left column right edge

    # ── Watermark (rotated, left edge, all pages) ──────────────────
    def _draw_watermark(self) -> None:
        """Draw a sideways watermark along the left edge. Identical
        helper to the yearly-multi-page renderer's, kept private here to avoid
        cross-module dependency on internals.
        """
        text = "Generated by YidCal integration on Home Assistant"
        with self.local_context():
            self.set_font(FONT_FAMILY_SERIF, "", self.WATERMARK_SIZE)
            self.set_text_color(200, 200, 200)
            text_w = self.get_string_width(text)
            cx = 5.0
            cy = (self.h + text_w) / 2
            with self.rotation(angle=90, x=cx, y=cy):
                self.text(cx, cy, text)

    # ── Title block (page top, full width) ─────────────────────────
    def _draw_double_rule(self, y: float) -> None:
        """Draw a SF-style double horizontal rule spanning the body's
        usable width. Two thin parallel lines, ~0.8 mm apart.

        Used at:
          • Just below the title block, above the column headers.
          • Just below the body, above the page's bottom margin.
        """
        x_left = self.LEFT_MARGIN
        x_right = self.PAGE_WIDTH_MM - self.RIGHT_MARGIN
        prev_lw = self.line_width
        self.set_line_width(0.25)
        self.line(x_left, y, x_right, y)
        self.line(x_left, y + 0.8, x_right, y + 0.8)
        self.set_line_width(prev_lw)

    def _draw_title_block(self) -> float:
        """Draw the title block at the top of the page. Returns the y
        coordinate immediately below the block, which the body uses
        as its top edge.
        """
        y = self.TOP_MARGIN
        usable_w = self.PAGE_WIDTH_MM - self.LEFT_MARGIN - self.RIGHT_MARGIN
        self.set_text_color(0, 0, 0)

        # ── בס״ד at the top-right corner ──
        # Tiny right-aligned label above the title (SF convention; sits
        # in the right margin area outside the title's centered column).
        self.set_font(FONT_FAMILY_SERIF, "", self.NOTE_SIZE)
        # Use the right edge of the usable area, right-aligned cell.
        right_x = self.PAGE_WIDTH_MM - self.RIGHT_MARGIN - 12
        self.set_xy(right_x, y)
        self.cell(12, 4, bidi("בס״ד"), align="R")

        # Line 1 — main Hebrew title (bold)
        if self.title_he:
            self.set_font(FONT_FAMILY_SERIF, "B", self.TITLE_SIZE)
            self.set_xy(self.LEFT_MARGIN, y)
            self.cell(usable_w, 8, bidi(self.title_he), align="C")
            y += 8

        # Line 2 — city in an inverted (black-fill, white-text) box,
        # SF-style. Uppercased to match the SF "SOUTH FALLSBURG" cap
        # treatment; the subtitle string itself is expected to already
        # include the state (e.g. "South Fallsburg, New York").
        if self.subtitle_he:
            self.set_font(FONT_FAMILY_SERIF, "B", self.SUBTITLE_SIZE)
            box_text = self.subtitle_he.upper()
            text_w = self.get_string_width(box_text)
            # Generous horizontal padding so the box doesn't crowd the
            # text; vertical sized to fit the cap height with a hair of
            # bleed top and bottom.
            pad_x = 4.0
            box_w = text_w + 2 * pad_x
            box_h = 6.5
            box_x = self.LEFT_MARGIN + (usable_w - box_w) / 2
            # Filled rectangle (black).
            self.set_fill_color(0, 0, 0)
            self.rect(box_x, y, box_w, box_h, style="F")
            # White text on the fill.
            self.set_text_color(255, 255, 255)
            self.set_xy(box_x, y)
            self.cell(box_w, box_h, box_text, align="C")
            # Reset for subsequent lines.
            self.set_text_color(0, 0, 0)
            y += box_h

        # Line 3 — location-aware note (only when we have a city; the
        # phrasing reads awkwardly without one).
        if self.subtitle_he:
            note_he = (
                f"הדלקת הנרות בעש״ק, ומוצאי שבתות ויו״ט וסוף זמן "
                f"קר״ש ועלות השחר באופק {self.subtitle_he}"
            )
            self.set_font(FONT_FAMILY_SERIF, "", self.NOTE_SIZE)
            self.set_xy(self.LEFT_MARGIN, y)
            self.cell(usable_w, 4.5, bidi(note_he), align="C")
            y += 4.5

        # Line 4 — kvius hashanah (built from hebrew_year)
        if self.hebrew_year is not None:
            kvius = he.format_kvius_line(self.hebrew_year)
            self.set_font(FONT_FAMILY_SERIF, "B", self.KVIUS_SIZE)
            self.set_xy(self.LEFT_MARGIN, y)
            self.cell(usable_w, 5, bidi(kvius), align="C")
            y += 5

        # Line 4b — SF Yiddish azhara line. A small reminder that
        # during the shorter (winter) weeks the previous Shabbos's
        # zman קר״ש should be used. Appears between kvius and the
        # candle/havdalah convention notes.
        azhara = (
            "אזהרה: ווען די טעג ווערן קלענער זאל מען רעכענען זמן קר״ש "
            "לויט דעם פארגאנגענעם שבת"
        )
        self.set_font(FONT_FAMILY_SERIF, "", self.NOTE_SIZE)
        self.set_xy(self.LEFT_MARGIN, y)
        self.cell(usable_w, 4.5, bidi(azhara), align="C")
        y += 4.5

        # Line 5 — candle/havdalah convention notes (already built by
        # the service layer)
        if self.notes_he:
            self.set_font(FONT_FAMILY_SERIF, "", self.NOTE_SIZE)
            self.set_xy(self.LEFT_MARGIN, y)
            self.cell(usable_w, 4.5, bidi(self.notes_he), align="C")
            y += 4.5

        # Small breathing room before the double rule
        y += 1.0
        # SF-style double horizontal rule separating the title block
        # from the body.
        self._draw_double_rule(y)
        # Leave breathing space below the rule before the column headers
        y += 0.8 + 1.5
        return y

    # ── Column header (per-column, just above first row) ───────────
    def _draw_column_header(
        self, x_right: float, y: float, *, is_first_column: bool = False,
    ) -> float:
        """Draw the four tiny column-header labels above each column's
        time slots, rendered as TWO stacked lines per label (matching
        the SF compact-header convention).

        Layout (right-to-left): the rightmost column slot is candle,
        then motzei, then sof zman, then alos. Title and civil-date
        columns have no header (per the SF convention).

        When ``is_first_column`` is True, the title-cell area on the
        right side of the column carries the SF disclaimer line
        "עלות השחר אינו זמן הנחת תפילין" — a single bold line
        vertically centered against the two-line column headers. SF
        prints this only on the first column (which is the right
        column in RTL); the second column's matching area carries the
        DST banner instead.
        """
        self.set_font(FONT_FAMILY_SERIF, "B", self.HEADER_SIZE)
        self.set_text_color(0, 0, 0)
        labels_and_widths = [
            (("הדלקת",   "הנרות"),  self.W_CANDLE),
            (("מוצאי",   "שב״ק"),   self.W_MOTZEI),
            (("סוף זמן", "קר״ש"),   self.W_SOFZMAN),
            (("עלות",    "השחר"),   self.W_ALOS),
        ]
        # Start position: just to the LEFT of the title + civil-date
        # area, since headers only span the time columns.
        x = x_right - self.W_TITLE - self.W_CIVIL
        for lines, width in labels_and_widths:
            x -= width
            for li, line in enumerate(lines):
                self.set_xy(x, y + li * self.HEADER_LINE_HEIGHT)
                self.cell(width, self.HEADER_LINE_HEIGHT, bidi(line), align="C")

        # First-column-only SF disclaimer in the title-cell area.
        # Vertically centered against the two header lines.
        if is_first_column:
            disclaimer = "עלות השחר אינו זמן הנחת תפילין"
            disclaimer_w = self.W_TITLE + self.W_CIVIL
            disclaimer_x = x_right - disclaimer_w
            disclaimer_h = 2 * self.HEADER_LINE_HEIGHT
            self.set_font(FONT_FAMILY_SERIF, "B", self.HEADER_SIZE)
            self.set_xy(disclaimer_x, y)
            # Right-align so the text sits flush to the column's right
            # edge (matching SF), with a hair of inner padding.
            self.cell(
                disclaimer_w, disclaimer_h, bidi(disclaimer), align="R",
            )
        return y + 2 * self.HEADER_LINE_HEIGHT

    # ── Title cell (split-style: main bold + tail regular) ─────────
    def _draw_title_cell(
        self, x: float, y: float, width: float, row: LuachRow,
    ) -> None:
        """Render the row's title cell in SF two-tier typography:

          • Main label (parsha / YT name, e.g. "נח", "ערב סוכות",
            "ב׳ דראש השנה") in bold at TITLE_MAIN_SIZE — visually
            the largest element on the row.
          • Secondary text (day-of-week suffix, Hebrew date, special-
            Shabbos labels, pirkei avos chapter, Eruv Tavshilin
            marker), comma-joined with no parentheses, rendered in
            regular weight at TITLE_SECONDARY_SIZE.

        Visual right-to-left layout (within the cell):
          [right edge] <MAIN> <secondary>........[civil-date column]

        The dot leaders are added separately by the caller; this
        method only draws the text. ``head_w`` / ``tail_w`` analogues
        from the previous layout become ``main_w`` and ``secondary_w``.
        """
        # Fall back to title_he if the data layer hasn't populated
        # the split fields (older LuachRow instances during transition).
        main_text = row.title_main_he or row.title_he or ""
        explicit_suffix = row.title_suffix_he or ""
        if not row.title_main_he and row.title_he and not explicit_suffix:
            # No split available — treat the whole title as the main
            # label and put nothing in the secondary suffix slot.
            main_text = row.title_he

        # Build the secondary string from "tight" parts (qualifiers
        # that follow the main label with just a space — weekday
        # suffix, named-Shabbos labels) and "loose" parts (items
        # comma-separated AFTER the date — Hebrew date, BH"B marker,
        # pirkei chapter, Eruv Tavshilin marker).
        #
        # The rendering convention matches SF:
        #   "ערב סוכות יום ב׳, י״ד תשרי"  (tight + loose)
        #   "תצוה זכור, י׳ אדר"            (tight + loose)
        #   "ויקרא, ב׳ ניסן"               (loose only — leading comma)
        #   "ערב שבת שבועות, ו׳ סיון"      (loose only — leading comma)
        #   "נח, ב׳ חשון, מברכים בה״ב"     (BH"B marker AFTER the date)
        #   "מקץ ה׳ דחנוכה, כ״ט כסלו, שבת א׳ דר״ח"
        #                                  (chanukah-day tight; שבת-ר״ח loose)
        #
        # LOOSE-bucket labels (after the date) — calendar announcements
        # about upcoming events, as opposed to descriptors of THIS
        # parsha week itself:
        #   • Mevorchim BH"B markers
        #   • שבת ר״ח variants (plain, or with 2-day-RC day-position
        #     qualifier like "שבת א׳ דר״ח")
        BHB_LABELS = {"מברכין בה״ב", "מברכים בה״ב"}
        SHEHECHEYANU_MARKER = "א״א שהחיינו"

        def _is_loose_special(lbl: str) -> bool:
            if lbl in BHB_LABELS:
                return True
            if lbl == SHEHECHEYANU_MARKER:
                return True
            # Any 'שבת …ר״ח' variant: "שבת ר״ח", "שבת א׳ דר״ח",
            # "שבת ב׳ דר״ח". Defensive against future formatting.
            if lbl.startswith("שבת ") and "ר״ח" in lbl:
                return True
            # Bare RC labels emitted when the row's FRIDAY is RC but
            # Saturday isn't (SF convention — e.g. "ב׳ דר״ח" on Shoftim
            # when Friday = 1 Elul = RC day 2 and Saturday = 2 Elul).
            # These belong in the loose bucket alongside the Hebrew
            # date, NOT tight with the parsha name.
            if lbl in {"ר״ח", "א׳ דר״ח", "ב׳ דר״ח"}:
                return True
            return False

        named_specials = [
            lbl for lbl in row.special_shabbos_he
            if not _is_loose_special(lbl)
        ]
        loose_specials = [
            lbl for lbl in row.special_shabbos_he
            if _is_loose_special(lbl)
        ]

        # Tight (modifiers of main; rendered after main with just a
        # space, joined by space among themselves).
        tight_parts: list[str] = []
        if explicit_suffix:
            tight_parts.append(explicit_suffix)
        tight_parts.extend(named_specials)

        # Loose (comma-separated items; if no tight items, the
        # leading boundary between main and the first loose item is
        # also a comma).
        loose_parts: list[str] = []
        if row.hebrew_date_he:
            loose_parts.append(row.hebrew_date_he)
        loose_parts.extend(loose_specials)
        if row.pirkei_avos_he:
            loose_parts.append(row.pirkei_avos_he)
        if row.eruv_tavshilin:
            loose_parts.append("עירוב תבשילין")

        tight_str = " ".join(tight_parts) if tight_parts else ""
        loose_str = ", ".join(loose_parts) if loose_parts else ""

        if tight_str and loose_str:
            secondary_text = f"{tight_str}, {loose_str}"
            secondary_has_leading_comma = False
        elif tight_str:
            secondary_text = tight_str
            secondary_has_leading_comma = False
        elif loose_str:
            secondary_text = f", {loose_str}"
            secondary_has_leading_comma = True
        else:
            secondary_text = ""
            secondary_has_leading_comma = False

        main_bidi = bidi(main_text) if main_text else ""
        sec_bidi = bidi(secondary_text) if secondary_text else ""

        # Measure widths
        if main_bidi:
            self.set_font(FONT_FAMILY_SERIF, "B", self.TITLE_MAIN_SIZE)
            main_w = self.get_string_width(main_bidi)
        else:
            main_w = 0
        # A small space between main and secondary so they don't run
        # together visually — UNLESS the secondary starts with a
        # leading comma, in which case the comma should sit flush
        # against the main text (matching SF typography).
        if main_bidi and sec_bidi:
            gap_main_secondary = 0.0 if secondary_has_leading_comma else 1.0
        else:
            gap_main_secondary = 0.0
        if sec_bidi:
            self.set_font(FONT_FAMILY_SERIF, "", self.TITLE_SECONDARY_SIZE)
            sec_w = self.get_string_width(sec_bidi)
        else:
            sec_w = 0

        right_edge = x + width  # right-align to cell right edge

        # Render main at right edge
        if main_bidi:
            self.set_font(FONT_FAMILY_SERIF, "B", self.TITLE_MAIN_SIZE)
            # fpdf2's cell() indents its text by self.c_margin (1 mm
            # by default), so anchoring the cell at (right_edge - w)
            # actually printed the title ~1 mm PAST right_edge — bold
            # titles in the page-right column visibly bled past the end
            # of the top/bottom rules. Pull the cell origin back by
            # c_margin so the ink ends exactly at right_edge.
            self.set_xy(right_edge - main_w - self.c_margin, y)
            self.cell(main_w, self.ROW_HEIGHT, main_bidi,
                      border=0, align="L", fill=False)

        # Render secondary to the LEFT of main
        if sec_bidi:
            self.set_font(FONT_FAMILY_SERIF, "", self.TITLE_SECONDARY_SIZE)
            # Same c_margin compensation as the main title above.
            self.set_xy(
                right_edge - main_w - gap_main_secondary - sec_w - self.c_margin,
                y,
            )
            self.cell(sec_w, self.ROW_HEIGHT, sec_bidi,
                      border=0, align="L", fill=False)

        # ── SF-style dot leaders ───────────────────────────────────
        # Fill the empty space on the LEFT of main+secondary with dots
        # at the row's main-size font (regular weight), matching the
        # SF printed-luach convention of connecting the title text
        # to the civil-date column.
        DOT_GAP_RIGHT = 1.0   # mm: gap between dots and the secondary text
        DOT_GAP_LEFT = 0.0    # mm: dots run flush to the civil-date column
        used_width = main_w + gap_main_secondary + sec_w
        dots_zone_width = width - used_width - DOT_GAP_RIGHT - DOT_GAP_LEFT
        if dots_zone_width > 0.5 and (main_bidi or sec_bidi):
            self.set_font(FONT_FAMILY_SERIF, "", self.TITLE_SECONDARY_SIZE)
            dot_char_w = self.get_string_width(".")
            if dot_char_w > 0:
                n_dots = int(dots_zone_width / dot_char_w)
                if n_dots > 0:
                    dot_str = "." * n_dots
                    actual_w = self.get_string_width(dot_str)
                    dots_x = x + DOT_GAP_LEFT
                    self.set_xy(dots_x, y)
                    self.cell(actual_w, self.ROW_HEIGHT, dot_str,
                              border=0, align="L", fill=False)

    # ── Row drawing ────────────────────────────────────────────────
    def _draw_row(self, row: LuachRow, *, x_right: float, y: float) -> None:
        """Draw one luach row at column-right-edge ``x_right``, top ``y``.

        Layout (right-to-left within the column):
          [title cell] [civil] [candle] [motzei] [sof zman] [alos]
        """
        self.set_text_color(0, 0, 0)
        self.set_font(FONT_FAMILY_SERIF, "B", self.ROW_MAIN_SIZE)

        # Title cell
        title_x = x_right - self.W_TITLE
        self._draw_title_cell(title_x, y, self.W_TITLE, row)

        # Civil date (LTR text — show "Sep 22"; no bidi needed for
        # pure ASCII)
        x = title_x - self.W_CIVIL
        self.set_font(FONT_FAMILY_SERIF, "", self.ROW_MAIN_SIZE)
        self.set_xy(x, y)
        civil_text = _fmt_civil_short(row.civil_date) if row.civil_date else ""
        self.cell(self.W_CIVIL, self.ROW_HEIGHT, civil_text, align="C")

        # Candle, motzei, sof zman, alos — small bold times. Each cell
        # is empty when the underlying time is missing (trailing rows
        # have no candle / no zmanim, and so on).
        time_columns = [
            (row.candle_lighting, self.W_CANDLE),
            (row.motzei,          self.W_MOTZEI),
            (self._lookup_zman(row, ("סוף זמן קריאת שמע מג״א",
                                     "סוף זמן קר״ש",
                                     "סוף זמן קריאת שמע גר״א")),
             self.W_SOFZMAN),
            (self._lookup_zman(row, ("עלות השחר",)),
             self.W_ALOS),
        ]
        self.set_font(FONT_FAMILY_SERIF, "B", self.ROW_MAIN_SIZE)
        for value, width in time_columns:
            x -= width
            self.set_xy(x, y)
            text = _fmt_time_12(value) if value else ""
            self.cell(width, self.ROW_HEIGHT, text, align="C")

    @staticmethod
    def _lookup_zman(row: LuachRow, candidates: tuple[str, ...]):
        """Return the first matching zman from ``row.zmanim`` by trying
        each candidate label in order. Labels can vary slightly
        depending on which mg״a/gr״a variant was selected, so we accept
        a tuple of fallbacks.
        """
        for label in candidates:
            v = row.zmanim.get(label)
            if v is not None:
                return v
        return None

    # ── Annotation drawing ─────────────────────────────────────────
    def _annotation_lines(self, ann: AnnotationRow) -> list[str]:
        """Return the visual lines for an annotation. Most annotations
        render as a single line. The mevorchim annotation is special:
        the data layer concatenates the mevorchim head + molad with
        ``" · המולד:"`` to match the Monroe yearly-multi-page luach's one-line
        convention, but the SF yearly layout puts them on two
        separate centered lines:

            מבה״ח חשון ר״ח יום ד׳ וה׳ נח
            המולד: יום ה׳ תולדות בשעה 1:38 וט׳ חלקים אחה״צ

        We split on that exact marker only (so other annotations that
        use ``·`` as an inline separator — fast-day timings, etc. —
        are NOT split).
        """
        text = ann.text_he or ""
        if ann.kind == "mevorchim" and f" {INFO_SEP} המולד:" in text:
            head, tail = text.split(f" {INFO_SEP} המולד:", 1)
            tail_with_prefix = "המולד:" + tail
            # SF convention: render as ONE line with " - " connector
            # when the full text fits comfortably in the column; break
            # into two stacked centered lines otherwise.
            #
            # The threshold uses a 6 mm visual safety margin (3 mm of
            # padding on each side when centered). At a tighter margin
            # (e.g. 2 mm), wide single-line variants like Teves —
            # ``מבה״ח טבת: ר״ח שב״ק מקץ ויום א׳ ויגש - המולד: …`` —
            # appeared to kiss the column edge even though they
            # technically fit; the 6 mm margin forces those long lines
            # to break, matching the SF printed luach.
            one_line = f"{head} - {tail_with_prefix}"
            col_w = (self.W_TITLE + self.W_CIVIL + self.W_CANDLE
                     + self.W_MOTZEI + self.W_SOFZMAN + self.W_ALOS)
            # Measure at the annotation font size (regular weight)
            self.set_font(FONT_FAMILY_SERIF, "", self.ANN_SIZE)
            w = self.get_string_width(bidi(one_line))
            if w <= col_w - 6.0:
                return [one_line]
            return [head, tail_with_prefix]
        # DST start/end notes get the SF banner treatment: split on
        # the parenthetical so the "(סטענדערד טיים)" / "(סעיווינגס טיים)"
        # qualifier sits on its own line below the main message.
        if ann.kind in ("dst_start", "dst_end") and " (" in text:
            idx = text.rfind(" (")
            head = text[:idx]
            tail = text[idx + 1:]  # keep the leading '(' and trailing ')'
            return [head, tail]
        return [text]

    def _draw_annotation(
        self, ann: AnnotationRow, *, x_right: float, y: float,
    ) -> float:
        """Draw one annotation; for the mevorchim+molad case this
        emits two stacked centered lines. Returns the y immediately
        below the rendered block.

        DST start/end annotations get banner-style typography
        (bigger + bold) to match the prominent DST notes on the SF
        printed luach.
        """
        col_w = (self.W_TITLE + self.W_CIVIL + self.W_CANDLE
                 + self.W_MOTZEI + self.W_SOFZMAN + self.W_ALOS)
        x = x_right - col_w
        self.set_text_color(40, 40, 40)
        is_dst = ann.kind in ("dst_start", "dst_end")
        if is_dst:
            self.set_font(FONT_FAMILY_SERIF, "B", self.DST_SIZE)
            line_h = self.DST_LINE_HEIGHT
        else:
            self.set_font(FONT_FAMILY_SERIF, "", self.ANN_SIZE)
            line_h = self.ANN_HEIGHT
        for line in self._annotation_lines(ann):
            line_bidi = bidi(line)
            w = self.get_string_width(line_bidi)
            # Center within the column, but never let a near-column-wide
            # line (the two Tisha B'Av time lines are ~93 mm wide) start
            # left of the body frame — with COLUMN_SHIFT_LEFT pulling the
            # columns leftward, a plain column-centered cell would poke
            # past the left end of the top/bottom rules.
            line_x = max(x + (col_w - w) / 2, self.LEFT_MARGIN + 0.4)
            self.set_xy(line_x, y)
            self.cell(w, line_h, line_bidi, align="C")
            y += line_h
        return y

    # ── Item-height + balancing logic ──────────────────────────────
    def _item_height(self, item: LuachItem) -> float:
        if isinstance(item, LuachRow):
            return self.ROW_HEIGHT
        # AnnotationRow — count visual lines × per-line height.
        # DST annotations use a taller line height than the default
        # so the banner-style typography is reflected in column
        # balancing.
        n_lines = len(self._annotation_lines(item))
        if item.kind in ("dst_start", "dst_end"):
            return self.DST_LINE_HEIGHT * n_lines
        return self.ANN_HEIGHT * n_lines

    def _compute_column_break(self, items: list[LuachItem]) -> int:
        """Return the index ``i`` at which to split items into
        column 1 (items[:i]) and column 2 (items[i:]).

        Strategy:
          • Pre-measure each item's height (multi-line annotations
            count as their full height).
          • Compute cumulative heights.
          • Valid break points are positions where the item at ``i``
            **starts a new row-group** — i.e. either:
              (a) ``items[i]`` is a ``LuachRow`` and ``items[i-1]`` is
                  NOT a "before" annotation, OR
              (b) ``items[i]`` is a "before" annotation and
                  ``items[i-1]`` is NOT also a "before" annotation.
            Case (b) opens up many more candidate splits while still
            keeping each row-group (one or more "before" annotations
            + their row) intact: the whole group moves to col2
            together rather than being split across columns.
          • Pick the valid break whose ``|cum_before - total/2|`` is
            smallest.
        """
        if not items:
            return 0
        heights = [self._item_height(it) for it in items]
        total = sum(heights)
        target = total / 2

        # cum[i] = sum of heights[0:i], i.e. the column-1 height if we
        # break at index i.
        cum = [0.0]
        for h in heights:
            cum.append(cum[-1] + h)

        best_idx = len(items)
        best_diff = float("inf")
        for i in range(1, len(items)):
            cur = items[i]
            prev = items[i - 1]
            prev_is_before_ann = (
                isinstance(prev, AnnotationRow)
                and prev.position == "before"
            )
            # Case (a): break at a LuachRow not preceded by a
            # "before" annotation. This is the original behavior —
            # the row is the start of its own row-group.
            if isinstance(cur, LuachRow):
                if prev_is_before_ann:
                    continue  # would orphan the preceding ann
                valid = True
            # Case (b): break at the FIRST "before" annotation in a
            # group. The whole row-group (one-or-more before-anns +
            # row + any after-anns) moves to col2 together.
            elif isinstance(cur, AnnotationRow) and cur.position == "before":
                if prev_is_before_ann:
                    continue  # we're already inside a before-ann group
                valid = True
            else:
                continue  # other annotations don't begin row-groups
            if not valid:
                continue
            diff = abs(cum[i] - target)
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        # Safety net: if nothing matched (extremely unlikely — there's
        # always at least one un-prefixed row position to break at),
        # fall back to roughly the middle by item count.
        if best_idx == len(items):
            best_idx = max(1, len(items) // 2)
        return best_idx

    # ── Main rendering entry point ─────────────────────────────────
    def render_body(self, items: Iterable[LuachItem]) -> None:
        """Draw watermark + title block + both columns of body."""
        self._draw_watermark()
        body_top = self._draw_title_block()
        # Body geometry
        usable_w = self.PAGE_WIDTH_MM - self.LEFT_MARGIN - self.RIGHT_MARGIN
        self._col_width = (usable_w - self.COL_GAP) / 2
        # Right column right edge = page's right side minus right margin,
        # pulled left by COLUMN_SHIFT_LEFT to optically center the ink
        # between the rules (see the constant's comment).
        right_col_right = (
            self.PAGE_WIDTH_MM - self.RIGHT_MARGIN - self.COLUMN_SHIFT_LEFT
        )
        # Left column right edge = right column left edge minus gap
        left_col_right = right_col_right - self._col_width - self.COL_GAP
        self._col1_x_right = right_col_right     # column 1 = right column
        self._col2_x_right = left_col_right      # column 2 = left column
        self._body_top = body_top
        self._body_height = (
            self.PAGE_HEIGHT_MM - self.BOTTOM_MARGIN - self._body_top
        )

        items_list = list(items)
        split_idx = self._compute_column_break(items_list)
        col1_items = items_list[:split_idx]
        col2_items = items_list[split_idx:]

        # SF-style bottom alignment: both columns finish at the same
        # horizontal line (the closing double-rule). The taller
        # column determines that line; the shorter column gets the
        # leftover space pushed in BEFORE its first body item, so the
        # gap sits just under the column headers and is visually
        # innocuous. This way, no matter the year — whether there
        # are double parshas, two Adars, an extra mevorchim line, or
        # any other content swing — the bottom rule is always even
        # across both columns.
        col1_h = sum(self._item_height(it) for it in col1_items)
        col2_h = sum(self._item_height(it) for it in col2_items)
        body_height = max(col1_h, col2_h)
        col1_top_pad = body_height - col1_h  # 0 if col1 is taller
        col2_top_pad = body_height - col2_h  # 0 if col2 is taller

        # Column 1 (right) — start of the year
        col1_end_y = self._draw_column(
            col1_items, x_right=self._col1_x_right, top_pad=col1_top_pad,
            is_first_column=True,
        )
        # Column 2 (left) — rest of the year
        col2_end_y = self._draw_column(
            col2_items, x_right=self._col2_x_right, top_pad=col2_top_pad,
            is_first_column=False,
        )

        # SF-style closing double rule just below the (now-aligned)
        # column bottoms. A small breathing gap separates the last
        # row from the rule so it doesn't crowd the text above.
        body_end_y = max(col1_end_y, col2_end_y) + 1.0
        self._draw_double_rule(body_end_y)

    def _draw_column(
        self,
        items: list[LuachItem],
        *,
        x_right: float,
        top_pad: float = 0.0,
        is_first_column: bool = False,
    ) -> float:
        """Draw a single column: its small header row + interleaved
        rows and annotations. Returns the final y immediately below
        the last item drawn in this column.

        ``top_pad`` is extra blank space inserted between the column
        headers and the first body item — used by ``render_body`` to
        align the bottoms of the two columns across the page width.

        ``is_first_column`` flags the right column (start of the year);
        propagated to ``_draw_column_header`` so the SF-style "עלות
        השחר אינו זמן הנחת תפילין" disclaimer prints only there.
        """
        y = self._draw_column_header(
            x_right, self._body_top, is_first_column=is_first_column,
        )
        # Small breathing space below the header labels.
        y += 0.5
        # Bottom-alignment padding (zero for the taller column).
        y += top_pad
        for item in items:
            if isinstance(item, LuachRow):
                self._draw_row(item, x_right=x_right, y=y)
                y += self.ROW_HEIGHT
            else:
                # _draw_annotation handles the mevorchim two-line
                # case internally and returns the y immediately below.
                y = self._draw_annotation(item, x_right=x_right, y=y)
            # Hard-stop if we would overflow the page. This is a
            # defensive safety — if the column-balance miscalculates
            # we'd rather truncate gracefully than push content into
            # the bottom margin / off the page.
            if y > self.PAGE_HEIGHT_MM - self.BOTTOM_MARGIN:
                break
        return y
