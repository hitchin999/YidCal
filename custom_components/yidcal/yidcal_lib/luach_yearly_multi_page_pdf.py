"""
custom_components/yidcal/yidcal_lib/luach_yearly_multi_page_pdf.py

Renders the "Yearly multi-page" (Monroe-style) luach as a PDF.

Layout: US Letter portrait, RTL Hebrew table. Each LuachRow is a single row
showing parsha/Erev-YT title, Hebrew + civil dates, candle lighting,
motzei, and configurable extra zmanim columns (alos, sof zman shma,
etc.). Annotation rows (Mevorchim, Tekufah, fast times, etc.) span
the full width as a single grey-shaded text bar.

Pages break automatically; the table header repeats at the top of
each page.

The input is a list of LuachItem (LuachRow or AnnotationRow) produced
by luach_data.build_luach().
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable

from fpdf import FPDF

from .luach_data import LuachRow, AnnotationRow, LuachItem
from .luach_pdf_common import (
    register_fonts, bidi, FONT_FAMILY,
    register_watermark_font, draw_watermark,
)


# Default extra-zmanim columns (in Hebrew, matching zman_compute labels).
# These are the two extras Monroe shows beyond Candle + Motzei.
DEFAULT_EXTRA_ZMANIM = ("עלות השחר", "סוף זמן קריאת שמע מג״א")


def _fmt_time_12(dt: datetime) -> str:
    """12-hour 'H:MM' (no AM/PM — the luachs leave it implicit)."""
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d}"


def render_yearly_multi_page_pdf(
    *,
    items: list[LuachItem],
    output_path: Path,
    title_he: str = "",
    subtitle_he: str = "",
    notes_he: str = "",
    extra_zmanim_labels: tuple[str, ...] = DEFAULT_EXTRA_ZMANIM,
    diaspora: bool = True,
) -> None:
    """Render ``items`` to a PDF at ``output_path``.

    Caller is responsible for providing already-assembled rows in
    chronological order (see luach_data.build_luach).

    ``notes_he``, when non-empty, is rendered as a smaller Hebrew
    line beneath the subtitle. Used to describe the candle-lighting
    / havdalah convention in effect (e.g. ``זמן הדלקת הנרות 15 מינוט
    קודם השקיעה, וזמן מוצש"ק הוא לפי שיטת ר"ת``).
    """
    pdf = _YearlyMultiPagePDF(
        title_he=title_he,
        subtitle_he=subtitle_he,
        notes_he=notes_he,
        extra_zmanim_labels=extra_zmanim_labels,
    )
    pdf.set_auto_page_break(auto=True, margin=15)
    register_fonts(pdf)
    register_watermark_font(pdf)
    pdf.add_page()
    pdf.render_table(items)
    pdf.output(str(output_path))


class _YearlyMultiPagePDF(FPDF):
    """Internal: subclass of FPDF that knows the Yearly multi-page luach layout
    (column geometry, header, row drawing).
    """

    # Page geometry (US Letter portrait, mm)
    PAGE_WIDTH_MM = 215.9         # US Letter width (8.5 in)
    LEFT_MARGIN = 12
    RIGHT_MARGIN = 12

    # Row heights
    ROW_HEIGHT = 7
    HEADER_HEIGHT = 9
    ANN_HEIGHT = 6

    # Font sizes — 4 functional tiers to match the printed MSM/KJ
    # luach's visual hierarchy:
    #   PARSHA_SIZE: parsha names, Erev-YT main labels, Hebrew dates,
    #                all times (candle/motzei/zmanim) — rendered BOLD
    #   PAREN_SIZE:  parenthesized title qualifiers (e.g. '(שבת ר״ח)',
    #                '(פרק א׳)'), civil date column, annotation rows —
    #                rendered REGULAR
    #   HEADER_SIZE: column headers — same size as PAREN_SIZE, BOLD
    #   TITLE_SIZE / SUBTITLE_SIZE: page banner at top of page 1
    TITLE_SIZE = 14
    SUBTITLE_SIZE = 10
    PARSHA_SIZE = 11
    PAREN_SIZE = 9
    HEADER_SIZE = 10
    SMALL_SIZE = 7             # footer page-number only

    def __init__(
        self,
        *,
        title_he: str,
        subtitle_he: str,
        notes_he: str = "",
        extra_zmanim_labels: tuple[str, ...],
    ) -> None:
        super().__init__(orientation="portrait", unit="mm", format="Letter")
        self.set_margins(self.LEFT_MARGIN, 12, self.RIGHT_MARGIN)
        self.title_he = title_he
        self.subtitle_he = subtitle_he
        self.notes_he = notes_he
        self.extra_zmanim_labels = tuple(extra_zmanim_labels)
        self._columns = self._build_columns()

    # Standard short labels for the most common extra zmanim. The user
    # can override these by passing custom labels in extra_zmanim_labels,
    # but if they pass the canonical zman_compute label we substitute the
    # printed-luach abbreviation for the column header.
    _SHORT_HEADER = {
        "עלות השחר":                "עלות השחר",
        "זמן טלית ותפילין":         "טלית ותפילין",
        "הנץ החמה":                 "הנץ",
        "סוף זמן קריאת שמע מג״א":   "סוף זמן קר״ש",
        "סוף זמן קריאת שמע גר״א":   "סוף זמן קר״ש גר״א",
        "סוף זמן תפילה מג״א":       "סוף זמן תפילה",
        "סוף זמן תפילה גר״א":       "סוף זמן תפילה גר״א",
        "חצות היום":                "חצות",
        "מנחה גדולה":               "מנחה גדולה",
        "מנחה קטנה":                "מנחה קטנה",
        "פלג המנחה גר״א":           "פלג גר״א",
        "פלג המנחה מג״א":           "פלג מג״א",
        "שקיעת החמה":               "שקיעה",
        "צאת הכוכבים":              "צאה״כ",
        "זמן מעריב 60":             "מעריב 60",
        "חצות הלילה":               "חצות הלילה",
    }

    # ── Column geometry ────────────────────────────────────────────────
    def _build_columns(self) -> list[tuple[str, str, float]]:
        """Return list of (key, header_he, width_mm) in RTL display
        order. The rightmost column is index 0; cells are drawn from
        right to left so index 0 sits at the page's right edge.

        Header text uses '\\n' to mark a two-line wrap point so narrow
        columns can fit a full Hebrew label without abbreviation.

        Empty headers are intentional for the Hebrew-date and civil-date
        columns: the column values themselves are self-explanatory, and
        Monroe's printed luach also leaves them unlabelled.
        """
        cols: list[tuple[str, str, float]] = [
            ("title", "פרשיות / ימים טובים", 65),
            ("hebrew_date", "", 25),
            ("civil_date", "", 22),
            ("candle", "הדלקת\nהנרות", 19),
            ("motzei", "מוצאי\nשב״ק", 19),
        ]
        # Extras (one column per requested label). All extras at same width.
        for label in self.extra_zmanim_labels:
            short = self._SHORT_HEADER.get(label, label)
            # Auto-wrap multi-word headers onto two lines for readability
            # in narrow columns. Top-heavy split: for 3 words → 2+1
            # (e.g., 'סוף זמן' / 'קר״ש'), 5 words → 3+2.
            words = short.split(" ")
            if len(words) >= 2:
                mid = (len(words) + 1) // 2
                short = " ".join(words[:mid]) + "\n" + " ".join(words[mid:])
            cols.append((f"z::{label}", short, 16))
        return cols

    def _table_width(self) -> float:
        return sum(w for _, _, w in self._columns)

    def _table_x_right(self) -> float:
        """The RIGHT edge of the table (= where column index 0 ends)."""
        usable = self.PAGE_WIDTH_MM - self.LEFT_MARGIN - self.RIGHT_MARGIN
        tw = self._table_width()
        # Center the table within the usable width
        slack = max(0, usable - tw)
        return self.PAGE_WIDTH_MM - self.RIGHT_MARGIN - slack / 2

    # ── Page header (top of each page) ─────────────────────────────────
    def header(self) -> None:
        # Watermark first so subsequent content overlays it cleanly
        self._draw_watermark()
        if self.page_no() == 1 and self.title_he:
            self.set_font(FONT_FAMILY, "B", self.TITLE_SIZE)
            self.cell(0, 8, bidi(self.title_he), align="C",
                      new_x="LMARGIN", new_y="NEXT")
            if self.subtitle_he:
                self.set_font(FONT_FAMILY, "", self.SUBTITLE_SIZE)
                self.cell(0, 6, bidi(self.subtitle_he), align="C",
                          new_x="LMARGIN", new_y="NEXT")
            if self.notes_he:
                # Convention notes (candle / havdalah offsets) — set
                # one step smaller than the subtitle so it reads as
                # secondary information without competing with the
                # location subtitle.
                self.set_font(FONT_FAMILY, "", self.PAREN_SIZE)
                self.cell(0, 5, bidi(self.notes_he), align="C",
                          new_x="LMARGIN", new_y="NEXT")
            self.ln(2)
        # Column header row
        self._draw_column_headers()

    def _draw_watermark(self) -> None:
        """Draw the shared "Generated by YidCal" watermark up the left
        edge of every page (Archivo SemiBold, light grey, rotated,
        vertically centred — identical to the Weekly YidCal card and the
        yearly-sheet style). See ``luach_pdf_common.draw_watermark``.
        """
        draw_watermark(self)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font(FONT_FAMILY, "", self.SMALL_SIZE)
        self.cell(0, 8, f"{self.page_no()} / {{nb}}", align="C")

    def _draw_column_headers(self) -> None:
        """Draw the column-header row. Supports two-line headers via
        ``\\n`` in the header text — each line is centered vertically
        within the (taller) header cell.
        """
        self.set_font(FONT_FAMILY, "B", self.HEADER_SIZE)
        self.set_fill_color(220, 220, 220)
        self.set_draw_color(120, 120, 120)
        x_right = self._table_x_right()
        y = self.get_y()
        x = x_right
        # Taller header row to fit two lines comfortably
        header_h = self.HEADER_HEIGHT * 1.5
        for _key, header_he, width in self._columns:
            x -= width
            self.set_xy(x, y)
            lines = header_he.split("\n")
            if len(lines) <= 1:
                # Single-line header — draw normally
                self.cell(width, header_h, bidi(header_he),
                          border=1, align="C", fill=True)
            else:
                # Two-line header: draw the cell border + fill ONCE
                # (empty), then draw each text line at a tight vertical
                # spacing (avoids the large gap that comes from naïvely
                # dividing the full header height by the line count —
                # which makes each line's cell so tall that the
                # vertically-centered text floats with ~3mm of padding
                # above and below, producing a visible empty band
                # between the two lines).
                self.cell(width, header_h, "", border=1, align="C", fill=True)
                line_h = 4.5
                total_text_h = line_h * len(lines)
                y_start = y + (header_h - total_text_h) / 2
                for i, line in enumerate(lines):
                    if not line:
                        continue
                    self.set_xy(x, y_start + i * line_h)
                    self.cell(width, line_h, bidi(line),
                              border=0, align="C", fill=False)
        self.set_y(y + header_h)

    # ── Body rendering ─────────────────────────────────────────────────
    def render_table(self, items: Iterable[LuachItem]) -> None:
        # Alternate row shading for legibility
        zebra = False
        for item in items:
            if isinstance(item, AnnotationRow):
                self._draw_annotation(item)
                zebra = False
                continue
            self._draw_row(item, zebra=zebra)
            zebra = not zebra

    def _draw_row(self, row: LuachRow, *, zebra: bool) -> None:
        """Draw one Erev/Shabbos row."""
        x_right = self._table_x_right()
        y = self.get_y()
        # Page-break protection — if a row would overflow, force a new page.
        if y + self.ROW_HEIGHT > self.h - 15:
            self.add_page()
            x_right = self._table_x_right()
            y = self.get_y()

        if zebra:
            self.set_fill_color(245, 245, 250)
        else:
            self.set_fill_color(255, 255, 255)
        self.set_draw_color(180, 180, 180)
        self.set_text_color(0, 0, 0)
        self.set_font(FONT_FAMILY, "", self.PAREN_SIZE)

        # Build value for each column (title is handled separately
        # via the split-style _draw_title_cell helper)
        values = self._row_values(row)

        x = x_right
        for (key, _header, width) in self._columns:
            x -= width
            if key == "title":
                self._draw_title_cell(x, y, width, row)
                continue
            self.set_xy(x, y)
            val, align, font_style = values[key]
            target_size = self._size_for(key)
            if font_style != self.font_style or self.font_size_pt != target_size:
                self.set_font(FONT_FAMILY, font_style, target_size)
            self.cell(width, self.ROW_HEIGHT, val, border=1,
                      align=align, fill=True)
        self.set_y(y + self.ROW_HEIGHT)

    def _draw_title_cell(self, x: float, y: float, width: float, row: LuachRow) -> None:
        """Render the title column with split styling: the main label
        (parsha name or 'ערב X' Erev-YT label) is BOLD at PARSHA_SIZE,
        and any parenthesized qualifiers ('(שבת ר״ח)', '(פרק א׳)',
        '(עירוב תבשילין)' etc.) are REGULAR at PAREN_SIZE.

        Implementation: draw the cell border + fill in one cell() call
        with empty content, then overlay each text segment at the
        correct x-offset for RTL right-alignment.

        Hebrew RTL note: bidi() reverses character order so that an LTR
        renderer (fpdf2) displays the text right-to-left visually. Each
        segment is bidi-encoded independently; segments compose
        correctly because each occupies its own [x, x+width] rectangle.
        Visual layout for "מקץ (חנוכה) (שבת ר״ח)":
          right edge        ← main →   gap   ← parens →   left edge
        """
        # Phase 1: border + fill (no content)
        self.set_xy(x, y)
        self.cell(width, self.ROW_HEIGHT, "", border=1, fill=True)

        main_text = row.title_he or ""
        suffix_parts: list[str] = []
        if row.pirkei_avos_he:
            suffix_parts.append(row.pirkei_avos_he)
        suffix_parts.extend(row.special_shabbos_he)
        if row.eruv_tavshilin:
            suffix_parts.append("עירוב תבשילין")
        parens_text = (
            "(" + ") (".join(suffix_parts) + ")"
        ) if suffix_parts else ""

        main_bidi = bidi(main_text) if main_text else ""
        parens_bidi = bidi(parens_text) if parens_text else ""

        # Compute widths (each font set must precede its measurement)
        if main_bidi:
            self.set_font(FONT_FAMILY, "B", self.PARSHA_SIZE)
            main_w = self.get_string_width(main_bidi)
        else:
            main_w = 0
        if parens_bidi:
            self.set_font(FONT_FAMILY, "", self.PAREN_SIZE)
            parens_w = self.get_string_width(parens_bidi)
        else:
            parens_w = 0

        # Right padding from the cell border. fpdf2's `cell()` adds its
        # own ~1mm c_margin when rendering text inside a cell, so a pad
        # of 2.5 here translates to roughly 1.5mm of visible space
        # between the rendered text and the cell's right border —
        # matching the breathing room of the pre-Run-5 standard-cell
        # rendering.
        pad = 2.5
        gap = 0.8  # space between main and parens
        right_edge = x + width - pad

        # Render main label (bold, big) — right edge of main = right_edge
        if main_bidi:
            self.set_font(FONT_FAMILY, "B", self.PARSHA_SIZE)
            self.set_xy(right_edge - main_w, y)
            self.cell(main_w, self.ROW_HEIGHT, main_bidi,
                      border=0, align="L", fill=False)

        # Render parens (regular, smaller) — right edge of parens =
        # right_edge - main_w - gap
        if parens_bidi:
            parens_right_edge = right_edge - main_w - (gap if main_w else 0)
            self.set_font(FONT_FAMILY, "", self.PAREN_SIZE)
            self.set_xy(parens_right_edge - parens_w, y)
            self.cell(parens_w, self.ROW_HEIGHT, parens_bidi,
                      border=0, align="L", fill=False)

    def _size_for(self, key: str) -> float:
        # Two tiers in the row body:
        #   • civil_date → PAREN_SIZE (small reference)
        #   • everything else (title handled separately, hebrew_date,
        #     times) → PARSHA_SIZE (emphasized)
        if key == "civil_date":
            return self.PAREN_SIZE
        return self.PARSHA_SIZE

    def _row_values(self, row: LuachRow) -> dict[str, tuple[str, str, str]]:
        """For each column key, return (cell_text, align, font_style).

        Note: the ``title`` column value is built here for completeness
        but isn't used in ``_draw_row`` — title rendering is custom
        (see ``_draw_title_cell``) so the main label and parenthesized
        qualifiers can be rendered with different weights and sizes.
        """
        # Title: combine title + pirkei + special + eruv (single-style
        # fallback string; not actually used in current rendering path)
        title_parts = [row.title_he]
        suffix_parts: list[str] = []
        if row.pirkei_avos_he:
            suffix_parts.append(row.pirkei_avos_he)
        suffix_parts.extend(row.special_shabbos_he)
        if row.eruv_tavshilin:
            suffix_parts.append("עירוב תבשילין")
        if suffix_parts:
            title_parts.append("(" + ") (".join(suffix_parts) + ")")
        title_str = " ".join(title_parts)

        out: dict[str, tuple[str, str, str]] = {
            "title":       (bidi(title_str), "R", "B"),
            "hebrew_date": (bidi(row.hebrew_date_he), "C", "B"),
            "civil_date":  (row.civil_date.strftime("%d-%b-%y"), "C", ""),
            "candle":      (_fmt_time_12(row.candle_lighting)
                            if row.candle_lighting else "", "C", "B"),
            "motzei":      (_fmt_time_12(row.motzei)
                            if row.motzei else "", "C", "B"),
        }
        # Extras (zmanim columns) — all times rendered BOLD
        for label in self.extra_zmanim_labels:
            dt = row.zmanim.get(label)
            out[f"z::{label}"] = (_fmt_time_12(dt) if dt else "", "C", "B")
        return out

    def _draw_annotation(self, ann: AnnotationRow) -> None:
        """Draw an annotation row as a full-width grey bar spanning all columns."""
        x_right = self._table_x_right()
        tw = self._table_width()
        y = self.get_y()
        if y + self.ANN_HEIGHT > self.h - 15:
            self.add_page()
            x_right = self._table_x_right()
            y = self.get_y()
        x_left = x_right - tw
        # Different shading for different annotation kinds
        if ann.kind == "mevorchim":
            self.set_fill_color(225, 225, 245)
        elif ann.kind == "tekufah":
            self.set_fill_color(245, 230, 220)
        elif ann.kind == "erev_pesach_chametz":
            self.set_fill_color(250, 245, 220)
        elif ann.kind in ("17_tammuz", "tisha_bav_a", "tisha_bav_b", "taanis_esther"):
            self.set_fill_color(240, 220, 220)
        else:
            self.set_fill_color(235, 235, 235)
        self.set_draw_color(180, 180, 180)
        self.set_text_color(0, 0, 0)
        self.set_font(FONT_FAMILY, "", self.PAREN_SIZE)
        self.set_xy(x_left, y)
        self.cell(tw, self.ANN_HEIGHT, bidi(ann.text_he),
                  border=1, align="C", fill=True)
        self.set_y(y + self.ANN_HEIGHT)
