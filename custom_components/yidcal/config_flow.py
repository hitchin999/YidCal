import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    DOMAIN,
    CALENDAR_CHOICES,
    CALENDAR_DATE_EXTRA_CHOICES,
    ZMAN_CALENDAR_CHOICES,
)
from .yidcal_lib.alos_options import (
    ALOS_OPTIONS,
    DEFAULT_ALOS_OPTION,
    FOLLOW_PRIMARY,
)
from . import ui_strings as S
from .ui_strings import CONF_UI_LANGUAGE

# ============ Existing/general keys (unchanged) ============
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72
DEFAULT_TALLIS_TEFILIN_OFFSET = 22
CONF_INCLUDE_DATE = "include_date"
DEFAULT_DAY_LABEL_LANGUAGE = "yiddish"
CONF_INCLUDE_ATTR_SENSORS = "include_attribute_sensors"
CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL = "include_sefirah_short_in_full"
DEFAULT_INCLUDE_SEFIRAH_SHORT_IN_FULL = False
CONF_ENABLE_WEEKLY_YURTZEIT = "enable_weekly_yurtzeit"  # keep key name as-is
CONF_SLICHOS_LABEL_ROLLOVER = "slichos_label_rollover"
DEFAULT_SLICHOS_LABEL_ROLLOVER = "havdalah"
CONF_KIDDUSH_LEVANA_START = "kiddush_levana_start"
DEFAULT_KIDDUSH_LEVANA_START = "zayin"
CONF_UPCOMING_LOOKAHEAD_DAYS = "upcoming_lookahead_days"
DEFAULT_UPCOMING_LOOKAHEAD_DAYS = 2
CONF_IS_IN_ISRAEL = "is_in_israel"
DEFAULT_IS_IN_ISRAEL = False
CONF_TIME_FORMAT = "time_format"
DEFAULT_TIME_FORMAT = "12"

# ============ Multi-day candle lighting sensors ============
CONF_ENABLE_MULTIDAY_CANDLES = "enable_multiday_candles"
DEFAULT_ENABLE_MULTIDAY_CANDLES = False

# ============ Zmanim Lookup (options-only) ============
# Exposes sensor.yidcal_zmanim_lookup plus the yidcal.check_zmanim
# service. Not shown on first-time setup — only in the options /
# reconfigure flow — because the sensor's state is empty until the
# service is called, which can confuse users unfamiliar with service
# calls.
CONF_ENABLE_ZMANIM_LOOKUP = "enable_zmanim_lookup"
DEFAULT_ENABLE_ZMANIM_LOOKUP = False

# ============ Luach PDF generator ============
# Options-only toggle. Registers the yidcal.generate_luach service
# which creates a printable luach PDF under /config/www/yidcal-data/.
# Defaults ON so the service is available out of the box; kept out of
# the initial setup screen (it lives in Options) to avoid cluttering
# first-run — users can uncheck it there if they don't want it.
CONF_ENABLE_LUACH_PDF = "enable_luach_pdf"
DEFAULT_ENABLE_LUACH_PDF = True

# ============ Multi-year luach JSON ============
# YidCal always keeps ONE fixed-name JSON of the current Hebrew year's
# luach at /config/www/yidcal-data/luach_erev_rosh_hashanah.json,
# refreshed on Erev Rosh Hashanah. Turning this on additionally keeps a
# ROLLING WINDOW of per-year files — one year behind plus N years ahead —
# named luach_erev_rosh_hashanah_<hebrew year>.json, with an index at
# luach_years.json listing what's available.
#
# The window follows the year: each Erev Rosh Hashanah it gains the new
# far year and drops the one that fell off the back, so there is always
# exactly one past year on disk. Only meaningful while the Luach PDF
# feature above is enabled (it reuses that service).
CONF_LUACH_JSON_MULTIYEAR = "luach_json_multiyear"
DEFAULT_LUACH_JSON_MULTIYEAR = False
CONF_LUACH_JSON_YEARS_AHEAD = "luach_json_years_ahead"
DEFAULT_LUACH_JSON_YEARS_AHEAD = 5
MIN_LUACH_JSON_YEARS_AHEAD = 1
MAX_LUACH_JSON_YEARS_AHEAD = 10

# ============ Haftorah Minhag ============
# Selects which minhag the Haftorah sensor's STATE follows. Both
# minhagim are always published as the אשכנזי / ספרדי attributes, so
# this only picks the headline reading.
CONF_HAFTORAH_MINHAG = "haftorah_minhag"
DEFAULT_HAFTORAH_MINHAG = "ashkenazi"

# ============ Molad display language ============
# Which language sensor.yidcal_molad's STATE is written in. English and
# Hebrew renderings are always available as attributes regardless, so
# this only picks the headline. Default keeps the historical Yiddish.
CONF_MOLAD_LANGUAGE = "molad_language"
DEFAULT_MOLAD_LANGUAGE = "yiddish"

# ============ Calendars ============
# Four fields, not fifteen. The first draft asked a separate yes/no
# question per calendar, which turned the page into a wall of switches
# nobody wants to read. Picking from a list says the same thing in one
# glance, and stays one screen tall however many calendars get added.
#
# Master switch. When off, the calendar platform adds nothing and the
# "YidCal — Calendars" device never appears.
CONF_ENABLE_CALENDARS = "enable_calendars"
DEFAULT_ENABLE_CALENDARS = False

# Multi-select of calendar keys — see const.CALENDAR_CHOICES.
CONF_CALENDARS = "calendars"
DEFAULT_CALENDARS: list[str] = ["date"]

# Multi-select of decorations on the Date calendar's event titles —
# see const.CALENDAR_DATE_EXTRA_CHOICES.
#   parsha  → "י״ג אב תשפ״ו - פרשת צו"
#   weekday → "י״ג אב תשפ״ו - ג׳ צו"
#   both    → "י״ג אב תשפ״ו - ג׳ פרשת צו"
CONF_CAL_DATE_EXTRAS = "calendar_date_extras"
DEFAULT_CAL_DATE_EXTRAS: list[str] = []

# Multi-select of zman keys (see const.ZMAN_CALENDAR_CHOICES). Each
# selected zman becomes its own calendar entity.
CONF_CAL_ZMANIM = "calendar_zmanim"
DEFAULT_CAL_ZMANIM: list[str] = []

# ============ Alos HaShachar ============
# Which opinion sensor.yidcal_alos SHOWS. Every opinion is published as
# an attribute regardless, so this only picks the state. The default is
# the historical 72 fixed minutes before sunrise.
#
# Deliberately does NOT move the MGA sha'ah zmanis — see the note on
# zman_compute._ALOS_OFFSET_MIN.
CONF_ALOS_METHOD = "alos_method"
DEFAULT_ALOS_METHOD = DEFAULT_ALOS_OPTION

# Extra Alos sensors, one per opinion picked.
CONF_ALOS_EXTRA_SENSORS = "alos_extra_sensors"
DEFAULT_ALOS_EXTRA_SENSORS: list[str] = []

# Which Alos the Talis & Tefilin offset counts from. "primary" follows
# CONF_ALOS_METHOD; any option key pins it independently.
CONF_TALLIS_TEFILIN_BASE = "tallis_tefilin_base"
DEFAULT_TALLIS_TEFILIN_BASE = FOLLOW_PRIMARY

# ============ Parsha Metzora display ============
# "metzora" (default) shows "מצורע"; "tahara" shows "טהרה".
CONF_PARSHA_METZORA_DISPLAY = "parsha_metzora_display"
DEFAULT_PARSHA_METZORA_DISPLAY = "metzora"

# ============ Yurtzeit keys ============
CONF_ENABLE_YURTZEIT_DAILY = "enable_yurtzeit_daily"
CONF_YURTZEIT_DATABASES = "yurtzeit_databases"
DEFAULT_YURTZEIT_DATABASES = ["standard"]

# ============ Legacy (for migration/back-compat only) ============
CONF_YAHRTZEIT_DATABASE = "yahrtzeit_database"  # old single-select spelling
DEFAULT_YAHRTZEIT_DATABASE = "standard"

# ============ Early Entry keys ============
CONF_ENABLE_EARLY_SHABBOS = "enable_early_shabbos"
CONF_EARLY_SHABBOS_MODE = "early_shabbos_mode"  # default behavior
CONF_EARLY_SHABBOS_PLAG_METHOD = "early_shabbos_plag_method"
CONF_EARLY_SHABBOS_FIXED_TIME = "early_shabbos_fixed_time"
CONF_EARLY_SHABBOS_APPLY_RULE = "early_shabbos_apply_rule"
CONF_EARLY_SHABBOS_SUNSET_AFTER = "early_shabbos_sunset_after"

DEFAULT_ENABLE_EARLY_SHABBOS = False
DEFAULT_EARLY_SHABBOS_MODE = "plag"  # plag | fixed | disabled
DEFAULT_EARLY_SHABBOS_PLAG_METHOD = "gra"  # gra | ma
DEFAULT_EARLY_SHABBOS_FIXED_TIME = "19:00:00"
DEFAULT_EARLY_SHABBOS_APPLY_RULE = "every_friday"  # every_friday | sunset_after
DEFAULT_EARLY_SHABBOS_SUNSET_AFTER = "19:00:00"

CONF_ENABLE_EARLY_YOMTOV = "enable_early_yomtov"
CONF_EARLY_YOMTOV_MODE = "early_yomtov_mode"  # default behavior
CONF_EARLY_YOMTOV_PLAG_METHOD = "early_yomtov_plag_method"
CONF_EARLY_YOMTOV_FIXED_TIME = "early_yomtov_fixed_time"
CONF_EARLY_YOMTOV_INCLUDE = "early_yomtov_include"  # whitelist
CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS = "early_yomtov_allow_second_days"

DEFAULT_ENABLE_EARLY_YOMTOV = False
DEFAULT_EARLY_YOMTOV_MODE = "plag"  # plag | fixed | disabled
DEFAULT_EARLY_YOMTOV_PLAG_METHOD = "gra"
DEFAULT_EARLY_YOMTOV_FIXED_TIME = "19:00:00"

# ============ Krias HaTorah extras ============
CONF_KORBANOS_YUD_GIMMEL_MIDOS = "korbanos_yud_gimmel_midos"
DEFAULT_KORBANOS_YUD_GIMMEL_MIDOS = False

CONF_MISHNE_TORAH_HOSHANA_RABBA = "mishne_torah_hoshana_rabba"
DEFAULT_MISHNE_TORAH_HOSHANA_RABBA = False

# ============ Daf HaYomi ============
CONF_ENABLE_DAF_HAYOMI = "enable_daf_hayomi"
DEFAULT_ENABLE_DAF_HAYOMI = True

# Conservative defaults per our plan
DEFAULT_EARLY_YOMTOV_INCLUDE = [
    "rosh_hashana",
    "yom_kippur",
    "sukkos",
    "shemini_atzeres",
    "pesach_last_days",
]
DEFAULT_EARLY_YOMTOV_ALLOW_SECOND_DAYS = False


# ===========================================================================
# Language-aware form/menu plumbing
#
# Every visible string in the flow now comes from ui_strings.py at render
# time, via description_placeholders. strings.json is only a template of
# "{token}" markers. See the ui_strings module docstring for why.
#
# THE ONE RULE: never call async_show_form()/async_show_menu() directly.
# Go through _form()/_menu() below, which always attach the complete
# placeholder set. A single missing placeholder makes the HA frontend
# print "Translation Error …" in place of every label on that step.
# ===========================================================================
class _LangFlowMixin:
    """Shared render helpers. `_ns` selects the strings.json section."""

    _ns: str = "config"
    _lang: str = S.DEFAULT_UI_LANGUAGE

    def _form(self, step_id, schema, errors=None):
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema,
            errors=errors,
            description_placeholders=S.placeholders(f"{self._ns}.{step_id}", self._lang),
        )

    def _menu(self, step_id, menu_options):
        # Dict menu_options are rendered verbatim by the frontend — no
        # translation keys involved. Menu steps carry no title in
        # strings.json (renderMenuHeader does not pass placeholders), so
        # the header falls back to "YidCal".
        return self.async_show_menu(
            step_id=step_id,
            menu_options=menu_options,
            description_placeholders=S.placeholders(f"{self._ns}.{step_id}", self._lang),
        )

    def _language_menu(self, step_id, prefix, current):
        """Language picker. The current/guessed language is listed first."""
        order = [current] + [l for l in S.UI_LANGUAGES if l != current]
        return self._menu(
            step_id,
            {f"{prefix}{l}": S.LANGUAGE_NAMES[l] for l in order},
        )


# ---------------------------------------------------------------------------
# Schema builders — shared by the config flow and the options flow so the
# two can never drift. `get(key, default)` supplies the current value.
# ---------------------------------------------------------------------------
def _general_schema(lang, get, *, include_luach_pdf: bool):
    fields = {
        vol.Optional(CONF_IS_IN_ISRAEL, default=get(CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL)): bool,
        vol.Optional("strip_nikud", default=get("strip_nikud", False)): bool,
        vol.Optional("candlelighting_offset", default=get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET)): int,
        vol.Optional("havdalah_offset", default=get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET)): int,
        # tallis_tefilin_offset moved to the Zmanim step, next to the
        # Alos opinion it is measured from.
        vol.Optional(
            CONF_KORBANOS_YUD_GIMMEL_MIDOS,
            default=get(CONF_KORBANOS_YUD_GIMMEL_MIDOS, DEFAULT_KORBANOS_YUD_GIMMEL_MIDOS),
        ): bool,
        vol.Optional(
            CONF_MISHNE_TORAH_HOSHANA_RABBA,
            default=get(CONF_MISHNE_TORAH_HOSHANA_RABBA, DEFAULT_MISHNE_TORAH_HOSHANA_RABBA),
        ): bool,
        vol.Optional(
            "day_label_language",
            default=get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE),
        ): selector({"select": {"options": S.sel("day_label_language", lang)}}),
        vol.Optional(
            CONF_HAFTORAH_MINHAG,
            default=get(CONF_HAFTORAH_MINHAG, DEFAULT_HAFTORAH_MINHAG),
        ): selector({"select": {"options": S.sel("haftorah_minhag", lang)}}),
        vol.Optional(
            CONF_MOLAD_LANGUAGE,
            default=get(CONF_MOLAD_LANGUAGE, DEFAULT_MOLAD_LANGUAGE),
        ): selector({"select": {"options": S.sel("molad_language", lang)}}),
        vol.Optional(
            CONF_PARSHA_METZORA_DISPLAY,
            default=get(CONF_PARSHA_METZORA_DISPLAY, DEFAULT_PARSHA_METZORA_DISPLAY),
        ): selector({"select": {"options": S.sel("parsha_metzora_display", lang)}}),
        vol.Optional(
            CONF_TIME_FORMAT,
            default=get(CONF_TIME_FORMAT, DEFAULT_TIME_FORMAT),
        ): selector({"select": {"options": S.sel("time_format", lang)}}),
        vol.Optional(CONF_INCLUDE_DATE, default=get(CONF_INCLUDE_DATE, False)): bool,
        vol.Optional(CONF_INCLUDE_ATTR_SENSORS, default=get(CONF_INCLUDE_ATTR_SENSORS, True)): bool,
        vol.Optional(
            CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL,
            default=get(CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL, DEFAULT_INCLUDE_SEFIRAH_SHORT_IN_FULL),
        ): bool,
        vol.Optional(
            CONF_ENABLE_MULTIDAY_CANDLES,
            default=get(CONF_ENABLE_MULTIDAY_CANDLES, DEFAULT_ENABLE_MULTIDAY_CANDLES),
        ): bool,
        vol.Optional(
            CONF_ENABLE_DAF_HAYOMI,
            default=get(CONF_ENABLE_DAF_HAYOMI, DEFAULT_ENABLE_DAF_HAYOMI),
        ): bool,
        vol.Optional(
            CONF_SLICHOS_LABEL_ROLLOVER,
            default=get(CONF_SLICHOS_LABEL_ROLLOVER, DEFAULT_SLICHOS_LABEL_ROLLOVER),
        ): selector({"select": {"options": S.sel("slichos_label_rollover", lang)}}),
        vol.Optional(
            CONF_KIDDUSH_LEVANA_START,
            default=get(CONF_KIDDUSH_LEVANA_START, DEFAULT_KIDDUSH_LEVANA_START),
        ): selector({"select": {"options": S.sel("kiddush_levana_start", lang)}}),
        vol.Optional(
            CONF_UPCOMING_LOOKAHEAD_DAYS,
            default=get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS),
        ): selector({
            "number": {
                "min": 1,
                "max": 14,
                "step": 1,
                "mode": "slider",
                "unit_of_measurement": S.unit_days(lang),
            }
        }),
        # Zmanim Lookup — exposes sensor.yidcal_zmanim_lookup +
        # the yidcal.check_zmanim service.
        vol.Optional(
            CONF_ENABLE_ZMANIM_LOOKUP,
            default=get(CONF_ENABLE_ZMANIM_LOOKUP, DEFAULT_ENABLE_ZMANIM_LOOKUP),
        ): bool,
    }
    if include_luach_pdf:
        # Options-only (advanced). Exposes the yidcal.generate_luach
        # service. No sensor is added; the service produces files under
        # /config/www/yidcal-data/.
        fields[
            vol.Optional(
                CONF_ENABLE_LUACH_PDF,
                default=get(CONF_ENABLE_LUACH_PDF, DEFAULT_ENABLE_LUACH_PDF),
            )
        ] = bool
        # Rolling multi-year luach JSON window. Both fields do nothing
        # unless the toggle above is on — they ride the same service.
        fields[
            vol.Optional(
                CONF_LUACH_JSON_MULTIYEAR,
                default=get(CONF_LUACH_JSON_MULTIYEAR, DEFAULT_LUACH_JSON_MULTIYEAR),
            )
        ] = bool
        fields[
            vol.Optional(
                CONF_LUACH_JSON_YEARS_AHEAD,
                default=get(CONF_LUACH_JSON_YEARS_AHEAD, DEFAULT_LUACH_JSON_YEARS_AHEAD),
            )
        ] = selector({
            "number": {
                "min": MIN_LUACH_JSON_YEARS_AHEAD,
                "max": MAX_LUACH_JSON_YEARS_AHEAD,
                "step": 1,
                "mode": "slider",
                "unit_of_measurement": S.unit_years(lang),
            }
        })
    return vol.Schema(fields)


def _calendars_schema(lang, get):
    """One switch and three pick-lists.

    Nothing is created until ``enable_calendars`` is on; after that,
    every calendar comes from the lists below. All of them land on the
    single "YidCal — Calendars" device.
    """
    return vol.Schema({
        vol.Optional(
            CONF_ENABLE_CALENDARS,
            default=get(CONF_ENABLE_CALENDARS, DEFAULT_ENABLE_CALENDARS),
        ): bool,
        vol.Optional(
            CONF_CALENDARS,
            default=get(CONF_CALENDARS, DEFAULT_CALENDARS),
        ): selector({"select": {"multiple": True, "options": [
            {"value": key, "label": S.calendar_label(key, lang)}
            for key, _heb, _eng in CALENDAR_CHOICES
        ]}}),
        vol.Optional(
            CONF_CAL_DATE_EXTRAS,
            default=get(CONF_CAL_DATE_EXTRAS, DEFAULT_CAL_DATE_EXTRAS),
        ): selector({"select": {"multiple": True, "options": [
            {"value": key, "label": S.calendar_extra_label(key, lang)}
            for key, _heb, _eng in CALENDAR_DATE_EXTRA_CHOICES
        ]}}),
        vol.Optional(
            CONF_CAL_ZMANIM,
            default=get(CONF_CAL_ZMANIM, DEFAULT_CAL_ZMANIM),
        ): selector({"select": {"multiple": True, "options": [
            {"value": key, "label": S.zman_label(key, lang)}
            for key, _heb, _eng in ZMAN_CALENDAR_CHOICES
        ]}}),
    })


def _zmanim_schema(lang, get):
    """Alos HaShachar, and what hangs off it.

    ``tallis_tefilin_offset`` lives here rather than on the General page
    because it is meaningless without knowing which Alos it counts from,
    and that question is right below it.
    """
    alos_options = [
        {"value": opt.key, "label": S.alos_label(opt, lang)}
        for opt in ALOS_OPTIONS
    ]
    return vol.Schema({
        vol.Optional(
            CONF_ALOS_METHOD,
            default=get(CONF_ALOS_METHOD, DEFAULT_ALOS_METHOD),
        ): selector({"select": {"options": alos_options}}),
        vol.Optional(
            CONF_ALOS_EXTRA_SENSORS,
            default=get(CONF_ALOS_EXTRA_SENSORS, DEFAULT_ALOS_EXTRA_SENSORS),
        ): selector({"select": {"multiple": True, "options": alos_options}}),
        vol.Optional(
            "tallis_tefilin_offset",
            default=get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET),
        ): int,
        vol.Optional(
            CONF_TALLIS_TEFILIN_BASE,
            default=get(CONF_TALLIS_TEFILIN_BASE, DEFAULT_TALLIS_TEFILIN_BASE),
        ): selector({"select": {"options": (
            [{"value": FOLLOW_PRIMARY, "label": S.alos_follow_primary(lang)}]
            + alos_options
        )}}),
    })


def _yurtzeit_schema(lang, daily, weekly, dbs):
    return vol.Schema({
        vol.Optional(CONF_ENABLE_YURTZEIT_DAILY, default=daily): bool,
        vol.Optional(CONF_ENABLE_WEEKLY_YURTZEIT, default=weekly): bool,
        vol.Optional(CONF_YURTZEIT_DATABASES, default=dbs): selector({
            "select": {"multiple": True, "options": S.sel("yurtzeit_databases", lang)}
        }),
    })


def _early_shabbos_schema(lang, get):
    return vol.Schema({
        vol.Optional(
            CONF_ENABLE_EARLY_SHABBOS,
            default=get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS),
        ): bool,
        vol.Optional(
            CONF_EARLY_SHABBOS_MODE,
            default=get(CONF_EARLY_SHABBOS_MODE, DEFAULT_EARLY_SHABBOS_MODE),
        ): selector({"select": {"options": S.sel("early_mode", lang)}}),
        vol.Optional(
            CONF_EARLY_SHABBOS_PLAG_METHOD,
            default=get(CONF_EARLY_SHABBOS_PLAG_METHOD, DEFAULT_EARLY_SHABBOS_PLAG_METHOD),
        ): selector({"select": {"options": S.sel("early_plag_method", lang)}}),
        vol.Optional(
            CONF_EARLY_SHABBOS_FIXED_TIME,
            default=get(CONF_EARLY_SHABBOS_FIXED_TIME, DEFAULT_EARLY_SHABBOS_FIXED_TIME),
        ): selector({"time": {}}),
        vol.Optional(
            CONF_EARLY_SHABBOS_APPLY_RULE,
            default=get(CONF_EARLY_SHABBOS_APPLY_RULE, DEFAULT_EARLY_SHABBOS_APPLY_RULE),
        ): selector({"select": {"options": S.sel("early_shabbos_apply_rule", lang)}}),
        vol.Optional(
            CONF_EARLY_SHABBOS_SUNSET_AFTER,
            default=get(CONF_EARLY_SHABBOS_SUNSET_AFTER, DEFAULT_EARLY_SHABBOS_SUNSET_AFTER),
        ): selector({"time": {}}),
    })


def _early_yomtov_schema(lang, get):
    return vol.Schema({
        vol.Optional(
            CONF_ENABLE_EARLY_YOMTOV,
            default=get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV),
        ): bool,
        vol.Optional(
            CONF_EARLY_YOMTOV_MODE,
            default=get(CONF_EARLY_YOMTOV_MODE, DEFAULT_EARLY_YOMTOV_MODE),
        ): selector({"select": {"options": S.sel("early_mode", lang)}}),
        vol.Optional(
            CONF_EARLY_YOMTOV_PLAG_METHOD,
            default=get(CONF_EARLY_YOMTOV_PLAG_METHOD, DEFAULT_EARLY_YOMTOV_PLAG_METHOD),
        ): selector({"select": {"options": S.sel("early_plag_method", lang)}}),
        vol.Optional(
            CONF_EARLY_YOMTOV_FIXED_TIME,
            default=get(CONF_EARLY_YOMTOV_FIXED_TIME, DEFAULT_EARLY_YOMTOV_FIXED_TIME),
        ): selector({"time": {}}),
        vol.Optional(
            CONF_EARLY_YOMTOV_INCLUDE,
            default=get(CONF_EARLY_YOMTOV_INCLUDE, DEFAULT_EARLY_YOMTOV_INCLUDE),
        ): selector({
            "select": {"multiple": True, "options": S.sel("early_yomtov_include", lang)}
        }),
        vol.Optional(
            CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS,
            default=get(CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS, DEFAULT_EARLY_YOMTOV_ALLOW_SECOND_DAYS),
        ): bool,
    })


class YidCalConfigFlow(_LangFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YidCal."""

    VERSION = 1  # no explicit migration necessary
    _ns = "config"

    async def async_step_user(self, user_input=None):
        """Step 1: pick the language for the rest of the setup (one tap)."""
        # Only one instance
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        # Pre-select from the HA language; the user's tap is what counts.
        self._lang = S.guess_ui_language(self.hass)
        return self._language_menu("user", "lang_", self._lang)

    async def async_step_general(self, user_input=None):
        """Step 2: General settings."""
        if user_input is None:
            return self._form(
                "general",
                _general_schema(self._lang, lambda k, d: d, include_luach_pdf=True),
            )

        # Stash general config for the final entry
        self._general_data = dict(user_input)
        return await self.async_step_zmanim()

    async def async_step_zmanim(self, user_input=None):
        """Step 3: Alos HaShachar & Talis/Tefilin."""
        if user_input is None:
            return self._form(
                "zmanim",
                _zmanim_schema(self._lang, lambda k, d: d),
            )

        self._zmanim_data = dict(user_input)
        return await self.async_step_calendars()

    async def async_step_calendars(self, user_input=None):
        """Step 4: Calendars."""
        if user_input is None:
            return self._form(
                "calendars",
                _calendars_schema(self._lang, lambda k, d: d),
            )

        self._calendars_data = dict(user_input)
        return await self.async_step_yurtzeit()

    async def async_step_yurtzeit(self, user_input=None):
        """Step 5: Yurtzeit settings."""
        if user_input is None:
            return self._form(
                "yurtzeit",
                _yurtzeit_schema(self._lang, True, False, DEFAULT_YURTZEIT_DATABASES),
            )

        # Validation: if either toggle is on, must choose >=1 DB
        enable_daily = user_input.get(CONF_ENABLE_YURTZEIT_DAILY, False)
        enable_weekly = user_input.get(CONF_ENABLE_WEEKLY_YURTZEIT, False)
        dbs = user_input.get(CONF_YURTZEIT_DATABASES, [])

        if (enable_daily or enable_weekly) and not dbs:
            return self._form(
                "yurtzeit",
                _yurtzeit_schema(
                    self._lang, enable_daily, enable_weekly, dbs or DEFAULT_YURTZEIT_DATABASES
                ),
                errors={"base": "select_db_required"},
            )

        # Merge and create entry
        data = {
            **getattr(self, "_general_data", {}),
            **getattr(self, "_zmanim_data", {}),
            **getattr(self, "_calendars_data", {}),
            CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
            CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
            CONF_YURTZEIT_DATABASES: dbs,
            CONF_UI_LANGUAGE: self._lang,
        }
        return self.async_create_entry(title="YidCal", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(_LangFlowMixin, config_entries.OptionsFlow):
    """Options flow: Language / General / Yurtzeit / Early Entry."""

    _ns = "options"

    def __init__(self, config_entry):
        self._config_entry = config_entry

    # -- helpers ---------------------------------------------------------
    def _get(self, key, default):
        opts = self._config_entry.options or {}
        data = self._config_entry.data or {}
        return opts.get(key, data.get(key, default))

    def _resolve_lang(self):
        """Stored language, else a guess from the HA language."""
        self._lang = self._get(CONF_UI_LANGUAGE, None) or S.guess_ui_language(self.hass)
        return self._lang

    def _save(self, new_opts):
        # Force-set, never setdefault: new_opts is built from the existing
        # options, which already carry the OLD ui_language — setdefault would
        # be a no-op and silently discard the user's new choice.
        new_opts[CONF_UI_LANGUAGE] = self._lang
        return self.async_create_entry(title="", data=new_opts)

    # -- main menu -------------------------------------------------------
    async def async_step_init(self, user_input=None):
        self._resolve_lang()
        menu = S.menu_labels(
            {
                "general": "menu_general",
                "zmanim": "menu_zmanim",
                "calendars": "menu_calendars",
                "yurtzeit": "menu_yurtzeit",
                "early_shabbos_yt": "menu_early",
            },
            self._lang,
        )
        # Language row shows the language it will switch away from, so it
        # is recognisable even to someone who cannot read the current one.
        menu["language"] = f"{S.menu_language(self._lang)} · {S.LANGUAGE_NAMES[self._lang]}"
        return self._menu("init", menu)

    # -- language --------------------------------------------------------
    async def async_step_language(self, user_input=None):
        self._resolve_lang()
        return self._language_menu("language", "set_lang_", self._lang)

    # -- general ---------------------------------------------------------
    async def async_step_general(self, user_input=None):
        self._resolve_lang()
        if user_input is None:
            return self._form(
                "general",
                _general_schema(self._lang, self._get, include_luach_pdf=True),
            )
        return self._save({**(self._config_entry.options or {}), **user_input})

    # -- zmanim / alos ---------------------------------------------------
    async def async_step_zmanim(self, user_input=None):
        self._resolve_lang()
        if user_input is None:
            return self._form("zmanim", _zmanim_schema(self._lang, self._get))
        return self._save({**(self._config_entry.options or {}), **user_input})

    # -- calendars -------------------------------------------------------
    async def async_step_calendars(self, user_input=None):
        self._resolve_lang()
        if user_input is None:
            return self._form("calendars", _calendars_schema(self._lang, self._get))
        return self._save({**(self._config_entry.options or {}), **user_input})

    # -- yurtzeit --------------------------------------------------------
    async def async_step_yurtzeit(self, user_input=None):
        self._resolve_lang()
        legacy_db = self._get(CONF_YAHRTZEIT_DATABASE, DEFAULT_YAHRTZEIT_DATABASE)
        default_dbs = self._get(
            CONF_YURTZEIT_DATABASES,
            [legacy_db] if legacy_db else DEFAULT_YURTZEIT_DATABASES,
        )
        default_daily = self._get(CONF_ENABLE_YURTZEIT_DAILY, True)
        default_weekly = self._get(CONF_ENABLE_WEEKLY_YURTZEIT, False)

        if user_input is None:
            return self._form(
                "yurtzeit",
                _yurtzeit_schema(self._lang, default_daily, default_weekly, default_dbs),
            )

        enable_daily = user_input.get(CONF_ENABLE_YURTZEIT_DAILY, False)
        enable_weekly = user_input.get(CONF_ENABLE_WEEKLY_YURTZEIT, False)
        dbs = user_input.get(CONF_YURTZEIT_DATABASES, [])

        if (enable_daily or enable_weekly) and not dbs:
            return self._form(
                "yurtzeit",
                _yurtzeit_schema(
                    self._lang, enable_daily, enable_weekly, dbs or DEFAULT_YURTZEIT_DATABASES
                ),
                errors={"base": "select_db_required"},
            )

        new_opts = {**(self._config_entry.options or {})}
        new_opts[CONF_ENABLE_YURTZEIT_DAILY] = enable_daily
        new_opts[CONF_ENABLE_WEEKLY_YURTZEIT] = enable_weekly
        new_opts[CONF_YURTZEIT_DATABASES] = dbs
        return self._save(new_opts)

    # -- early entry -----------------------------------------------------
    async def async_step_early_shabbos_yt(self, user_input=None):
        self._resolve_lang()
        return self._menu("early_shabbos_yt", S.menu_labels(
            {"early_shabbos": "menu_early_shabbos", "early_yomtov": "menu_early_yomtov"},
            self._lang,
        ))

    async def async_step_early_shabbos(self, user_input=None):
        self._resolve_lang()
        if user_input is None:
            return self._form("early_shabbos", _early_shabbos_schema(self._lang, self._get))
        return self._save({**(self._config_entry.options or {}), **user_input})

    async def async_step_early_yomtov(self, user_input=None):
        self._resolve_lang()
        if user_input is None:
            return self._form("early_yomtov", _early_yomtov_schema(self._lang, self._get))
        return self._save({**(self._config_entry.options or {}), **user_input})


# ---------------------------------------------------------------------------
# Language picker step handlers.
#
# A dict menu routes to async_step_<key>, so every language needs a real
# method. They are generated from S.UI_LANGUAGES so that adding a language
# means touching ui_strings.py only.
# ---------------------------------------------------------------------------
def _make_config_lang_step(lang):
    async def _step(self, user_input=None):
        self._lang = lang
        return await self.async_step_general()

    _step.__name__ = f"async_step_lang_{lang}"
    return _step


def _make_options_lang_step(lang):
    async def _step(self, user_input=None):
        # Save and close, exactly like every other options sub-step.
        #
        # Do NOT call async_update_entry() here and then keep the flow open.
        # That writes the entry while the flow is still alive and expected to
        # serve further steps, so the update listener's reload (191 entities)
        # runs *concurrently* with the flow — and a second language switch in
        # the same dialog session raced it, closing the options dialog with an
        # empty "Error" alert (frontend does `text: err?.body?.message`, which
        # is undefined when the request never completes; nothing is logged
        # server-side).
        #
        # Ending the flow hands the write to OptionsFlowManager.async_finish_flow
        # instead, which is the path every HA integration uses: the flow is torn
        # down and no further step is expected, so the reload cannot overlap it.
        self._lang = lang
        return self._save({**(self._config_entry.options or {})})

    _step.__name__ = f"async_step_set_lang_{lang}"
    return _step


for _l in S.UI_LANGUAGES:
    setattr(YidCalConfigFlow, f"async_step_lang_{_l}", _make_config_lang_step(_l))
    setattr(OptionsFlowHandler, f"async_step_set_lang_{_l}", _make_options_lang_step(_l))
del _l
