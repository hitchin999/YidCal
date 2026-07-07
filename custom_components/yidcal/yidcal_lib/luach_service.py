"""
custom_components/yidcal/yidcal_lib/luach_service.py

Service handler for ``yidcal.generate_luach``.

Pipeline:
  1. Validate input (style, date range, optional column list, optional
     location override).
  2. Resolve location: either the HA-configured one or a free-form
     ``location`` string (geocoded via zman_geocoder).
  3. Pull diaspora/havdalah/candle offsets from the active config entry
     (per-call overrides take precedence).
  4. Build LuachItems via luach_data.build_luach (with a molad provider
     backed by YidCalHelper.get_actual_molad).
  5. Render the PDF to
     /config/www/yidcal-data/luach_<style>_<years>_<ts>.pdf
     (the style — weekly / yearly_multi_page / yearly_sheet — is in
     the filename so each luach is identifiable at a glance).
  6. Fire a persistent notification with the download link.

Two renderers are supported:
  • ``style: yearly_multi_page`` — multi-page Monroe-style table.
  • ``style: yearly_sheet`` — single-page South-Fallsburg-style two-
                              column layout. ``hebrew_year`` is
                              optional (defaults to the current
                              Hebrew year); rejects
                              ``start_date``/``end_date``.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv

from zmanim.util.geo_location import GeoLocation

from ..const import DOMAIN, WEEKLY_LUACH_ENABLED
from .luach_data import (
    build_luach, build_weekly_data, build_weekly_cards, LuachConfig,
    LuachRow, AnnotationRow,
)
from .luach_pdf_common import fonts_available, serif_fonts_available, INFO_SEP
from .luach_yearly_multi_page_pdf import (
    render_yearly_multi_page_pdf, DEFAULT_EXTRA_ZMANIM,
)
from .luach_yearly_sheet_pdf import render_yearly_sheet_pdf
from .luach_weekly_pdf import render_weekly_pdf, render_weekly_pdf_multi


_LOGGER = logging.getLogger(__name__)


SERVICE_GENERATE_LUACH = "generate_luach"

# Output directory under /config/www so HA serves /local/yidcal-data/<file>
_OUTPUT_SUBDIR = "www/yidcal-data"

# Auto-retention: each run keeps only the most recent N luach files
# in the output directory IN TOTAL (a "global" cap), with PDFs and
# JSONs counted SEPARATELY — so the folder holds at most N most-recent
# PDFs plus at most N most-recent JSONs (when the JSON sidecar is
# enabled). Older files this integration generated are deleted; a
# user's explicit ``output_path`` override or any unrelated file is
# never touched (only files carrying our ``_YYYYMMDD-HHMMSS`` suffix
# are considered). Set to 0 to disable pruning and keep every file.
_KEEP_RECENT = 4

# Cap on date-range length to prevent runaway generation.
_MAX_RANGE_DAYS = 800  # ~2.2 years


_VALID_STYLES = ("yearly_multi_page", "yearly_sheet", "weekly")

# Legacy aliases from before the pre-beta rename (Run 7). These let
# callers with stale automations or cached service-call form state
# transparently migrate to the new style identifiers instead of
# seeing a hard error during the upgrade. Safe to remove after a
# release cycle once people have re-saved their automations.
# NOTE: the pre-rename (Run 7) era also aliased "weekly" ->
# "yearly_multi_page" (back when "weekly" was the OLD name for the
# multi-page Monroe style). That alias has been REMOVED: "weekly" is
# now a real, first-class style (the Kiryas-Yoel single card), so
# keeping the alias would silently shadow it and emit the yearly
# multi-page instead. Only the non-conflicting "yearly" alias
# remains. Safe to remove entirely after a release cycle.
_LEGACY_STYLE_ALIASES = {
    "yearly": "yearly_sheet",  # was the single-page SF style
}


def _style_validator(v):
    """Voluptuous validator for the ``style`` field.

    Tolerates the case where Home Assistant's UI passes the field
    through as ``None`` (which happens when the user opens the
    service-call form and submits without manually touching the
    style dropdown — HA serialises an unmodified optional select as
    ``null`` rather than omitting the key, which means the
    ``vol.Optional`` ``default=`` never fires). Coerces ``None`` to
    the default style. Also accepts the legacy pre-rename names as
    aliases. Otherwise validates against the allowed set.
    """
    if v is None:
        return "yearly_multi_page"
    # Current valid styles ALWAYS win over legacy aliases, so a real
    # style name can never be hijacked by a stale alias mapping.
    if v in _VALID_STYLES:
        return v
    if v in _LEGACY_STYLE_ALIASES:
        return _LEGACY_STYLE_ALIASES[v]
    raise vol.Invalid(
        f"style must be one of {list(_VALID_STYLES)}, got {v!r}"
    )


_SCHEMA = vol.Schema({
    vol.Optional("style", default="yearly_multi_page"): _style_validator,
    vol.Optional("hebrew_year"): vol.All(vol.Coerce(int), vol.Range(min=5780, max=5900)),
    vol.Optional("start_date"): cv.date,
    vol.Optional("end_date"): cv.date,
    vol.Optional("location"): cv.string,
    vol.Optional("columns"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("output_path"): cv.string,
    # Per-call diaspora override. When omitted, the integration's
    # configured ``is_in_israel`` value (Settings → Devices → YidCal →
    # Configure) is used. This lets a user in Brooklyn generate a
    # luach for Yerushalayim (and vice versa) without flipping
    # integration settings.
    vol.Optional("is_in_israel"): cv.boolean,
    # Per-call offset overrides. When omitted, the integration's
    # configured values (Settings → Devices → YidCal → Configure) are
    # used. Ranges below cover virtually every minhag in use —
    # candle 0–40 min before sunset, havdalah 30–120 min after.
    vol.Optional("candle_offset"): vol.All(vol.Coerce(int), vol.Range(min=0, max=40)),
    vol.Optional("havdalah_offset"): vol.All(vol.Coerce(int), vol.Range(min=30, max=120)),
    # Weekly-only display option. When true, the GRID zman columns
    # print with seconds (H:MM:SS) using the exact unrounded
    # astronomical value, instead of the rounded H:MM. Candle
    # lighting / havdalah / motzei boxes are NOT affected (they keep
    # their halachic rounding). Ignored by the yearly styles.
    vol.Optional("add_seconds"): cv.boolean,
    # When True, also write a sidecar JSON next to the PDF (same path,
    # ``.json`` extension) carrying the structured luach data, so a
    # dashboard/card can be built off the data instead of the PDF.
    vol.Optional("emit_json"): cv.boolean,
})


# ── Weekly-style full UI (published only when the flag is ON) ──────────
# services.yaml ships as the DISABLED variant: no "weekly" entry in the
# style dropdown and no weekly wording in the field texts. When
# const.WEEKLY_LUACH_ENABLED is True, _async_publish_weekly_ui() loads
# services.yaml at startup, restores the weekly texts below, and
# republishes the service description via async_set_service_schema —
# so flipping the ONE flag in const.py brings back the complete UI
# (dropdown option + descriptions) after a restart.
_WEEKLY_STYLE_OPTION = {"value": "weekly", "label": "Weekly (Single Card)"}
_WEEKLY_STYLE_DESC_SUFFIX = (
    ' "Weekly (Single Card)" produces one card per Sun→Shabbos week '
    "(one row per day with that day's daily zmanim): give "
    '"hebrew_year" for a full-year booklet, or "start_date" + '
    '"end_date" for a range of weeks, or leave all of those blank for '
    'just the current week. A bare "start_date" with no end_date / '
    "Hebrew year is also a single week (anchored on that date)."
)
_WEEKLY_HY_MARKER = "whole Hebrew year. Enter"
_WEEKLY_HY_RESTORED = (
    'whole Hebrew year. For the "Weekly (Single Card)" style it '
    "generates a full-year booklet — one card per week. Enter"
)
_WEEKLY_AS_NAME = "Show seconds in the grid (Weekly only)"
_WEEKLY_AS_MARKER = (
    "Advanced. When enabled, daily-zmanim GRID columns (card layouts)"
)
_WEEKLY_AS_RESTORED = (
    "Weekly style only. When enabled, the daily-zmanim GRID columns"
)


async def _async_publish_weekly_ui(hass: HomeAssistant) -> None:
    """Republish the generate_luach service description with the
    weekly style restored (dropdown option + field texts).

    Best-effort: any failure only affects the service's DESCRIPTION in
    the UI — the weekly style itself works regardless (the gate in
    the handler is already open when this runs)."""
    try:
        from homeassistant.helpers.service import async_set_service_schema

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "services.yaml",
        )

        def _load():
            import yaml as _yaml
            with open(path, encoding="utf-8") as fh:
                return _yaml.safe_load(fh)

        all_svcs = await hass.async_add_executor_job(_load)
        desc = dict((all_svcs or {}).get(SERVICE_GENERATE_LUACH) or {})
        fields = desc.get("fields") or {}

        style = fields.get("style") or {}
        sel = ((style.get("selector") or {}).get("select") or {})
        opts = sel.setdefault("options", [])
        if not any(
            isinstance(o, dict) and o.get("value") == "weekly"
            for o in opts
        ):
            opts.append(dict(_WEEKLY_STYLE_OPTION))
        style["description"] = (
            (style.get("description") or "").rstrip()
            + _WEEKLY_STYLE_DESC_SUFFIX
        )

        hy = fields.get("hebrew_year") or {}
        hy_desc = hy.get("description") or ""
        if _WEEKLY_HY_MARKER in hy_desc:
            hy["description"] = hy_desc.replace(
                _WEEKLY_HY_MARKER, _WEEKLY_HY_RESTORED, 1)

        add_s = fields.get("add_seconds") or {}
        add_s["name"] = _WEEKLY_AS_NAME
        as_desc = add_s.get("description") or ""
        if _WEEKLY_AS_MARKER in as_desc:
            add_s["description"] = as_desc.replace(
                _WEEKLY_AS_MARKER, _WEEKLY_AS_RESTORED, 1)

        async_set_service_schema(
            hass, DOMAIN, SERVICE_GENERATE_LUACH, desc)
        _LOGGER.info(
            "YidCal: weekly luach style is ENABLED — full service UI "
            "(dropdown + descriptions) published")
    except Exception:
        _LOGGER.exception(
            "YidCal: could not publish the weekly-enabled service UI "
            "(the weekly style itself still works)")


def async_register_service(hass: HomeAssistant) -> None:
    """Register (or re-register) the ``yidcal.generate_luach`` service.

    Calling ``hass.services.async_register`` with an existing service
    name is an upsert — it replaces the handler and schema. We
    intentionally don't guard against re-registration here so that
    integration reload (without a full HA restart) picks up the
    latest schema/handler from this module, rather than keeping the
    version registered on the previous load.
    """
    async def _handle(call: ServiceCall) -> None:
        await _async_generate_luach(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_LUACH, _handle, schema=_SCHEMA,
    )
    _LOGGER.debug("YidCal: registered service %s.%s", DOMAIN, SERVICE_GENERATE_LUACH)
    if WEEKLY_LUACH_ENABLED:
        # Restore the full weekly UI (dropdown + texts) — see
        # _async_publish_weekly_ui above.
        hass.async_create_task(_async_publish_weekly_ui(hass))


def async_remove_service(hass: HomeAssistant) -> None:
    """Remove the service if present."""
    if hass.services.has_service(DOMAIN, SERVICE_GENERATE_LUACH):
        hass.services.async_remove(DOMAIN, SERVICE_GENERATE_LUACH)


async def _async_generate_luach(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service handler body. Runs on the HA event loop; offloads the
    blocking PDF generation to the executor.
    """
    style = call.data.get("style") or "yearly_multi_page"

    # ── Weekly-style feature gate ──
    # The weekly card style ships disabled (const.WEEKLY_LUACH_ENABLED).
    # "weekly" stays in _VALID_STYLES so the schema accepts it and the
    # caller gets THIS clear message instead of a validator stack trace.
    if style == "weekly" and not WEEKLY_LUACH_ENABLED:
        raise ServiceValidationError(
            "The 'weekly' luach style is not available in this release."
        )

    # ── Yearly-sheet-specific input validation ──
    # The yearly-sheet style is a one-Hebrew-year single-page layout.
    # ``hebrew_year`` is optional: when omitted it defaults to the
    # CURRENT Hebrew year (the year today falls in) so the user can
    # just pick the style and generate. Custom start/end dates are
    # still rejected (an arbitrary range would either overflow one
    # page or leave large gaps on a fixed two-column layout).
    #
    # ``eff_hebrew_year`` is the effective year used downstream: the
    # caller-supplied value when given, the current Hebrew year when
    # not (yearly_sheet only). For every other style it is simply the
    # caller value (possibly None) — behaviour unchanged.
    eff_hebrew_year = call.data.get("hebrew_year")
    if style == "yearly_sheet":
        if (
            call.data.get("start_date") is not None
            or call.data.get("end_date") is not None
        ):
            raise ServiceValidationError(
                "yearly_sheet luach style does not accept 'start_date' or "
                "'end_date' — pass only 'hebrew_year' (or leave it blank "
                "to use the current Hebrew year)."
            )
        if eff_hebrew_year is None:
            from pyluach.hebrewcal import HebrewDate as _PHDyear
            eff_hebrew_year = _PHDyear.today().year
            _LOGGER.info(
                "yearly_sheet: no hebrew_year given — defaulting to the "
                "current Hebrew year %s", eff_hebrew_year,
            )

    # ── Date range ──
    # The weekly style is anchored on a single date (start_date, or
    # today) and covers exactly one Sun→Shabbos week. It does NOT use
    # the hebrew_year full-year shortcut or end_date — honoring those
    # here would anchor the card on Erev Rosh Hashana of that year
    # (the full-year range's start) instead of the intended week.
    # The Style field's help text documents this; enforce it so a
    # leftover hebrew_year/end_date from a previous yearly run (a
    # very easy state to leave the service-call form in) can't
    # silently retarget the weekly card to the wrong week.
    # Weekly modes (per the agreed trigger design):
    #   • hebrew_year given          → full-year booklet (multi-page)
    #   • end_date given (a range)   → that range of weeks (multi-page)
    #   • bare start_date / nothing  → the single current week
    weekly_multi = False
    if style == "weekly":
        weekly_multi = (
            call.data.get("hebrew_year") is not None
            or call.data.get("end_date") is not None
        )
        if weekly_multi:
            _date_input = call.data            # let hy/end_date through
        else:
            _date_input = {
                k: v for k, v in call.data.items()
                if k not in ("hebrew_year", "end_date")
            }
    else:
        if (
            style == "yearly_sheet"
            and call.data.get("hebrew_year") is None
        ):
            # No year supplied → use the current-year default so the
            # full-year range is resolved correctly.
            _date_input = {**call.data, "hebrew_year": eff_hebrew_year}
        else:
            _date_input = call.data
    start_d, end_d = _resolve_date_range(_date_input)

    # ── Location: free-form override or HA-configured ──
    # ``loc_name`` is the human-readable location string used in the
    # PDF subtitle (geocoded display name for explicit overrides, or
    # the integration's cached "City, State" for the default).
    lat, lon, tzname, loc_name = await _resolve_location(hass, call.data.get("location"))

    # ── Pull config-entry options for diaspora + offsets + display knobs ──
    diaspora, candle_off, havdalah_off, metzora_disp = _read_config_options(hass)
    # Per-call overrides take precedence over the config-entry
    # defaults. Useful when generating luachs for a different minhag
    # or community without flipping integration settings.
    if call.data.get("is_in_israel") is not None:
        diaspora = not bool(call.data["is_in_israel"])
    if call.data.get("candle_offset") is not None:
        candle_off = int(call.data["candle_offset"])
    if call.data.get("havdalah_offset") is not None:
        havdalah_off = int(call.data["havdalah_offset"])

    # ── Verify bundled fonts are present (small, but fail loudly if not) ──
    if not fonts_available():
        raise ServiceValidationError(
            "Required font files are missing from custom_components/yidcal/"
            "yidcal_lib/fonts/. Reinstall the integration to restore them."
        )
    if style == "yearly_sheet" and not serif_fonts_available():
        raise ServiceValidationError(
            "yearly_sheet luach style requires the Frank Ruehl CLM serif "
            "fonts (FrankRuehlCLM-Medium.ttf, FrankRuehlCLM-Bold.ttf) "
            "under custom_components/yidcal/yidcal_lib/fonts/. "
            "Reinstall the integration to restore them."
        )

    # ── Build a molad provider closure (uses the existing YidCalHelper) ──
    molad_provider = _make_molad_provider(tzname)

    # ── Optional extra-zmanim column override ──
    extra_labels = tuple(call.data.get("columns") or DEFAULT_EXTRA_ZMANIM)

    # Capture the path override before entering the executor; resolution
    # (which does mkdir) happens inside the executor since it touches
    # the filesystem.
    output_override = call.data.get("output_path")

    # Opt-in sidecar JSON (off by default so the www folder isn't
    # doubled for users who only want the PDF). When on, a ``.json``
    # twin of the PDF is written with the structured luach data so a
    # dashboard card can be built off it.
    emit_json = bool(call.data.get("emit_json", False))
    _json_location = {
        "lat": lat, "lon": lon, "tzname": tzname,
        "name": loc_name or "",
    }

    # Offload to the executor: build_luach, mkdir, and PDF rendering
    # are blocking ops that would stall the event loop on a Pi.
    def _blocking_generate() -> Path:
        # Filename slug: prefer the raw user input (so "10952" stays
        # "10952" verbatim), falling back to the resolved location
        # name when the user didn't pass an override.
        raw_loc = call.data.get("location") or ""
        loc_slug = _location_slug(raw_loc or loc_name)
        out_path = _resolve_output_path(
            hass, output_override, start_d, end_d,
            location_slug=loc_slug, style=style,
        )
        geo = GeoLocation(
            name="YidCal", latitude=lat, longitude=lon,
            time_zone=tzname, elevation=0,
        )

        # ── Weekly (KY-style single card) ──
        # Anchored on a single date (start_d, defaults to today); covers
        # the Sun→Shabbos week containing the next Shabbos. Does not use
        # hebrew_year / end_date. Data comes from build_weekly_data() —
        # the same build_luach + compute_zmanim_for_date pipeline the
        # yearly luachs use.
        if style == "weekly":
            from datetime import timedelta as _td
            from . import halacha_events as _he
            from pyluach.hebrewcal import HebrewDate as _PHD

            wconfig = LuachConfig(
                geo=geo, tz=ZoneInfo(tzname), diaspora=diaspora,
                candle_offset=candle_off, havdalah_offset=havdalah_off,
                metzora_display=metzora_disp,
                extra_zmanim_labels=extra_labels,
                molad_style="monroe",
                hebrew_date_rc_emphasis=True,
            )
            # Community name: Hebrew form from the places DB when the
            # coordinates snap to a known community, else the English
            # place name (same precedence the yearly headers use).
            _community = loc_name or "YidCal"
            try:
                from .places import find_place, get_hebrew_name
                _fp = find_place(lat, lon)
                if _fp is not None:
                    _canon = _fp[0]
                    _community = get_hebrew_name(_canon) or _canon
            except Exception:
                pass
            notes_he = (
                "זמן עלות: 72 מינוט לפני הנ״ץ | "
                "מנחה גדולה גר״א: מוקדם 6 מינוט | "
                "מנחה קטנה גר״א: מוקדם 42 מינוט | "
                "פלג המנחה גר״א: מוקדם 57 מינוט | "
                f"הדלה״נ: {candle_off} מינוט לפני השקיעה | "
                "לכל זמן \u00a9"
            )

            def _strip_for(week_end_date):
                _hy = _PHD.from_pydate(week_end_date).year
                try:
                    _yl = _he.hebrew_year_letters(_hy)
                except Exception:
                    _yl = str(_hy)
                return f'{_community} {INFO_SEP} שנת {_yl} לפ"ק'

            if weekly_multi:
                # One KY card per Sun→Shabbos week across the span.
                # Anchor each week by its Shabbos (the first Saturday
                # on/after start_d, then every +7 days through end_d).
                _sat = start_d + _td(
                    days=(5 - start_d.weekday()) % 7
                )
                # When invoked as a `hebrew_year` booklet, the range
                # now runs past the last parsha into the first weeks
                # of (hy+1) — the ערב-ראש-השנה + האזינו preview pages.
                # Those trailing pages carry the incoming year (שנת …)
                # as their hero sub. Detect them by RH of (hy+1): any
                # week whose Shabbos is on/after that date is a
                # next-year page. Only set for the hebrew_year path so
                # the main body / sensors are untouched.
                _booklet_hy = call.data.get("hebrew_year")
                _next_year_rh = None
                _next_year_n = None
                if _booklet_hy is not None:
                    try:
                        _bh = int(_booklet_hy)
                        _next_year_n = _bh + 1
                        _next_year_rh = _PHD(
                            _bh + 1, 7, 1
                        ).to_pydate()
                    except Exception:
                        _next_year_rh = None
                        _next_year_n = None
                weeks_out = []
                while _sat <= end_d:
                    # A single Sun→Shabbos week can yield more than
                    # one card (Tishrei: Erev-YT card + Shabbos card).
                    _tys = None
                    if (
                        _next_year_rh is not None
                        and _sat >= _next_year_rh
                    ):
                        _tys = _next_year_n
                    _cards = build_weekly_cards(
                        anchor_date=_sat, config=wconfig,
                        molad_provider=molad_provider,
                        trailing_year_sub=_tys,
                        add_seconds=bool(
                            call.data.get("add_seconds", False)),
                    )
                    for _wd in _cards:
                        weeks_out.append(
                            (_wd, _strip_for(_wd.week_end), notes_he)
                        )
                    _sat = _sat + _td(days=7)
                if not weeks_out:
                    raise ServiceValidationError(
                        "No Sun→Shabbos weeks fall in the requested "
                        "range."
                    )
                render_weekly_pdf_multi(
                    weeks=weeks_out, output_path=out_path,
                )
                _all_notes = sorted({
                    n for _wd, _, _ in weeks_out for n in _wd.open_notes
                })
                _LOGGER.info(
                    "YidCal weekly booklet — %d pages (%s → %s)",
                    len(weeks_out),
                    weeks_out[0][0].week_start.isoformat(),
                    weeks_out[-1][0].week_end.isoformat(),
                )
                if _all_notes:
                    _LOGGER.info(
                        "YidCal weekly booklet — open items: %s",
                        " | ".join(_all_notes),
                    )
                if emit_json:
                    _write_sidecar_json(
                        out_path,
                        _json_weekly_payload(
                            [_wd for _wd, _, _ in weeks_out],
                            location=_json_location,
                            diaspora=diaspora,
                        ),
                    )
                return out_path

            # Single current week. Still may yield >1 card (a Tishrei
            # week with Erev-YT + Shabbos) — emit every card for that
            # week, in printed order, via the multi-card renderer.
            _cards = build_weekly_cards(
                anchor_date=start_d, config=wconfig,
                molad_provider=molad_provider,
                add_seconds=bool(
                    call.data.get("add_seconds", False)),
            )
            render_weekly_pdf_multi(
                weeks=[
                    (_wd, _strip_for(_wd.week_end), notes_he)
                    for _wd in _cards
                ],
                output_path=out_path,
            )
            _sw_notes = sorted({
                n for _wd in _cards for n in _wd.open_notes
            })
            if _sw_notes:
                _LOGGER.info(
                    "YidCal weekly luach — open items: %s",
                    " | ".join(_sw_notes),
                )
            if emit_json:
                _write_sidecar_json(
                    out_path,
                    _json_weekly_payload(
                        list(_cards),
                        location=_json_location,
                        diaspora=diaspora,
                    ),
                )
            return out_path

        # The yearly-sheet layout uses the South-Fallsburg printed
        # conventions:
        #   • Molad phrasing: ``בשעה H:MM ו<P> חלקים <TOD>``
        #   • Hebrew dates: plain (no RC emphasis), since SF conveys
        #     RC info via the row's ``שבת ר״ח`` special-Shabbos tag
        # The yearly-multi-page layout keeps the Monroe / KJ
        # conventions (molad with time-of-day word first, dates with
        # RC emphasis).
        is_sheet = (style == "yearly_sheet")
        molad_style = "sf" if is_sheet else "monroe"
        hebrew_date_rc_emphasis = not is_sheet
        config = LuachConfig(
            geo=geo, tz=ZoneInfo(tzname), diaspora=diaspora,
            candle_offset=candle_off, havdalah_offset=havdalah_off,
            metzora_display=metzora_disp, extra_zmanim_labels=extra_labels,
            molad_style=molad_style,
            hebrew_date_rc_emphasis=hebrew_date_rc_emphasis,
            compact_erev_yt_labels=is_sheet,
            compact_mevorchim_parsha=is_sheet,
            omit_chazak=is_sheet,
        )
        items = build_luach(
            start_date=start_d, end_date=end_d,
            config=config, molad_provider=molad_provider,
        )
        title_he, subtitle_he, notes_he = _build_titles(
            start_d, end_d, lat, lon, tzname, city=loc_name,
            hebrew_year_override=eff_hebrew_year,
            candle_offset=candle_off, havdalah_offset=havdalah_off,
            style=style,
        )
        if style == "yearly_sheet":
            render_yearly_sheet_pdf(
                items=items, output_path=out_path,
                title_he=title_he, subtitle_he=subtitle_he,
                notes_he=notes_he,
                hebrew_year=int(eff_hebrew_year),
                extra_zmanim_labels=extra_labels, diaspora=diaspora,
            )
        else:
            render_yearly_multi_page_pdf(
                items=items, output_path=out_path,
                title_he=title_he, subtitle_he=subtitle_he,
                notes_he=notes_he,
                extra_zmanim_labels=extra_labels, diaspora=diaspora,
            )
        if emit_json:
            _write_sidecar_json(
                out_path,
                _json_yearly_payload(
                    items, kind=style,
                    title_he=title_he, subtitle_he=subtitle_he,
                    notes_he=notes_he, location=_json_location,
                    diaspora=diaspora, hebrew_year=eff_hebrew_year,
                    start=start_d, end=end_d,
                ),
            )
        return out_path

    try:
        out_path = await hass.async_add_executor_job(_blocking_generate)
    except Exception as exc:
        _LOGGER.exception("Luach generation failed")
        raise ServiceValidationError(f"Luach generation failed: {exc}") from exc

    # Auto-retention: trim old generated PDFs so the www folder doesn't
    # grow unbounded across repeated runs. Runs after the new file is
    # written (so it's the newest and is always kept) and in the
    # executor since it touches the filesystem. Never fatal.
    _pruned_names: list[str] = []
    try:
        _pruned_names = await hass.async_add_executor_job(
            _prune_old_outputs, hass)
    except Exception:  # noqa: BLE001 — pruning must never fail the call
        _LOGGER.exception("Luach output prune failed (ignored)")

    # When the prune actually removed file(s), tell the user — same
    # persistent-notification mechanism as the "luach generated"
    # notification below, but a SEPARATE notification (its own
    # notification_id) so it sits alongside the "saved" one.
    # Skipped silently when nothing was deleted (no spammy
    # zero-count noises).
    if _pruned_names:
        from homeassistant.components import (
            persistent_notification as _pn_del,
        )
        _names_md = "\n".join(f"• `{n}`" for n in _pruned_names)
        _kind = (
            "files" if len(_pruned_names) != 1
            else ("PDF" if _pruned_names[0].endswith(".pdf") else "JSON")
        )
        _pn_del.async_create(
            hass,
            message=(
                f"Auto-cleanup removed {len(_pruned_names)} old "
                f"luach {_kind} from `/config/{_OUTPUT_SUBDIR}/`:\n\n"
                f"{_names_md}\n\n"
                f"YidCal keeps the {_KEEP_RECENT} most-recent PDFs "
                f"and {_KEEP_RECENT} most-recent JSONs in this "
                f"folder; older ones are deleted when a new luach "
                f"is generated."
            ),
            title="YidCal luach auto-cleanup",
            notification_id="yidcal_luach_pruned",
        )

    # ── Notify the user with a download link ──
    # The clickable anchor uses the RELATIVE ``/local/...`` URL on
    # purpose: the browser resolves it against whatever origin the
    # user is on when they CLICK (Cloudflare tunnel, DDNS, LAN IP),
    # so the link works from every access path. An absolute URL
    # frozen at generation time breaks whenever that one path is
    # down — and ``get_url(require_current_request=True)`` can only
    # ever return URLs HA knows about (Internal/External/Cloud), so
    # unconfigured origins like a tunnel hostname never matched
    # anyway. An absolute URL is still computed below, but ONLY for
    # the copy-paste line (useful for sharing to another device):
    #   1. ``require_current_request=True`` — the origin the user
    #      generated from, when a request context exists.
    #   2. ``prefer_external=True`` — the configured External URL.
    #   3. raw config attrs as a last resort on very old HA.
    #   4. falls back to the relative URL if nothing is configured.
    from homeassistant.components import persistent_notification

    rel = out_path.name
    rel_url = f"/local/yidcal-data/{rel}"
    base_url = ""
    try:
        from homeassistant.helpers.network import (
            get_url,
            NoURLAvailableError,
        )
        try:
            base_url = get_url(hass, require_current_request=True)
        except NoURLAvailableError:
            try:
                base_url = get_url(hass, prefer_external=True)
            except NoURLAvailableError:
                base_url = ""
    except ImportError:  # very old HA, get_url helper not available
        base_url = (
            getattr(hass.config, "external_url", None)
            or getattr(hass.config, "internal_url", None)
            or ""
        )
    full_url = f"{base_url.rstrip('/')}{rel_url}" if base_url else rel_url

    _json_note = ""
    if emit_json:
        _json_rel = _sidecar_json_path(out_path).name
        # Relative on purpose — card configs fetching ``/local/...``
        # work from any origin the dashboard is opened on.
        _json_url = f"/local/yidcal-data/{_json_rel}"
        _json_note = (
            f"\n\nData (JSON) for a dashboard/card:\n`{_json_url}`"
        )

    persistent_notification.async_create(
        hass,
        message=(
            f"Luach PDF saved to `{out_path}`.\n\n"
            # Raw HTML anchor with target="_blank" — HA's frontend
            # SPA router intercepts same-origin clicks from markdown
            # ``[text](url)`` links and treats them as in-app
            # navigation (which lands on the dashboard since
            # ``/local/...`` isn't a panel). Setting target="_blank"
            # tells the router to let the browser handle the click
            # natively, so the PDF opens in a new tab.
            f'<a href="{rel_url}" target="_blank" rel="noopener noreferrer">'
            f"Open the file</a> (opens in a new tab)\n\n"
            f"If the link above doesn't open, copy this URL:\n"
            f"`{full_url}`\n\n"
            f"Date range: {start_d.isoformat()} → {end_d.isoformat()}"
            + _json_note
            + (
                f"\n\nTip: download/save the file if you want to keep it — "
                f"YidCal auto-keeps only the {_KEEP_RECENT} most-recent "
                f"luach files in this folder ({_KEEP_RECENT} PDFs and, "
                f"if enabled, {_KEEP_RECENT} JSONs) and removes older "
                f"ones, so the folder never fills up."
                if _KEEP_RECENT > 0 else ""
            )
        ),
        title="YidCal luach generated",
        notification_id=f"yidcal_luach_{rel}",
    )
    _LOGGER.info("YidCal: generated luach → %s", out_path)


# ── Date range resolution ─────────────────────────────────────────────

def _resolve_date_range(data) -> tuple[date_cls, date_cls]:
    """Defaults: start = today, end = ב׳ תשרי of NEXT Hebrew year
    (matches the Monroe luach's traditional end-point).

    Special case: when ``hebrew_year`` is supplied, generate a full year
    from Tishrei 1 of that Hebrew year through Tishrei 2 of the following
    Hebrew year, ignoring start_date/end_date. This matches the printed
    luach convention of one Hebrew-year-named volume (e.g. "תשפ״ז").
    """
    from pyluach.hebrewcal import HebrewDate as PHebrewDate

    hy = data.get("hebrew_year")
    if hy is not None:
        hy = int(hy)
        # Start: 29 Elul of (hy-1) = Erev Rosh Hashanah of hy. We
        # generate from Erev RH so the candle-lighting + 2-day YT
        # block at the very start of the year is anchored by the
        # Erev row (matching the rest of the luach, where every YT
        # block is introduced by its Erev row). Without this the
        # luach would open mid-block on Tishrei 2, missing the Erev
        # row entirely.
        # End: the printed תשפ״ו-style booklet does NOT stop at the
        # last parsha of its own year — it carries the first weeks of
        # the NEXT year as a preview: the ערב-ראש-השנה week and the
        # האזינו week of (hy+1), each sub-titled with the incoming
        # year (שנת …). Extend the range to the END (Shabbos) of the
        # האזינו week of (hy+1). Found generically as the first
        # Shabbos on/after RH(hy+1) whose parsha is האזינו (handles
        # every year-type: Shabbos-Shuva-Haazinu and Haazinu-after-YK).
        start = PHebrewDate(hy - 1, 6, 29).to_pydate()  # 29 Elul hy-1
        try:
            from .luach_data import _weekly_resolve_week
            from . import halacha_events as _he2
            _rh_next = PHebrewDate(hy + 1, 7, 1).to_pydate()
            _d = _rh_next
            _haazinu_sat = None
            for _ in range(45):
                if _d.weekday() == 5:  # Shabbos
                    try:
                        _p = _he2.parsha_name(
                            _d, diaspora=True,
                            metzora_display="hyphen",
                        )
                    except Exception:
                        _p = ""
                    if "האזינו" in (_p or ""):
                        _haazinu_sat = _d
                        break
                _d += timedelta(days=1)
            if _haazinu_sat is not None:
                _, end = _weekly_resolve_week(_haazinu_sat)
            else:
                end = PHebrewDate(hy, 6, 29).to_pydate()
        except Exception:
            end = PHebrewDate(hy, 6, 29).to_pydate()
        # A Hebrew year + one extra month is well under _MAX_RANGE_DAYS.
        return start, end

    today = datetime.now().date()
    start = data.get("start_date") or today
    end = data.get("end_date")
    if end is None:
        # Default: end at ב׳ תשרי of the Hebrew year AFTER the one in
        # which `start` falls (so a luach generated mid-year carries us
        # well past the next Rosh Hashanah into early Tishrei).
        start_hy = PHebrewDate.from_pydate(start).year
        # If start is past Tishrei, ph.year already reflects the new
        # year boundary in pyluach's convention. Default to RH+1 year +
        # 2 Tishrei.
        target_hy = start_hy + 1
        end = PHebrewDate(target_hy, 7, 2).to_pydate()

    if end < start:
        raise ServiceValidationError("end_date must be on or after start_date.")
    if (end - start).days > _MAX_RANGE_DAYS:
        raise ServiceValidationError(
            f"Requested range exceeds {_MAX_RANGE_DAYS} days. "
            f"Split into smaller ranges."
        )
    return start, end


# ── Location resolution ───────────────────────────────────────────────

async def _resolve_location(
    hass: HomeAssistant, raw_location: str | None,
) -> tuple[float, float, str, str]:
    """Return (lat, lon, tzname, location_name).

    ``location_name`` is a human-readable string identifying the
    location used. Sources, matching the resolution order below:
      • Per-call geocoded query   → the geocoder's ``display_name``
        (e.g. ``"Lakewood, Ocean County, NJ, USA"``).
      • Cached HA-config snap     → the integration's pre-formatted
        ``"City, State"`` string (e.g. ``"Brooklyn, New York"``).
      • Raw HA-config fallback    → empty (the subtitle then falls
        back to lat/lon coordinates).

    Resolution order:
      1. If ``raw_location`` is supplied (zip/city/landmark), geocode it
         via ``zman_geocoder`` — this is an explicit per-call override.
      2. Otherwise, use YidCal's cached ``resolved_location`` from the
         config entry. This is what every other YidCal sensor uses —
         it's the result of running ``resolve_location_from_coordinates``
         at setup time, which snaps Monroe-area HA configs to the
         canonical Kiryas Joel centroid (41.34202, -74.1762), Monsey-
         area to its centroid, and everywhere else through Nominatim
         reverse + forward geocoding to the official city centroid.
         Using the same source here means the luach times match what
         the live sensors show.
      3. As a last resort (no cached snap yet — e.g. very first run
         before the snap completes), fall back to HA's raw configured
         coordinates.
    """
    if raw_location and str(raw_location).strip():
        from .zman_geocoder import resolve_location
        resolved = await resolve_location(hass, str(raw_location))
        return (
            resolved.latitude,
            resolved.longitude,
            resolved.tzname,
            resolved.display_name,
        )

    # Try the cached snap from YidCal's config entry (matches the
    # coordinates used by every other YidCal sensor).
    from homeassistant.config_entries import ConfigEntry
    entries: list[ConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    if entries:
        entry_data = {**(entries[0].data or {}), **(entries[0].options or {})}
        snapped = entry_data.get("resolved_location") or {}
        snap_lat = snapped.get("lat")
        snap_lon = snapped.get("lon")
        snap_tz = snapped.get("tzname")
        if snap_lat is not None and snap_lon is not None:
            # Use the same "City, State" string the integration exposes
            # on its zman sensors' City attribute (e.g. "Brooklyn,
            # New York"). Strip the "Town of " prefix to match sensor
            # behavior.
            city_str = (
                hass.data.get(DOMAIN, {})
                .get("config", {})
                .get("city", "")
                .replace("Town of ", "")
            )
            return (
                float(snap_lat),
                float(snap_lon),
                str(snap_tz) if snap_tz else str(hass.config.time_zone or "UTC"),
                city_str,
            )

    # Fall back to HA's raw configured coordinates.
    lat = hass.config.latitude
    lon = hass.config.longitude
    tzname = str(hass.config.time_zone) if hass.config.time_zone else "UTC"
    if lat is None or lon is None:
        raise ServiceValidationError(
            "No location configured. Set HA's home location, or pass a "
            "'location' parameter (e.g. zip code or city)."
        )
    return (float(lat), float(lon), tzname, "")


# ── Config-entry option reads ─────────────────────────────────────────

def _read_config_options(hass: HomeAssistant) -> tuple[bool, int, int, str]:
    """Pull diaspora flag, candle offset, havdalah offset, and metzora
    display preference from the YidCal config entry.

    Returns (diaspora, candle_offset_min, havdalah_offset_min, metzora_display).
    """
    from homeassistant.config_entries import ConfigEntry

    # Find the YidCal config entry (there's at most one).
    entries: list[ConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(
            "YidCal is not configured. Add the integration before "
            "generating a luach."
        )
    entry = entries[0]
    data = {**(entry.data or {}), **(entry.options or {})}

    is_israel = bool(data.get("is_in_israel", False))
    diaspora = not is_israel
    candle = int(data.get("candle_offset", data.get("candlelighting_offset", 15)))
    havdalah = int(data.get("havdalah_offset", 72))
    metzora = str(data.get("parsha_metzora_display", "metzora"))
    return diaspora, candle, havdalah, metzora


# ── Molad provider ────────────────────────────────────────────────────

def _make_molad_provider(tzname: str):
    """Return a callable(date) → Molad backed by YidCalHelper."""
    from types import SimpleNamespace
    from .helper import YidCalHelper

    helper = YidCalHelper(SimpleNamespace(time_zone=tzname))

    def _provider(target_date: date_cls):
        try:
            return helper.get_actual_molad(target_date)
        except Exception:
            return None

    return _provider


# ── Output-path resolution ────────────────────────────────────────────

def _location_slug(raw: str) -> str:
    """Filesystem-safe slug derived from a location string.

    Takes the first comma-separated segment (so ``"Brooklyn, New
    York"`` → ``Brooklyn``, ``"10952"`` → ``10952``) and reduces it
    to ``[A-Za-z0-9-]+`` — runs of non-alphanumerics collapse to
    a single hyphen, leading/trailing hyphens are stripped. Capped
    at 30 chars so very long Nominatim display names don't blow up
    the filename. Empty input returns ``""``.
    """
    if not raw:
        return ""
    import re
    first = raw.split(",")[0].strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", first).strip("-")
    return slug[:30]


_GENERATED_TS_RE = re.compile(r"_\d{8}-\d{6}\.(?:pdf|json)$")


def _prune_old_outputs(
    hass: HomeAssistant, keep: int = _KEEP_RECENT,
) -> list[str]:
    """Delete stale auto-generated luach files so they don't pile up.

    Covers both the PDF and (when enabled) its sidecar ``.json``.
    Applies a GLOBAL cap: keeps the ``keep`` most-recent ``.pdf``
    files in the output directory (across all styles / year-ranges /
    locations combined) and, INDEPENDENTLY, the ``keep`` most-recent
    ``.json`` files. Everything older is deleted. Only files that
    carry our generated-timestamp suffix are considered, so a user's
    custom ``output_path`` (and its JSON twin) and any unrelated file
    are never deleted. Best-effort: any filesystem error is logged
    and swallowed — pruning must never break or fail a successful
    generation.

    Returns the LIST of deleted file basenames (empty when nothing
    was removed). Caller can use it to post a user-facing
    notification.
    """
    if keep <= 0:
        return []
    try:
        default_dir = Path(hass.config.config_dir) / _OUTPUT_SUBDIR
        if not default_dir.is_dir():
            return []
        mine = [
            f
            for pat in ("luach_*.pdf", "luach_*.json")
            for f in default_dir.glob(pat)
            if f.is_file() and _GENERATED_TS_RE.search(f.name)
        ]
    except OSError as err:
        _LOGGER.warning("Luach output prune skipped (listing failed): %s", err)
        return []

    # Bucket by extension only — PDFs and JSONs are capped
    # independently (so enabling the JSON sidecar doesn't shrink the
    # PDF retention, and vice-versa).
    by_ext: dict[str, list[Path]] = {}
    for f in mine:
        by_ext.setdefault(f.suffix.lower(), []).append(f)

    deleted: list[str] = []
    for files in by_ext.values():
        try:
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for old in files[keep:]:
            try:
                old.unlink()
                deleted.append(old.name)
            except OSError as err:
                _LOGGER.warning(
                    "Luach output prune: could not delete %s: %s",
                    old, err,
                )
    if deleted:
        _LOGGER.debug(
            "Luach output prune: removed %d old file(s); "
            "kept %d most-recent per extension",
            len(deleted), keep,
        )
    return deleted


def _json_default(o):
    """JSON encoder hook: datetimes/dates → ISO 8601 strings.

    (datetime is a subclass of date, so it must be checked first.)
    """
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, date_cls):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


# Bump when the sidecar JSON shape changes in a breaking way so a
# card can guard on it (``payload["schema_version"]``).
_JSON_SCHEMA_VERSION = 1


def _sidecar_json_path(pdf_path: Path) -> Path:
    """The sidecar JSON path for a given PDF output path (same dir +
    stem, ``.json`` extension)."""
    return pdf_path.with_suffix(".json")


def _write_sidecar_json(pdf_path: Path, payload: dict) -> Path:
    """Write ``payload`` as pretty UTF-8 JSON next to the PDF and
    return the JSON path. Hebrew is kept literal (ensure_ascii=False);
    datetimes/dates are ISO strings. Best-effort caller handles
    failures — never let JSON emission break PDF delivery.
    """
    jpath = _sidecar_json_path(pdf_path)
    jpath.write_text(
        json.dumps(
            payload, ensure_ascii=False, indent=2, default=_json_default,
        ),
        encoding="utf-8",
    )
    return jpath


def _json_weekly_payload(
    weeks, *, location: dict, diaspora: bool,
) -> dict:
    """Serialize the weekly-card data (one entry per printed card).

    ``weeks`` is the list of ``WeeklyData`` cards. ``dataclasses.asdict``
    recurses the nested ``WeeklyBox`` / ``WeeklyDayRow`` dataclasses;
    the ``_json_default`` hook turns the per-zman datetimes into ISO
    strings at dump time.
    """
    return {
        "schema_version": _JSON_SCHEMA_VERSION,
        "kind": "weekly",
        "generated_at": datetime.now().isoformat(),
        "diaspora": diaspora,
        "location": location,
        "weeks": [dataclasses.asdict(wd) for wd in weeks],
    }


def _json_yearly_payload(
    items, *, kind: str, title_he: str, subtitle_he: str,
    notes_he: str, location: dict, diaspora: bool,
    hebrew_year, start: date_cls, end: date_cls,
) -> dict:
    """Serialize the yearly luach item list. Each row is tagged
    ``row_type`` = ``luach`` (a candle-lighting row) or ``annotation``
    (an interleaved Mevorchim / Tekufah / fast-times text row)."""
    rows = []
    for it in items:
        d = dataclasses.asdict(it)
        d["row_type"] = (
            "annotation" if isinstance(it, AnnotationRow) else "luach"
        )
        rows.append(d)
    return {
        "schema_version": _JSON_SCHEMA_VERSION,
        "kind": kind,
        "generated_at": datetime.now().isoformat(),
        "title_he": title_he,
        "subtitle_he": subtitle_he,
        "notes_he": notes_he,
        "hebrew_year": (int(hebrew_year) if hebrew_year is not None else None),
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "diaspora": diaspora,
        "location": location,
        "rows": rows,
    }


def _resolve_output_path(
    hass: HomeAssistant,
    override: str | None,
    start: date_cls,
    end: date_cls,
    *,
    location_slug: str = "",
    style: str = "",
) -> Path:
    """Default to /config/www/yidcal-data/luach_<style>_<years>[_<loc>]_<ts>.pdf.

    If ``override`` is an absolute path, use it verbatim (caller's
    responsibility to ensure HA can write there). Otherwise treat as
    a filename under the default directory.

    ``style`` (one of ``weekly`` / ``yearly_multi_page`` /
    ``yearly_sheet``) is prefixed into the default name so the file
    says which luach it is at a glance. When ``location_slug`` is
    non-empty it's injected between the year range and timestamp
    (e.g. ``luach_yearly_sheet_2025-2026_10952_….pdf``). An explicit
    ``override`` is used verbatim and is NOT decorated with the style.
    """
    config_dir = Path(hass.config.config_dir)
    default_dir = config_dir / _OUTPUT_SUBDIR
    default_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    style_tag = f"{style}_" if style else ""
    if location_slug:
        default_name = (
            f"luach_{style_tag}{start.year}-{end.year}"
            f"_{location_slug}_{ts}.pdf"
        )
    else:
        default_name = (
            f"luach_{style_tag}{start.year}-{end.year}_{ts}.pdf"
        )

    if override:
        p = Path(override)
        if not p.is_absolute():
            p = default_dir / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    return default_dir / default_name


# ── Title strings ─────────────────────────────────────────────────────

def _build_titles(
    start: date_cls, end: date_cls,
    lat: float, lon: float, tzname: str,
    *, city: str = "",
    hebrew_year_override: int | None = None,
    candle_offset: int | None = None,
    havdalah_offset: int | None = None,
    style: str = "yearly_multi_page",
) -> tuple[str, str, str]:
    """Build the Hebrew title, subtitle, and convention-notes strings
    shown on page 1.

    When ``city`` is provided (the geocoded "City, State" string the
    integration populated at setup time, e.g. "Brooklyn, New York" or
    "Monroe, NY"), use it for the subtitle. Otherwise fall back to the
    raw lat/lon coordinates.

    When ``hebrew_year_override`` is provided (the user invoked the
    Hebrew-year shortcut, so we know the luach is for that single
    year regardless of the underlying date range — which spans Erev
    RH of `hy` through 2 Tishrei of `hy+1`), title shows only that
    year. Otherwise title is derived from the date range, showing a
    single year when start/end fall in the same Hebrew year and a
    "start-end" pair when they don't.

    ``style`` is kept as a parameter for future per-style overrides
    but currently both ``yearly_multi_page`` and ``yearly_sheet`` use
    the same formal title — ``f"לוח הזמנים לשנת {years} לפ\"ק"`` —
    matching the standard printed-luach convention.

    When ``candle_offset``/``havdalah_offset`` are provided, returns a
    third string describing the conventions in use. Empty string
    otherwise.
    """
    from pyluach.hebrewcal import HebrewDate as PHebrewDate
    from . import halacha_events as he

    if hebrew_year_override is not None:
        years_he = he.hebrew_year_letters(int(hebrew_year_override))
    else:
        start_hy = PHebrewDate.from_pydate(start).year
        end_hy = PHebrewDate.from_pydate(end).year
        start_he = he.hebrew_year_letters(start_hy)
        if start_hy == end_hy:
            years_he = start_he
        else:
            end_he = he.hebrew_year_letters(end_hy)
            years_he = f"{start_he}-{end_he}"

    # Unified title across both styles — matches the standard printed
    # luach convention ("לוח הזמנים לשנת תשפ\"ו לפ\"ק").
    title = f'לוח הזמנים לשנת {years_he} לפ"ק'
    if city:
        # City already implies the timezone for anyone reading the
        # luach, so showing tzname here would be redundant
        # ("Monsey, NY · America/New_York"). Keep it in the lat/lon
        # fallback below where the coordinates alone don't communicate
        # timezone.
        subtitle = city
    else:
        subtitle = f"({lat:.4f}, {lon:.4f}) {INFO_SEP} {tzname}"

    # Convention notes line:
    #   "זמן הדלקת הנרות 15 מינוט קודם השקיעה, וזמן מוש"ק הוא לפי שיטת ר"ת"
    # For non-72-min havdalah, drop the "ר"ת" reference and just state
    # the minute count.
    notes = ""
    if candle_offset is not None and havdalah_offset is not None:
        candle_part = (
            f"זמן הדלקת הנרות {candle_offset} מינוט קודם השקיעה"
        )
        if havdalah_offset == 72:
            havdalah_part = 'וזמן מוצש"ק הוא לפי שיטת ר"ת'
        else:
            havdalah_part = (
                f'וזמן מוצש"ק {havdalah_offset} מינוט אחר השקיעה'
            )
        notes = f"{candle_part}, {havdalah_part}"

    return title, subtitle, notes
