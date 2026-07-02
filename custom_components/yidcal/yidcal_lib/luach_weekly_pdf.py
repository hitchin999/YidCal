"""
custom_components/yidcal/yidcal_lib/luach_weekly_pdf.py

Renders the "Weekly" single-card luach as a PDF.

Visual fidelity strategy
------------------------
The card's artistic chrome (the beveled LED-style time boxes, the
bullet-dot caps, the gradient header band, the small-box pills, the
table grid, the alternating row shading, the outer frame) is the
original Adobe-Illustrator artwork (``luach-01.svg``) with the
week-specific sample glyphs removed. Three blank variants ship as
pre-rasterised assets:

  * ``weekly_template_regular`` — no small boxes (plain Shabbos)
  * ``weekly_template_yt1``     — one small box  (1 extra candle)
  * ``weekly_template_yt2``     — two small boxes (2 extra candles)

Elements that must resize or appear conditionally — the weekday
pennants, the sefirah badges (only during the omer), the rotated
month tab AND the inverted קר״ש "boxed-column" tag — are drawn by
the renderer, not baked into the template. (The tag used to be
baked at a fixed x that was ~0.6 pt off the cell centre; it is now
drawn live, centred per cell for every row/year, and the shipped
rasters have the old baked rects removed.)

The renderer lays the chosen template over the page 1:1 with the
SVG's point coordinate system, scales the card sub-region up to fill
a Letter-landscape page, and overlays the live data as text at the
exact coordinates Adobe placed the originals. Text uses the bundled
Frank Ruehl CLM serif (the traditional-luach face already used by the
yearly-sheet renderer); the original BAPe'erot is outlined in the SVG
and not redistributable.

Input is a ``WeeklyData`` from ``luach_data.build_weekly_data()`` —
sourced from the same ``build_luach`` + ``compute_zmanim_for_date``
pipeline the yearly renderers use.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fpdf import FPDF
from PIL import Image

from .luach_data import WeeklyData, WEEKLY_ZMAN_COLUMNS, WEEKLY_BOXED_COLUMN
from .luach_pdf_common import (
    register_serif_fonts, bidi, FONT_FAMILY_SERIF, INFO_SEP,
)


def _assets_dir() -> Path:
    return Path(__file__).parent / "assets"


@lru_cache(maxsize=4)
def _card_image(tpl: str) -> Image.Image:
    """The template raster cropped to the ``.st0`` white-card rect.

    The shipped PNG is ~87% solid black surround with the card in the
    middle; compositing only this crop means the black is never drawn,
    so the page stays white around the card (no black band can appear
    regardless of fit / margin). The crop is taken from the card's
    exact SVG bounds (``_CARD``) mapped through the raster's own
    px-per-pt, so the cropped image's corners are precisely the
    affine images of (cx0,cy0)/(cx1,cy1) — anchors stay locked.
    """
    png = _assets_dir() / f"weekly_template_{tpl}.png"
    im = Image.open(png).convert("RGB")
    px_per_pt_x = im.width / 612.0
    px_per_pt_y = im.height / 792.0
    cx0, cy0, cx1, cy1 = _CARD
    left = round(cx0 * px_per_pt_x)
    top = round(cy0 * px_per_pt_y)
    right = round(cx1 * px_per_pt_x)
    bottom = round(cy1 * px_per_pt_y)
    return im.crop((left, top, right, bottom))


def template_available() -> bool:
    """True iff the weekly template rasters are present."""
    a = _assets_dir()
    return (a / "weekly_template_regular.png").exists()


# ── Exact SVG-space anchors (points; SVG viewBox 0 0 612 792) ──
#
# ``_CARD`` is the white card's TRUE bounds, taken VERBATIM from the
# source Adobe artwork's own card rectangle:
#     <rect x="135.98" y="271.44" width="340.04" height="249.12"/>
# (the ``.st0`` white-fill rect in ``weekly_template_*.svg`` — all
# three template variants share this identical rect, confirmed by
# pixel measurement of the shipped rasters). Earlier revisions used a
# deliberately PADDED, looser rectangle "so nothing clips"; that
# padding is exactly what produced the thin black rim that printed
# just inside every page edge. The card is now cropped to its real
# bounds so the surrounding black falls fully off-page.
#
# Why the renderer never needs its anchors re-tuned when _CARD
# changes: the template raster AND every overlay coordinate are both
# expressed in this same SVG point space and both pass through the
# identical _X/_Y/_S affine, so they move together. Tightening _CARD
# only re-scales/-pans the whole composite (template + overlays as
# one); it never shifts an anchor relative to the template feature it
# sits on. (The watermark is the sole intentional exception — it is
# pinned to the physical paper edge in page space, by design.)
#
# Page model — matches the PRINTED Kiryas-Joel luach: a framed card
# sitting on a WHITE page with a clean white margin all around it
# (the printed sheet has the frame border, then white space — that
# white band is where this renderer's left-edge watermark sits).
#
# To reproduce that the renderer (a) paints the whole page white,
# (b) draws ONLY the card sub-region of the template — the raster is
# cropped to the ``.st0`` white-card rect so its huge black surround
# is never composited at all (no black band can appear; the leftover
# page area is the white margin), and (c) fits the card inside the
# page MINUS a uniform ``_PAGE_MARGIN`` so a balanced white border
# rings the card. Because the card image is still placed at the
# affine image of its own SVG bounds and every overlay uses the same
# affine, all anchors stay locked to template features regardless of
# margin / fit (see the note above).
#
# Aspect note: the card is 340.04×249.12 (aspect 1.365) vs
# Letter-landscape 1.294, so a proportional fit can't be edge-tight
# on both axes — but that no longer matters: the slack is simply
# extra WHITE margin (more top/bottom than left/right), exactly like
# the printed sheet. ``_FIT_MODE``:
#   "contain" – uniform scale = min() into the margin box; no
#               distortion, no clipping, white border all around.
#               THE DEFAULT (matches the printed luach).
#   "cover"   – uniform scale = max(); fills the margin box, the
#               card's blank outer margin + frame hairline bleed off
#               L/R. Full-bleed look; no data clipped.
#   "stretch" – independent X/Y scale; exact fill, ~5.6% vertical
#               stretch (circles → slight ellipses).
# ``_PAGE_MARGIN`` is the white border width in PAGE points (the
# binding axis sits exactly this far from the paper edge; the other
# axis gets that plus the aspect slack). Tunable in isolation —
# nothing else needs editing.
_CARD = (135.98, 271.44, 476.02, 520.56)      # SVG ``.st0`` rect
_FIT_MODE = "contain"                         # contain | cover | stretch
_PAGE_MARGIN = 3.0                            # white border, page pt
# Left-edge watermark distance from the paper edge (page pt). Pulled
# in from the old 14 pt so that even at a very tight _PAGE_MARGIN the
# sideways watermark still sits in clear white space, clear of the
# card's frame rule (the card's own ~17 pt inner white margin keeps
# the buffer). Raise this if _PAGE_MARGIN is widened a lot.
_WATERMARK_X = 7.0

# White strip above the grey band (place / year line). The white
# zone is between the inner double-rule frame line (card-y ≈278.75)
# and the grey header band's top edge (≈290.4) → zone centre ≈284.6.
# _STRIP_Y is calibrated so the rendered glyph's optical centre
# lands there (fpdf draws the glyph ~0.2 pt low within its line box).
_STRIP_RIGHT, _STRIP_Y = 463.0, 284.8

# Hero-band horizontal centre — the SAME x the big hero title is
# centred on ((366.14+468.42)/2). The place/year strip ABOVE the
# hero band and the תקופה strip BELOW it are both centred here, so
# they line up with the hero text (left/right centred, not edge-hugged).
_HEADER_CX = (366.14 + 468.42) / 2.0          # ≈ 417.28

# White strip BELOW the grey hero band — the exact visual mirror of
# _STRIP_Y above it. Raster-measured at the hero-centre column:
# the hero band's thin bottom line ends at card-y ≈341.5 and the
# info-ribbon's top edge starts at ≈360.2 → white-gap centre ≈350.85.
# +0.25 is the SAME optical calibration the top place/year strip uses
# (its zone centre ≈284.55, _STRIP_Y 284.8), so both strips sit
# optically centred in their gaps identically.
_STRIP_BOT_Y = 351.1

# Grey band — hero (parsha / Erev-YT) + eruv sub-line, CENTRED in the
# gap between the right big box (≈x366) and the card edge (≈x476).
_HERO_CX = 415.0
# The grey hero band (where the parsha / Yom-Tov title + its sub
# line(s) sit) spans card-y ≈293.1 (top, below the thin highlight
# rule) → ≈338.5 (bottom). The hero block — the big title PLUS any
# sub line(s) below it — is centred as ONE group on the band's
# vertical centre, so a 1-line title sits dead-centre and a 2- or
# 3-line block straddles the centre evenly (no longer hanging low
# off a fixed baseline).
_HERO_BAND_TOP, _HERO_BAND_BOT = 293.1, 338.5
_HERO_BAND_MID = (_HERO_BAND_TOP + _HERO_BAND_BOT) / 2.0   # ≈315.8
# Erev-Pesach chametz black box (renderer-drawn, only when present).
# It occupies the lower part of the band, so when shown the hero
# group is centred in the REDUCED band above the box instead.
# Erev-Pesach chametz black box (renderer-drawn, only when present).
# Centred on the hero band's horizontal centre (≈417.28) so it aligns
# with the 'ערב פסח' title and 'עירוב תבשילין' sub-line directly
# above it. Width was tightened to 76 pt — the new shorter line text
# ('סוף זמן אכילת חמץ X' / 'סוף זמן שריפת חמץ Y') fits comfortably
# at size 7.6 + track=92 with the box no wider than necessary.
_CHAMETZ_BOX = (379.28, 327.0, 76.0, 22.0)     # x, y, w, h
_HERO_BAND_BOT_CH = 325.0                      # box top − small gap
_HERO_BAND_MID_CH = (_HERO_BAND_TOP + _HERO_BAND_BOT_CH) / 2.0
# Legacy single-baseline constants kept for any other references.
_HERO_Y = 314.0
_ERUV_Y = 330.0
_HERO_Y_CH, _ERUV_Y_CH = 304.0, 320.0

# Big time boxes (interiors; from the st3 heavy-border paths)
_BIG_R = (270.4, 287.1, 95.7, 57.7)           # candle lighting (right)
_BIG_L = (157.0, 287.1, 95.8, 57.7)           # motzei (left)
_CAP_R_CX, _CAP_L_CX, _CAP_CY = 318.3, 204.9, 286.3

# Small boxes (st1 pill + black inner). Inner rects measured from
# the SVG (the time is centred in the inner; label fills the grey).
_SM_L = (157.0, 355.1, 95.8, 21.8)
_SM_R = (270.4, 355.1, 95.8, 21.8)
_SM_L_INNER = (160.07, 357.31, 40.21, 17.76)  # x, y, w, h
_SM_R_INNER = (273.46, 357.31, 40.21, 17.76)

# Info / molad / kiddush-levana line (band between boxes and table)
# Vertical centre of the grey 'tube' ribbon band (measured from the
# rasterised template: dark edges at card-y 360.55 / 371.42 → centre
# 365.99). _INFO_Y is calibrated so the rendered glyph's optical
# centre lands on the band centre (fpdf draws the glyph ~0.17 pt low
# within its line box; _INFO_Y compensates so no per-call rise is
# needed).
_INFO_Y = 366.16
_INFO_OPT_RISE = 0.0
_INFO_RIGHT = 462.0

# Table grid
_COL_EDGES = [
    149.32, 169.68, 189.84, 209.52, 229.68, 250.08, 270.24, 289.68,
    310.08, 330.72, 350.88, 370.56, 390.65, 408.43, 435.88,
]
_HDR_Y1, _HDR_Y2 = 390.6, 397.4               # 2-line header baselines
_ROW_CY = [411.43, 424.48, 437.21, 450.18, 462.98, 475.79, 488.75]
_ROW_H = 12.7

# The inverted "boxed" column (סוף זמן קר״ש) used to be baked into the
# template raster at a fixed x (334.07) that was ~0.6 pt off the cell
# centre, so box + text read as misaligned. It is now drawn live by
# the renderer, perfectly centred on the cell for every row/year; the
# templates ship with the baked rects removed. Size kept identical to
# the original Adobe art so the visual weight matches the print luach.
_BOXED_W, _BOXED_H = 14.09, 9.16

# Right-edge strip (renderer-drawn). Day-tab pennant geometry taken
# VERBATIM from the original Adobe st9 path (x437.83→457.53, 9.17 pt
# tall, 2 pt #827C77 stroke, 2.49 pt right-corner radius). This is
# the artwork's own pennant outline, so it aligns exactly with the
# baked grid and tail/notch: the flat left edge sits ~2 pt clear of
# the table block right edge (x435.88) — the grey border meets the
# grid without overlapping the black grid lines. (Run 22–23 had
# shifted this right to 446.8/461.3/SW1.1, which floated the pill
# off its notch and onto the grid in the final render; reverted.)
# Left edge stays on the native st9 x (437.83) so the flat side sits
# ~2 pt clear of the table grid. The RIGHT edge is pulled in from the
# native 457.53 → 452.0 (the day text has spare room) and the grey
# stroke thinned to 1.2 pt, so the black/grey pill no longer crowds
# the dynamic sefirah circles — they now sit cleanly on the grid.
_PEN_X0, _PEN_X1 = 437.83, 452.0              # flat left edge → round right
_PEN_R = 2.49                                 # right-end corner radius
_PEN_H = 9.17                                 # bar height
_PEN_SW = 1.2                                 # grey border width (thinned)
# Sefirah badge + ספירה tab shifted LEFT ~7 pt into the space freed
# by removing the baked pennant, so the circles and the rotated
# ספירה title sit comfortably inside the page frame (x…476) instead
# of hanging off the right edge — with a ~3 pt gap from the pennant
# (right edge x452).
_BADGE_CX, _BADGE_R = 459.5, 4.6              # circle, aligned w/ ספירה
_TAB_X0, _TAB_X1, _TAB_Y0, _TAB_Y1 = 455.6, 463.9, 384.71, 403.30
_TAB_PT_Y = 406.5                             # point tip y

# Two-line header splits (RTL display label → (upper, lower))
_HDR_SPLIT = {
    "יום החודש":   ("יום", "החודש"),
    "למספ׳":       ("", "למספ׳"),
    "עלות השחר":   ("עלות", "השחר"),
    "הנץ החמה":    ("נץ", "החמה"),
    "סוף זמן קר״ש": ("סוף זמן", "קר״ש"),
    "סוזק״ש גר״א":  ("סוזק״ש", "גר״א"),
    "סוף זמן תפלה": ("סוף זמן", "תפלה"),
    "סוז״ת גר״א":   ("סוז״ת", "גר״א"),
    "חצות היום":    ("חצות", "היום"),
    "מנחה גדולה":   ("מנחה", "גדולה"),
    "מנחה קטנה":    ("מנחה", "קטנה"),
    "פלג המנחה":    ("פלג", "המנחה"),
    "שקיעת החמה":   ("שקיעת", "החמה"),
    "צאה״כ":        ("", "צאה״כ"),
}


def _col_center(idx_from_right: int) -> tuple[float, float]:
    """(centre-x, width). idx 0 = rightmost (יום החודש),
    1 = למספ׳, 2.. = the 12 zmanim columns."""
    n = len(_COL_EDGES) - 1
    cell = n - 1 - idx_from_right
    x0, x1 = _COL_EDGES[cell], _COL_EDGES[cell + 1]
    return (x0 + x1) / 2.0, (x1 - x0)


def render_weekly_pdf(
    *,
    weekly: WeeklyData,
    output_path: Path,
    title_he: str = "",
    subtitle_he: str = "",
    notes_he: str = "",
) -> None:
    """Render ``weekly`` onto the single-card template at ``output_path``."""
    n_small = sum(1 for b in weekly.boxes if not b.big)
    pdf = _WeeklyPDF(
        community_strip_he=title_he, notes_he=notes_he, n_small=n_small,
    )
    pdf.set_auto_page_break(auto=False)
    register_serif_fonts(pdf)
    pdf.add_page()
    pdf.render_card(weekly)
    pdf.output(str(output_path))


def render_weekly_pdf_multi(
    *,
    weeks: list,
    output_path: Path,
) -> None:
    """Render a multi-page weekly booklet — one KY card per page.

    ``weeks`` is a list of ``(WeeklyData, title_he, notes_he)`` tuples
    in print order. Each page picks its own template (regular / yt1 /
    yt2) from that week's small-box count and carries its own
    community-strip / notes line, so an Erev-YT week with extra
    candle boxes renders correctly alongside ordinary weeks.
    """
    if not weeks:
        raise ValueError("render_weekly_pdf_multi: no weeks given")
    pdf = _WeeklyPDF(community_strip_he="", notes_he="", n_small=0)
    pdf.set_auto_page_break(auto=False)
    register_serif_fonts(pdf)
    for weekly, title_he, notes_he in weeks:
        pdf.community_strip_he = title_he or ""
        pdf.notes_he = notes_he or ""
        pdf.n_small = sum(1 for b in weekly.boxes if not b.big)
        pdf.add_page()
        pdf.render_card(weekly)
    pdf.output(str(output_path))


class _WeeklyPDF(FPDF):
    """Composites the chosen Adobe template raster with a text +
    vector overlay, scaling the card up to fill a Letter-landscape
    page via a single affine."""

    PAGE_W = 792   # Letter landscape
    PAGE_H = 612
    FONT = FONT_FAMILY_SERIF

    def __init__(self, *, community_strip_he="", notes_he="", n_small=0):
        super().__init__(orientation="landscape", unit="pt",
                         format="Letter")
        self.community_strip_he = community_strip_he
        self.notes_he = notes_he
        self.n_small = n_small
        self.set_margins(0, 0, 0)
        self.set_auto_page_break(False)
        self.set_title("YidCal Weekly Luach")
        cx0, cy0, cx1, cy1 = _CARD
        cw, ch = cx1 - cx0, cy1 - cy0
        # Fit into the page MINUS a uniform white border so the framed
        # card is ringed by clean white space (printed-luach look).
        avail_w = self.PAGE_W - 2.0 * _PAGE_MARGIN
        avail_h = self.PAGE_H - 2.0 * _PAGE_MARGIN
        fx, fy = avail_w / cw, avail_h / ch
        if _FIT_MODE == "cover":
            self._sx = self._sy = max(fx, fy)
        elif _FIT_MODE == "stretch":
            self._sx, self._sy = fx, fy
        else:  # "contain" (default): no clip, no skew, white border
            self._sx = self._sy = min(fx, fy)
        # Scalar scale for sizes / strokes / corner radii. Equals the
        # uniform scale for contain & cover; geometric mean for
        # stretch so glyph weight and line widths stay sane.
        self._s = (self._sx * self._sy) ** 0.5
        self._ox = (self.PAGE_W - cw * self._sx) / 2.0 - cx0 * self._sx
        self._oy = (self.PAGE_H - ch * self._sy) / 2.0 - cy0 * self._sy

    # affine — template raster and every overlay share this transform,
    # so anchors stay locked to template features across any _CARD /
    # _FIT_MODE change (see the _CARD header note).
    def _X(self, x): return x * self._sx + self._ox
    def _Y(self, y): return y * self._sy + self._oy
    def _S(self, v): return v * self._s

    def _t(self, cx, mid_y, s, *, size, color=(0, 0, 0), anchor="C",
           rtl=True, max_w=None, bold=True, track=None):
        if s is None or s == "":
            return
        size = self._S(size)
        mw = self._S(max_w) if max_w else None
        sty = "B" if bold else ""
        self.set_font(self.FONT, sty, size)
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
                self.set_font(self.FONT, sty, size)
                _guard += 1
        wd = self.get_string_width(disp)
        px, py = self._X(cx), self._Y(mid_y)
        # Use fpdf's own cell alignment relative to a box anchored on
        # (px) — robust against Hebrew/bidi advance-width quirks that
        # biased the previous manual-x + left-align approach.
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

    def _adaptive_hero_track(
        self, text: str, *, size: float, max_w: float,
        base: int = 82, floor: int = 60,
    ) -> int:
        """Pick a ``set_stretching`` value for the hero title so the
        full text fits at the FULL requested ``size`` (no font
        auto-shrink) instead of shrinking the font to a smaller size.

        Used only for the big hero line so short parshas keep their
        familiar condensed look at the current default (``base``, e.g.
        82%) while long titles ('ערב ראש השנה' etc.) get a lower
        track so they too render at full hero size — visually
        consistent with the short-title look instead of appearing
        smaller. ``floor`` caps how tight the stretch can get; below
        that the normal ``_t()`` font-shrink fallback takes over for
        any title that's still wider than ``max_w``.
        """
        if not text:
            return base
        disp = bidi(text)
        scaled_size = size * self._s
        scaled_mw = max_w * self._s
        # Measure at track=100 (natural width) once, then compute the
        # stretch needed to fit. Always set the font BEFORE measuring
        # (fpdf needs it for current set_font state).
        self.set_font(self.FONT, "B", scaled_size)
        self.set_stretching(100)
        w100 = self.get_string_width(disp)
        if w100 <= 0:
            self.set_stretching(100)
            return base
        # Required stretch (%) to fit exactly in max_w. Cap at the
        # base track (don't STRETCH short titles wider than the base
        # condense) and floor it so letters stay readable.
        needed = int(round(100.0 * scaled_mw / w100))
        track = max(floor, min(base, needed))
        self.set_stretching(100)            # reset state for caller
        return track

    def _rrect(self, x, y, w, h, r, fill):
        self.set_fill_color(*fill)
        self.rect(self._X(x), self._Y(y), self._S(w), self._S(h),
                  style="F", round_corners=True,
                  corner_radius=self._S(r))

    def _fill_rect(self, x, y, w, h, fill):
        """Plain (sharp-corner) filled rect in SVG-space coords."""
        self.set_fill_color(*fill)
        self.rect(self._X(x), self._Y(y), self._S(w), self._S(h),
                  style="F")

    def render_card(self, weekly: WeeklyData) -> None:
        tpl = {0: "regular", 1: "yt1", 2: "yt2"}.get(
            min(self.n_small, 2), "regular")
        cx0, cy0, cx1, cy1 = _CARD
        # White page so the area around the card is clean white margin
        # (the printed-luach border space, where the watermark sits) —
        # never the template's black surround.
        self.set_fill_color(255, 255, 255)
        self.rect(0, 0, self.PAGE_W, self.PAGE_H, style="F")
        # Draw ONLY the card (raster cropped to the .st0 rect) at the
        # affine image of its own SVG bounds, so every template feature
        # lands exactly where the shared affine puts the overlays.
        self.image(_card_image(tpl), self._X(cx0), self._Y(cy0),
                   (cx1 - cx0) * self._sx, (cy1 - cy0) * self._sy)
        self._header(weekly)
        self._table(weekly)
        self._right_strip(weekly)
        if self.notes_he:
            self._t(306, 508, self.notes_he, size=6.4, anchor="C",
                    max_w=322, track=78)
        self._draw_watermark()

    # ── Watermark (rotated, left edge, every page) ──
    def _draw_watermark(self) -> None:
        """Sideways grey watermark along the left edge — identical
        text / colour / size to the two yearly luachs (just adapted
        to this renderer's point units and Letter-landscape page).
        Drawn last so it overlays the template cleanly; uses text()
        so it never disturbs the layout cursor.
        """
        text = "Generated by YidCal integration on Home Assistant"
        with self.local_context():
            self.set_font(self.FONT, "", 7)
            self.set_text_color(200, 200, 200)
            text_w = self.get_string_width(text)
            cx = _WATERMARK_X               # distance from paper edge
            cy = (self.h + text_w) / 2.0    # vertically centred
            with self.rotation(90, cx, cy):
                self.text(cx, cy, text)

    # ── header band ──
    def _header(self, w: WeeklyData) -> None:
        # place / year — white strip above the grey band, centred on
        # the hero-band centre (matches the hero title's centring).
        self._t(_HEADER_CX, _STRIP_Y, self.community_strip_he,
                size=6.6, anchor="C", max_w=180)
        # ── Hero block: big title + optional sub line(s) ──
        # Treat the title and ALL its sub line(s) as ONE group and
        # centre that group vertically on the hero-band centre. So:
        #   • title only            → title sits dead-centre
        #   • title + 1 sub         → the pair straddles the centre
        #   • title + 2 sub (ShA/ST)→ all three centred together
        # When the Erev-Pesach chametz box is shown the group is
        # centred in the reduced band above the box instead.
        has_ch = bool(w.chametz_lines_he)
        band_mid = _HERO_BAND_MID_CH if has_ch else _HERO_BAND_MID

        _hero_cx = (366.14 + 468.42) / 2.0     # ≈ 417.3
        _hero_mw = 92.0                        # < full gap (≈102) → pad

        _stack = getattr(w, "title_sub_stack_he", None) or []
        sub_lines: list[str] = []
        if _stack:
            sub_lines = list(_stack)
        elif w.title_sub_he:
            sub_lines = [w.title_sub_he]

        # Vertical metrics (card-space pt). The title cap-height is
        # large (size 23); each sub line is size 10.5. Use per-line
        # "slot" heights and a small gap so the group is a tight,
        # balanced block whose geometric centre lands on band_mid.
        _TITLE_H = 17.0          # visual height of the size-23 title
        _SUB_H = 9.0             # per sub line
        _GAP = 3.5               # title→first-sub gap
        n_sub = len(sub_lines)
        group_h = _TITLE_H + (
            (_GAP + n_sub * _SUB_H) if n_sub else 0.0
        )
        # Top of the group, then baselines. _t() centres text on the
        # y it's given, so place each element's centre.
        g_top = band_mid - group_h / 2.0
        # The big bold-serif title's ink sits ~1.9 pt BELOW its line
        # box centre (descender-heavy Hebrew sofit forms). Lift it so
        # its OPTICAL centre lands on the intended slot centre — this
        # makes a title-only card sit dead-centre and keeps multi-line
        # groups balanced.
        _TITLE_RISE = 1.9
        _main_stack = getattr(w, "title_main_stack_he", None) or []
        if _main_stack:
            # Two (or more) BIG title lines, all at the hero size,
            # vertically centred as one group on the band centre.
            # Adaptive condense: each line gets its own track so the
            # whole stack stays at size 23 (no font auto-shrink).
            _n = len(_main_stack)
            _LINE_H = 17.0
            _LGAP = 2.5
            _grp_h = _n * _LINE_H + (_n - 1) * _LGAP
            _g_top = band_mid - _grp_h / 2.0
            for _i, _ln in enumerate(_main_stack):
                _cy = (
                    _g_top + _i * (_LINE_H + _LGAP)
                    + _LINE_H / 2.0 - _TITLE_RISE
                )
                _tk = self._adaptive_hero_track(
                    _ln, size=23, max_w=_hero_mw)
                self._t(_hero_cx, _cy, _ln, size=23,
                        anchor="C", max_w=_hero_mw, track=_tk)
        else:
            title_cy = g_top + _TITLE_H / 2.0 - _TITLE_RISE
            # Adaptive condense: short parshas keep the familiar
            # track-82 look; long titles ('ערב ראש השנה' etc.) get a
            # lower track so they stay at FULL size 23 instead of
            # auto-shrinking to a smaller size — visually consistent
            # condensation across all titles.
            _tk = self._adaptive_hero_track(
                w.title_main_he, size=23, max_w=_hero_mw)
            self._t(_hero_cx, title_cy, w.title_main_he, size=23,
                    anchor="C", max_w=_hero_mw, track=_tk)
            if n_sub:
                sub_top = g_top + _TITLE_H + _GAP
                for _i, _ln in enumerate(sub_lines):
                    self._t(_hero_cx,
                             sub_top + _i * _SUB_H + _SUB_H / 2.0,
                             _ln, size=10.5,
                             anchor="C", max_w=_hero_mw)
        # chametz black box (dynamic)
        if has_ch:
            bx, by, bw, bh = _CHAMETZ_BOX
            self._rrect(bx, by, bw, bh, 3.0, (0, 0, 0))
            lines: list[str] = []
            for raw in w.chametz_lines_he:
                lines.extend(
                    p.strip() for p in raw.split(" - ") if p.strip())
            n = max(len(lines), 1)
            lh = 7.4                              # line height for size 7.6
            y0 = by + bh / 2 - lh * (n - 1) / 2.0
            for i, ln in enumerate(lines):
                self._t(bx + bw / 2, y0 + i * lh, ln,
                        size=7.6, color=(255, 255, 255),
                        max_w=bw - 4, track=92)

        big = [b for b in w.boxes if b.big]
        small = [b for b in w.boxes if not b.big]
        # Visual centre of the BIG black box on the template:
        # inner-black is y≈293.2..344.6 (raster-measured at the
        # box-centre column of weekly_template_regular.png), centre
        # ≈y=318.9. The big-time glyphs (numerals, no descenders)
        # visually sit a hair above true cell-centre, so add a small
        # optical drop (+0.4) so the digits look centred on the box.
        # The font size is bumped 42→50 (~19% taller per Yoel's
        # printed-luach comparison) and squeezed with track=92 so
        # values like '12:11' still fit comfortably; max_w guards
        # against any value that would otherwise overflow.
        _BIG_TIME_CY = 319.3
        if len(big) >= 1:                       # candle → right box
            x, y, bw, bh = _BIG_R
            self._t(x + bw / 2, _BIG_TIME_CY, big[0].time_str,
                    size=50, color=(255, 255, 255), rtl=False,
                    max_w=bw - 6, track=92)
            # Pill-tab label sits on the GREY tab above the black box
            # → black text (matches the printed luach and the
            # already-black small-box pill labels). White was washed
            # out on the grey pill.
            self._t(_CAP_R_CX, _CAP_CY, big[0].label_he, size=9.8,
                    color=(0, 0, 0), max_w=74)
        if len(big) >= 2:                       # motzei → left box
            x, y, bw, bh = _BIG_L
            self._t(x + bw / 2, _BIG_TIME_CY, big[1].time_str,
                    size=50, color=(255, 255, 255), rtl=False,
                    max_w=bw - 6, track=92)
            self._t(_CAP_L_CX, _CAP_CY, big[1].label_he, size=9.8,
                    color=(0, 0, 0), max_w=74)

        # small boxes: 2 → [right, left]; 1 → [left]. Time centred in
        # the black inner rect; label big & 2-line on the grey.
        if len(small) >= 2:
            slots = [(_SM_R, _SM_R_INNER), (_SM_L, _SM_L_INNER)]
        else:
            slots = [(_SM_L, _SM_L_INNER)]
        for i, (box, inner) in enumerate(slots):
            if i >= len(small):
                break
            b = small[i]
            ix, iy, iw, ih = inner
            self._t(ix + iw / 2, iy + ih / 2, b.time_str,
                    size=17, color=(255, 255, 255), rtl=False)
            # label fills the grey area to the right of the inner box.
            # The printed KY card LEFT-aligns the label in the grey
            # area (so it sits close to the black inner time-box on
            # the left; the bullet-dot template element occupies the
            # right edge). Anchor "L" places the cell's left edge at
            # gx0 + a small inset so the glyphs don't kiss the inner
            # rect.
            bx, by, bw, bh = box
            gx0 = ix + iw
            gx1 = bx + bw - 2
            lx = gx0 + 1.0
            lmw = gx1 - gx0 - 2
            toks = b.label_he.split()
            if toks and toks[0].startswith("הדלה"):
                head = toks[0]
                if len(toks) > 1 and toks[1].startswith("("):
                    head += " " + toks[1]
                    rest = " ".join(toks[2:])
                else:
                    rest = " ".join(toks[1:])
                l1, l2 = head, rest
            else:
                l1, l2 = b.label_he, ""
            if l2:
                self._t(lx, by + bh * 0.34, l1, size=8.4,
                        color=(0, 0, 0), max_w=lmw, anchor="L")
                self._t(lx, by + bh * 0.70, l2, size=8.4,
                        color=(0, 0, 0), max_w=lmw, anchor="L")
            else:
                self._t(lx, by + bh * 0.55, l1, size=9.0,
                        color=(0, 0, 0), max_w=lmw, anchor="L")

        # info band — molad / ז׳ שלמים / kiddush-levana. Split any
        # "A - B" line into two. Centred full-width when there are no
        # small boxes; otherwise centred within the free ribbon span
        # to the right of the small box(es) instead of hugging the
        # right edge (so a single Erev-YT box no longer pushes the
        # molad all the way right).
        if w.info_lines_he:
            disp: list[str] = []
            for raw in w.info_lines_he:
                disp.extend(
                    p.strip() for p in raw.split(" - ") if p.strip())
            # The תקופה line is lifted OUT of the info ribbon and
            # printed in the white strip BELOW the hero band — the
            # visual mirror of the place/year strip above it, centred
            # on the same hero-band centre.
            tek = [s for s in disp if s.lstrip().startswith("תקופת")]
            disp = [
                s for s in disp if not s.lstrip().startswith("תקופת")
            ]
            if tek:
                self._t(_HEADER_CX, _STRIP_BOT_Y,
                         f"  {INFO_SEP}  ".join(tek),
                         size=6.6, anchor="C", max_w=180, track=78)
            iy = _INFO_Y - _INFO_OPT_RISE
            if disp and small:
                rightmost = max(b[0] + b[2] for b, _inner in slots)
                left_bound = rightmost + 4
                cx = (left_bound + _INFO_RIGHT) / 2.0
                avail = _INFO_RIGHT - left_bound
                # When a small candle box is present the ribbon span
                # is narrow, but the printed luach still keeps the
                # whole info on ONE line (e.g. ערב-סוכות:
                # 'תקופת תשרי: …  •  ס״ז קידוש לבנה: …'). Join every
                # info segment with ' • ' and let _t() squeeze the
                # font down to fit the available width on a single
                # centred line, instead of wrapping to two lines.
                one_line = f"  {INFO_SEP}  ".join(disp)
                self._t(cx, _INFO_Y - _INFO_OPT_RISE, one_line,
                        size=7.2, anchor="C", max_w=avail)
            elif disp:
                # No small box → full ribbon width. The printed luach
                # still keeps the whole info on ONE line (e.g. the
                # השאלה week: 'השאלה: …  •  ס״ז קידוש לבנה: …'). Join
                # every segment with ' • ' on a single centred line at
                # the larger size; _t() auto-shrinks only if a very
                # long combined line would exceed the full width.
                one_line = f"  {INFO_SEP}  ".join(disp)
                self._t(306.0, _INFO_Y - _INFO_OPT_RISE, one_line,
                        size=8.4, anchor="C", max_w=300)

    # ── table ──
    def _table(self, w: WeeklyData) -> None:
        cols = WEEKLY_ZMAN_COLUMNS

        def hdr(idx, label):
            cx, cw = _col_center(idx)
            up, lo = _HDR_SPLIT.get(label, ("", label))
            col = (255, 255, 255)
            if up:
                self._t(cx, _HDR_Y1, up, size=6.5, color=col,
                        max_w=cw - 1)
                self._t(cx, _HDR_Y2, lo, size=6.5, color=col,
                        max_w=cw - 1)
            else:
                self._t(cx, (_HDR_Y1 + _HDR_Y2) / 2, lo, size=6.5,
                        color=col, max_w=cw - 1)

        hdr(0, "יום החודש")
        hdr(1, "למספ׳")
        for i, (disp, _c) in enumerate(cols):
            hdr(2 + i, disp)

        for ri, day in enumerate(w.days):
            ry = _ROW_CY[ri]
            cx, cw = _col_center(0)
            if day.dom_sublabel_he:
                # Match the hero's condensed look — same base track
                # used by the hero (82). The SUB-label (the small
                # event line under the Hebrew date, e.g.
                # 'תענית שני בתרא', 'חמשה עשר בשבט') uses an even
                # tighter track=78 + a wider horizontal inset
                # (cw - 2.5) so long labels no longer kiss the
                # English-date column line — uniform breathing room
                # across all sub-labels, matching the tekufah strip
                # and footer condense.
                self._t(cx, ry - 3.0, day.hebrew_dom_he, size=8.6,
                        max_w=cw - 0.5, track=82)
                self._t(cx, ry + 3.6, day.dom_sublabel_he, size=5.2,
                        max_w=cw - 2.5, track=78)
            else:
                self._t(cx, ry, day.hebrew_dom_he, size=9.4,
                        max_w=cw - 0.5, track=82)
            cx, cw = _col_center(1)
            self._t(cx, ry, day.civil_date.strftime("%b ")
                    + str(day.civil_date.day), size=6.8,
                    rtl=False, max_w=cw - 1, track=78)
            for i, (disp, canon) in enumerate(cols):
                cx, cw = _col_center(2 + i)
                _secs = getattr(w, "add_seconds", False)
                if _secs:
                    # Unrounded astronomical value, H:MM:SS. Only the
                    # GRID columns — candle/havdalah/motzei boxes keep
                    # their halachic rounding (handled elsewhere).
                    dt = day.zmanim_raw.get(canon) or day.zmanim.get(
                        canon)
                    txt = "" if dt is None else (
                        f"{dt.hour % 12 or 12}:"
                        f"{dt.minute:02d}:{dt.second:02d}")
                else:
                    dt = day.zmanim.get(canon)
                    txt = "" if dt is None else (
                        f"{dt.hour % 12 or 12}:{dt.minute:02d}")
                boxed = disp == WEEKLY_BOXED_COLUMN
                if boxed:
                    # Black tag drawn live, centred exactly on the
                    # cell centre (same anchor the text uses), so box
                    # and time are always concentric for every row /
                    # year. Time clamped to the tag width so it can
                    # never spill past the box on a wide value.
                    if txt:
                        self._fill_rect(
                            cx - _BOXED_W / 2.0, ry - _BOXED_H / 2.0,
                            _BOXED_W, _BOXED_H, (0, 0, 0))
                    self._t(cx, ry, txt, size=7.2,
                            color=(255, 255, 255), rtl=False,
                            max_w=_BOXED_W - 1.6)
                else:
                    self._t(cx, ry, txt, size=7.2, color=(0, 0, 0),
                            rtl=False, max_w=cw - 0.5)

    # ── right strip: pennants, sefirah badges, month tab ──
    def _right_strip(self, w: WeeklyData) -> None:
        for ri, day in enumerate(w.days):
            ry = _ROW_CY[ri]
            # weekday pennant — one path, black-filled with a thin
            # grey (#827C77) stroke. The path is OPEN on the left
            # (grid) edge: the fill still closes implicitly, but an
            # open stroke draws only top → rounded-right → bottom, so
            # no grey appears on the grid side and there is no
            # offset/shadow doubling.
            y0 = ry - _PEN_H / 2.0
            x0, x1 = self._X(_PEN_X0), self._X(_PEN_X1)
            yt, yb = self._Y(y0), self._Y(y0 + _PEN_H)
            r = self._S(_PEN_R)
            k = r * 0.5523                      # circle bezier const
            with self.new_path(x0, yt) as p:
                p.auto_close = False
                p.style.fill_color = "#000000"
                p.style.stroke_color = "#827C77"
                p.style.stroke_width = self._S(_PEN_SW)
                p.style.stroke_join_style = "round"
                p.style.stroke_cap_style = "butt"
                p.line_to(x1 - r, yt)                       # top edge
                p.curve_to(x1 - r + k, yt, x1, yt + r - k,
                           x1, yt + r)                      # TR round
                p.line_to(x1, yb - r)                       # right
                p.curve_to(x1, yb - r + k, x1 - r + k, yb,
                           x1 - r, yb)                      # BR round
                p.line_to(x0, yb)                           # bottom
            # day-of-week label — big, tight tracking to fill the bar.
            self._t((_PEN_X0 + _PEN_X1) / 2.0, ry, day.weekday_he,
                    size=7.6, color=(255, 255, 255),
                    max_w=_PEN_X1 - _PEN_X0 - 3.0, track=90)
            # sefirah badge — ONLY during the omer
            if day.omer_letters_he:
                # Fill with the same grey the template's zebra rows use
                # (RGB 171,173,176 — measured from weekly_template_regular.png)
                # so the badge matches the printed KY card.
                self.set_fill_color(171, 173, 176)
                self.set_draw_color(0, 0, 0)
                self.set_line_width(self._S(0.5))
                r = self._S(_BADGE_R)
                bx = self._X(_BADGE_CX) - r
                by = self._Y(ry) - r
                self.ellipse(bx, by, r * 2, r * 2, style="DF")
                # centred via an aligned cell spanning the diameter.
                # Strip geresh ׳ / gershayim ״ (incl. ASCII ' ")
                # so just the numeral letters show in the circle.
                _omer = day.omer_letters_he
                for _g in ("\u05f3", "\u05f4", "'", '"', "\u2032",
                           "\u2033"):
                    _omer = _omer.replace(_g, "")
                size = self._S(5.4)
                disp = bidi(_omer)
                self.set_font(self.FONT, "B", size)
                while size > 3 and self.get_string_width(disp) > r * 1.7:
                    size -= 0.3
                    self.set_font(self.FONT, "B", size)
                self.set_text_color(0, 0, 0)
                self.set_xy(bx, self._Y(ry) - size * 0.55)
                self.cell(r * 2, size, disp, align="C")

        # ספירה vertical tab — drawn ONLY when the week has any omer
        # day (dynamic, like the badges). Shape: a rounded rect body
        # with a small centred point at the bottom, reproduced from
        # the original SVG path.
        if any(d.omer_letters_he for d in w.days):
            cx = (_TAB_X0 + _TAB_X1) / 2.0
            self.set_fill_color(0, 0, 0)
            self.set_draw_color(0, 0, 0)
            self.set_line_width(self._S(0.2))
            pts = [
                (self._X(_TAB_X0), self._Y(_TAB_Y0)),
                (self._X(_TAB_X1), self._Y(_TAB_Y0)),
                (self._X(_TAB_X1), self._Y(_TAB_Y1)),
                (self._X(cx + 1.0), self._Y(_TAB_Y1)),
                (self._X(cx), self._Y(_TAB_PT_Y)),
                (self._X(cx - 1.0), self._Y(_TAB_Y1)),
                (self._X(_TAB_X0), self._Y(_TAB_Y1)),
            ]
            self.polygon(pts, style="DF")
            tcx, tcy = cx, (_TAB_Y0 + _TAB_Y1) / 2.0
            ccx, ccy = self._X(tcx), self._Y(tcy)
            with self.rotation(90, ccx, ccy):
                self._t(tcx, tcy, "ספירה", size=6.0,
                        color=(255, 255, 255),
                        max_w=_TAB_Y1 - _TAB_Y0 - 3)