"""
custom_components/yidcal/__init__.py

YidCal integration setup: config-entry lifecycle (setup / options-update /
unload), sample-file creation, home-location resolution and caching, and
service registration.
"""
from __future__ import annotations

import logging
import os
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

# Importing yidcal_lib applies a one-time monkey-patch to python-zmanim so
# that every ZmanimCalendar(...) instantiated anywhere in YidCal uses
# GrossmanCalculator (the algorithm published by R' Yissocher Dov Grossmann
# in קונטרס קו לקו, used by the Kiryas Joel luach publisher) as the default
# astronomical calculator instead of NOAACalculator. The import is placed
# here, BEFORE any HA helpers or YidCal modules, so the patch is active
# before any sensor platform is loaded. See yidcal_lib/__init__.py for
# details. Do not remove or reorder this import.
from . import yidcal_lib  # noqa: F401

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.exceptions import ServiceValidationError
import homeassistant.helpers.config_validation as cv
from timezonefinder import TimezoneFinder

from .const import DOMAIN
from .config_flow import (
    # General / existing
    CONF_INCLUDE_ATTR_SENSORS,
    CONF_INCLUDE_DATE,
    CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL,
    DEFAULT_INCLUDE_SEFIRAH_SHORT_IN_FULL,
    CONF_ENABLE_WEEKLY_YURTZEIT,  # (keep existing key name & behavior)
    CONF_SLICHOS_LABEL_ROLLOVER,
    CONF_KIDDUSH_LEVANA_START,
    DEFAULT_KIDDUSH_LEVANA_START,
    CONF_UPCOMING_LOOKAHEAD_DAYS,
    DEFAULT_UPCOMING_LOOKAHEAD_DAYS,
    CONF_IS_IN_ISRAEL,
    DEFAULT_IS_IN_ISRAEL,
    # Haftorah Minhag (NEW)
    CONF_HAFTORAH_MINHAG,
    DEFAULT_HAFTORAH_MINHAG,
    # Parsha Metzora display (NEW)
    CONF_PARSHA_METZORA_DISPLAY,
    DEFAULT_PARSHA_METZORA_DISPLAY,
    # NEW Yurtzeit
    CONF_ENABLE_YURTZEIT_DAILY,
    CONF_YURTZEIT_DATABASES,
    # Legacy single-select (fallback only)
    CONF_YAHRTZEIT_DATABASE,
    CONF_TIME_FORMAT,
    DEFAULT_TIME_FORMAT,
    # Early Entry (NEW)
    CONF_ENABLE_EARLY_SHABBOS,
    CONF_EARLY_SHABBOS_MODE,
    CONF_EARLY_SHABBOS_PLAG_METHOD,
    CONF_EARLY_SHABBOS_FIXED_TIME,
    CONF_EARLY_SHABBOS_APPLY_RULE,
    CONF_EARLY_SHABBOS_SUNSET_AFTER,

    CONF_ENABLE_EARLY_YOMTOV,
    CONF_EARLY_YOMTOV_MODE,
    CONF_EARLY_YOMTOV_PLAG_METHOD,
    CONF_EARLY_YOMTOV_FIXED_TIME,
    CONF_EARLY_YOMTOV_INCLUDE,
    CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS,

    DEFAULT_ENABLE_EARLY_SHABBOS,
    DEFAULT_EARLY_SHABBOS_MODE,
    DEFAULT_EARLY_SHABBOS_PLAG_METHOD,
    DEFAULT_EARLY_SHABBOS_FIXED_TIME,
    DEFAULT_EARLY_SHABBOS_APPLY_RULE,
    DEFAULT_EARLY_SHABBOS_SUNSET_AFTER,

    DEFAULT_ENABLE_EARLY_YOMTOV,
    DEFAULT_EARLY_YOMTOV_MODE,
    DEFAULT_EARLY_YOMTOV_PLAG_METHOD,
    DEFAULT_EARLY_YOMTOV_FIXED_TIME,
    DEFAULT_EARLY_YOMTOV_INCLUDE,
    DEFAULT_EARLY_YOMTOV_ALLOW_SECOND_DAYS,
    
    CONF_KORBANOS_YUD_GIMMEL_MIDOS,
    DEFAULT_KORBANOS_YUD_GIMMEL_MIDOS,
    CONF_MISHNE_TORAH_HOSHANA_RABBA,
    DEFAULT_MISHNE_TORAH_HOSHANA_RABBA,

    CONF_ENABLE_DAF_HAYOMI,
    DEFAULT_ENABLE_DAF_HAYOMI,

    CONF_ENABLE_MULTIDAY_CANDLES,
    DEFAULT_ENABLE_MULTIDAY_CANDLES,

    CONF_ENABLE_ZMANIM_LOOKUP,
    DEFAULT_ENABLE_ZMANIM_LOOKUP,

    CONF_ENABLE_LUACH_PDF,
    DEFAULT_ENABLE_LUACH_PDF,

)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT, Platform.TIME]

DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72
DEFAULT_TALLIS_TEFILIN_OFFSET = 22
DEFAULT_DAY_LABEL_LANGUAGE = "yiddish"

# Legacy default for migration fallback
DEFAULT_YAHRTZEIT_DATABASE = "standard"
DEFAULT_SLICHOS_LABEL_ROLLOVER = "havdalah"

# ─── Yurtzeit list-management services ───────────────────────────────
# Used by the yidcal-yurtzeit-config-card to mute / unmute yurtzeits and
# add custom ones. Both services rewrite the corresponding text file
# under www/yidcal-data/ (legacy filenames preserved for back-compat)
# and fire the ``yidcal_yurtzeit_data_changed`` event so live yurtzeit
# sensors can reload immediately without a full integration reload.
SERVICE_SET_YURTZEIT_MUTED = "set_yurtzeit_muted"
SERVICE_SET_YURTZEIT_CUSTOM = "set_yurtzeit_custom"
EVENT_YURTZEIT_DATA_CHANGED = "yidcal_yurtzeit_data_changed"

_SET_YURTZEIT_MUTED_SCHEMA = vol.Schema({
    vol.Required("names"): [cv.string],
})

_SET_YURTZEIT_CUSTOM_SCHEMA = vol.Schema({
    vol.Required("entries"): [vol.Schema({
        vol.Required("date"): cv.string,   # Hebrew date string e.g. "ט\"ו תמוז"
        vol.Required("name"): cv.string,
    })],
})


def _async_register_yurtzeit_services(hass: HomeAssistant) -> None:
    """Register the yidcal.set_yurtzeit_muted / set_yurtzeit_custom services.

    Both services persist their list to the corresponding text file in
    ``www/yidcal-data/`` and fire ``yidcal_yurtzeit_data_changed`` so the
    daily/weekly Yurtzeit sensors can refresh instantly.
    """
    if (
        hass.services.has_service(DOMAIN, SERVICE_SET_YURTZEIT_MUTED)
        and hass.services.has_service(DOMAIN, SERVICE_SET_YURTZEIT_CUSTOM)
    ):
        return

    yidcal_dir = Path(hass.config.path("www/yidcal-data"))

    def _ensure_dir() -> None:
        if not yidcal_dir.exists():
            yidcal_dir.mkdir(mode=0o755, parents=True, exist_ok=True)

    async def _handle_set_muted(call: ServiceCall) -> None:
        names = call.data.get("names") or []
        # Normalize: strip + drop blanks, preserve order, keep duplicates out.
        seen: set[str] = set()
        clean: list[str] = []
        for raw in names:
            line = str(raw).strip()
            if not line or line in seen:
                continue
            seen.add(line)
            clean.append(line)

        header = (
            "# YidCal Muted Yurtzeits\n"
            "# Managed by the yidcal-yurtzeit-config-card.\n"
            "# Lines starting with # are comments and ignored.\n\n"
        )
        body = header + "\n".join(clean) + ("\n" if clean else "")

        def _write() -> None:
            _ensure_dir()
            (yidcal_dir / "muted_yahrtzeits.txt").write_text(body, encoding="utf-8")

        await hass.async_add_executor_job(_write)
        hass.bus.async_fire(EVENT_YURTZEIT_DATA_CHANGED, {"kind": "muted"})
        _LOGGER.debug("YidCal: wrote %d muted yurtzeit entries", len(clean))

    async def _handle_set_custom(call: ServiceCall) -> None:
        entries = call.data.get("entries") or []
        lines: list[str] = []
        for e in entries:
            d = str(e.get("date", "")).strip()
            n = str(e.get("name", "")).strip()
            if not d or not n:
                continue
            # Re-normalize separators (one ":" max)
            n = n.replace("\n", " ").replace("\r", " ")
            lines.append(f"{d}: {n}")

        header = (
            "# YidCal Custom Yurtzeits\n"
            "# Managed by the yidcal-yurtzeit-config-card.\n"
            "# Format: <Hebrew date>: <Name>\n\n"
        )
        body = header + "\n".join(lines) + ("\n" if lines else "")

        def _write() -> None:
            _ensure_dir()
            (yidcal_dir / "custom_yahrtzeits.txt").write_text(body, encoding="utf-8")

        await hass.async_add_executor_job(_write)
        hass.bus.async_fire(EVENT_YURTZEIT_DATA_CHANGED, {"kind": "custom"})
        _LOGGER.debug("YidCal: wrote %d custom yurtzeit entries", len(lines))

    hass.services.async_register(
        DOMAIN, SERVICE_SET_YURTZEIT_MUTED,
        _handle_set_muted, schema=_SET_YURTZEIT_MUTED_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_YURTZEIT_CUSTOM,
        _handle_set_custom, schema=_SET_YURTZEIT_CUSTOM_SCHEMA,
    )
    _LOGGER.debug(
        "YidCal: registered services %s.%s and %s.%s",
        DOMAIN, SERVICE_SET_YURTZEIT_MUTED,
        DOMAIN, SERVICE_SET_YURTZEIT_CUSTOM,
    )


# ─── Zmanim Lookup service ────────────────────────────────────────────
SERVICE_CHECK_ZMANIM = "check_zmanim"
# ±100 years from the current year. Solar zmanim and pyluach both handle
# far greater spans, so 100 years is a conservative safety cap.
_ZMANIM_LOOKUP_MAX_YEARS = 100
# Max number of dates accepted per service call (one required + N optional).
# Bumped from 5 → 10 so the card can do "next Yom Tov" (1–2 days) and
# "this week" (Sun→Sun, 8 days) in a single call.
_ZMANIM_LOOKUP_MAX_DATES = 10

_CHECK_ZMANIM_SCHEMA = vol.Schema({
    vol.Required("date"): cv.date,
    vol.Optional("date_2"):  cv.date,
    vol.Optional("date_3"):  cv.date,
    vol.Optional("date_4"):  cv.date,
    vol.Optional("date_5"):  cv.date,
    vol.Optional("date_6"):  cv.date,
    vol.Optional("date_7"):  cv.date,
    vol.Optional("date_8"):  cv.date,
    vol.Optional("date_9"):  cv.date,
    vol.Optional("date_10"): cv.date,
    vol.Optional("location"): cv.string,
})


def _async_register_check_zmanim_service(hass: HomeAssistant) -> None:
    """Register the ``yidcal.check_zmanim`` service.

    Accepts 1–10 dates (``date`` required; ``date_2`` through ``date_10``
    optional) plus an optional ``location`` (free-form string — ZIP,
    city, landmark — geocoded via Nominatim). Writes the combined result
    to ``sensor.yidcal_zmanim_lookup``. Safe to call multiple times —
    returns immediately if already registered.
    """
    if hass.services.has_service(DOMAIN, SERVICE_CHECK_ZMANIM):
        return

    def _coerce_date(raw) -> date_cls:
        if isinstance(raw, datetime):
            return raw.date()
        if isinstance(raw, date_cls):
            return raw
        try:
            return date_cls.fromisoformat(str(raw))
        except Exception as exc:
            raise ServiceValidationError(
                f"Invalid date: {raw!r}. Expected ISO format (YYYY-MM-DD)."
            ) from exc

    async def _handle_check_zmanim(call: ServiceCall) -> None:
        # Collect the dates in order (primary first, then _2 .. _10).
        dates: list[date_cls] = []
        date_keys = ["date"] + [f"date_{i}" for i in range(2, _ZMANIM_LOOKUP_MAX_DATES + 1)]
        for key in date_keys:
            raw = call.data.get(key)
            if raw is None:
                continue
            dates.append(_coerce_date(raw))

        if not dates:
            raise ServiceValidationError("At least one date must be provided.")

        today = datetime.now().date()
        for target in dates:
            if abs(target.year - today.year) > _ZMANIM_LOOKUP_MAX_YEARS:
                raise ServiceValidationError(
                    f"Date {target.isoformat()} is outside the supported "
                    f"range of ±{_ZMANIM_LOOKUP_MAX_YEARS} years from today."
                )

        # Optional override location — geocoded via Nominatim if provided.
        # When omitted, the sensor uses its configured (HA-resolved)
        # location with no API call.
        resolved = None
        loc_raw = call.data.get("location")
        if loc_raw and str(loc_raw).strip():
            from .yidcal_lib.zman_geocoder import resolve_location
            resolved = await resolve_location(hass, str(loc_raw))

        # Late import to avoid circular-import risk at module load.
        from .zmanim_lookup_sensor import SENSOR_REF_KEY
        sensor = hass.data.get(DOMAIN, {}).get(SENSOR_REF_KEY)
        if sensor is None:
            raise ServiceValidationError(
                "sensor.yidcal_zmanim_lookup is not available. "
                "Enable 'Zmanim Lookup' in the YidCal integration options."
            )
        await sensor.async_lookup_dates(dates, resolved=resolved)

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_ZMANIM,
        _handle_check_zmanim,
        schema=_CHECK_ZMANIM_SCHEMA,
    )
    _LOGGER.debug("YidCal: registered service %s.%s", DOMAIN, SERVICE_CHECK_ZMANIM)


# ───────────────────────────────────────────────────────────────────────────────
# Sample files for Yurtzeit customization (keep filenames for back-compat)
# ───────────────────────────────────────────────────────────────────────────────

# Sample content for custom yahrtzeits file
CUSTOM_YAHRTZEITS_SAMPLE = """# YidCal Custom Yurtzeits
# Format: Date: Name
#
# Remove the # from lines you want to use
#
# דוגמאות / Examples:
#ט"ו תמוז: רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א
#י"ז תמוז: רבי דוגמא בן רבי משל זי"ע תרצ"ב
#ב' אלול: רבי ישראל בן רבי אברהם זי"ע [בעל תוספות דוגמא] תק"ס
#כ"ה כסלו: רבי יעקב בן רבי יצחק הלוי זי"ע תרפ"ה
#ג' ניסן: רבי משה בן רבי יוסף זי"ע [מחבר שו"ת דוגמא] תרל"ג
"""

# Sample content for muted yahrtzeits file
MUTED_YAHRTZEITS_SAMPLE = """# YidCal Muted Yurtzeits
# List of Yurtzeits to hide
#
# Enter exact name as it appears in the Yurtzeit list
#
# דוגמאות / Examples:
#רבי פלוני בן רבי אלמוני זי"ע [מחבר ספר דוגמא] תש"א ומנו"כ בהה"ז
#רבי ישראל בן רבי אברהם זי"ע [בעל תוספות דוגמא] תק"ס ומנו"כ במץ
"""


async def create_sample_files(hass: HomeAssistant) -> None:
    """Create sample Yurtzeit files if they don't exist (keeps legacy filenames)."""
    yidcal_dir = Path(hass.config.path("www/yidcal-data"))

    # Create directory if it doesn't exist
    if not yidcal_dir.exists():
        await hass.async_add_executor_job(yidcal_dir.mkdir, 0o755, True)
        _LOGGER.info("Created YidCal data directory at %s", yidcal_dir)

    # Create custom yahrtzeits sample file
    custom_file = yidcal_dir / "custom_yahrtzeits.txt"
    if not custom_file.exists():
        await hass.async_add_executor_job(
            custom_file.write_text,
            CUSTOM_YAHRTZEITS_SAMPLE,
            "utf-8",
        )
        _LOGGER.info("Created sample custom Yurtzeits file at %s", custom_file)

    # Create muted yahrtzeits sample file
    muted_file = yidcal_dir / "muted_yahrtzeits.txt"
    if not muted_file.exists():
        await hass.async_add_executor_job(
            muted_file.write_text,
            MUTED_YAHRTZEITS_SAMPLE,
            "utf-8",
        )
        _LOGGER.info("Created sample muted Yurtzeits file at %s", muted_file)


from .yidcal_lib.zman_geocoder import resolve_location_from_coordinates  # noqa: E402


# ───────────────────────────────────────────────────────────────────────────────
# Home Assistant integration lifecycle
# ───────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YidCal from a config entry."""
    # Create sample files before anything else
    await create_sample_files(hass)

    initial = entry.data or {}
    opts = entry.options or {}

    strip = opts.get("strip_nikud", initial.get("strip_nikud", False))
    candle = opts.get(
        "candlelighting_offset",
        initial.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
    )
    havdala = opts.get(
        "havdalah_offset",
        initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET),
    )
    tallis = opts.get(
        "tallis_tefilin_offset",
        initial.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET),
    )
    day_label = opts.get(
        "day_label_language",
        initial.get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE),
    )
    time_format = opts.get(
        CONF_TIME_FORMAT,
        initial.get(CONF_TIME_FORMAT, DEFAULT_TIME_FORMAT),
    )
    haftorah_minhag = opts.get(
        CONF_HAFTORAH_MINHAG,
        initial.get(CONF_HAFTORAH_MINHAG, DEFAULT_HAFTORAH_MINHAG),
    )
    parsha_metzora_display = opts.get(
        CONF_PARSHA_METZORA_DISPLAY,
        initial.get(CONF_PARSHA_METZORA_DISPLAY, DEFAULT_PARSHA_METZORA_DISPLAY),
    )
    include_attrs = opts.get(
        CONF_INCLUDE_ATTR_SENSORS,
        initial.get(CONF_INCLUDE_ATTR_SENSORS, True),
    )
    include_date = opts.get(
        CONF_INCLUDE_DATE,
        initial.get(CONF_INCLUDE_DATE, False),
    )
    include_sefirah_short_in_full = opts.get(
        CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL,
        initial.get(
            CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL,
            DEFAULT_INCLUDE_SEFIRAH_SHORT_IN_FULL,
        ),
    )
    enable_weekly = opts.get(
        CONF_ENABLE_WEEKLY_YURTZEIT,
        initial.get(CONF_ENABLE_WEEKLY_YURTZEIT, False),
    )
    is_in_israel = opts.get(
        CONF_IS_IN_ISRAEL,
        initial.get(CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL),
    )
    diaspora = not is_in_israel

    enable_daily = opts.get(
        CONF_ENABLE_YURTZEIT_DAILY,
        initial.get(CONF_ENABLE_YURTZEIT_DAILY, True),
    )
    databases = opts.get(
        CONF_YURTZEIT_DATABASES,
        initial.get(CONF_YURTZEIT_DATABASES, None),
    )
    if not databases:
        legacy_db = opts.get(
            CONF_YAHRTZEIT_DATABASE,
            initial.get(CONF_YAHRTZEIT_DATABASE, DEFAULT_YAHRTZEIT_DATABASE),
        )
        databases = [legacy_db] if legacy_db else [DEFAULT_YAHRTZEIT_DATABASE]

    slichos_label_rollover = opts.get(
        CONF_SLICHOS_LABEL_ROLLOVER,
        initial.get(CONF_SLICHOS_LABEL_ROLLOVER, DEFAULT_SLICHOS_LABEL_ROLLOVER),
    )
    kiddush_levana_start = opts.get(
        CONF_KIDDUSH_LEVANA_START,
        initial.get(CONF_KIDDUSH_LEVANA_START, DEFAULT_KIDDUSH_LEVANA_START),
    )
    upcoming_lookahead = opts.get(
        CONF_UPCOMING_LOOKAHEAD_DAYS,
        initial.get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS),
    )
    
    # ---------------- Early Entry options (NEW) ----------------
    enable_early_shabbos = opts.get(
        CONF_ENABLE_EARLY_SHABBOS,
        initial.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS),
    )
    early_shabbos_mode = opts.get(
        CONF_EARLY_SHABBOS_MODE,
        initial.get(CONF_EARLY_SHABBOS_MODE, DEFAULT_EARLY_SHABBOS_MODE),
    )
    early_shabbos_plag_method = opts.get(
        CONF_EARLY_SHABBOS_PLAG_METHOD,
        initial.get(CONF_EARLY_SHABBOS_PLAG_METHOD, DEFAULT_EARLY_SHABBOS_PLAG_METHOD),
    )
    early_shabbos_fixed_time = opts.get(
        CONF_EARLY_SHABBOS_FIXED_TIME,
        initial.get(CONF_EARLY_SHABBOS_FIXED_TIME, DEFAULT_EARLY_SHABBOS_FIXED_TIME),
    )
    early_shabbos_apply_rule = opts.get(
        CONF_EARLY_SHABBOS_APPLY_RULE,
        initial.get(CONF_EARLY_SHABBOS_APPLY_RULE, DEFAULT_EARLY_SHABBOS_APPLY_RULE),
    )
    early_shabbos_sunset_after = opts.get(
        CONF_EARLY_SHABBOS_SUNSET_AFTER,
        initial.get(CONF_EARLY_SHABBOS_SUNSET_AFTER, DEFAULT_EARLY_SHABBOS_SUNSET_AFTER),
    )

    enable_early_yomtov = opts.get(
        CONF_ENABLE_EARLY_YOMTOV,
        initial.get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV),
    )
    early_yomtov_mode = opts.get(
        CONF_EARLY_YOMTOV_MODE,
        initial.get(CONF_EARLY_YOMTOV_MODE, DEFAULT_EARLY_YOMTOV_MODE),
    )
    early_yomtov_plag_method = opts.get(
        CONF_EARLY_YOMTOV_PLAG_METHOD,
        initial.get(CONF_EARLY_YOMTOV_PLAG_METHOD, DEFAULT_EARLY_YOMTOV_PLAG_METHOD),
    )
    early_yomtov_fixed_time = opts.get(
        CONF_EARLY_YOMTOV_FIXED_TIME,
        initial.get(CONF_EARLY_YOMTOV_FIXED_TIME, DEFAULT_EARLY_YOMTOV_FIXED_TIME),
    )
    early_yomtov_include = opts.get(
        CONF_EARLY_YOMTOV_INCLUDE,
        initial.get(CONF_EARLY_YOMTOV_INCLUDE, DEFAULT_EARLY_YOMTOV_INCLUDE),
    )
    early_yomtov_allow_second_days = opts.get(
        CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS,
        initial.get(CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS, DEFAULT_EARLY_YOMTOV_ALLOW_SECOND_DAYS),
    )
    korbanos_yud_gimmel_midos = opts.get(
        CONF_KORBANOS_YUD_GIMMEL_MIDOS,
        initial.get(CONF_KORBANOS_YUD_GIMMEL_MIDOS, DEFAULT_KORBANOS_YUD_GIMMEL_MIDOS),
    )
    mishne_torah_hoshana_rabba = opts.get(
        CONF_MISHNE_TORAH_HOSHANA_RABBA,
        initial.get(CONF_MISHNE_TORAH_HOSHANA_RABBA, DEFAULT_MISHNE_TORAH_HOSHANA_RABBA),
    )

    enable_daf_hayomi = opts.get(
        CONF_ENABLE_DAF_HAYOMI,
        initial.get(CONF_ENABLE_DAF_HAYOMI, DEFAULT_ENABLE_DAF_HAYOMI),
    )

    enable_multiday_candles = opts.get(
        CONF_ENABLE_MULTIDAY_CANDLES,
        initial.get(CONF_ENABLE_MULTIDAY_CANDLES, DEFAULT_ENABLE_MULTIDAY_CANDLES),
    )

    enable_zmanim_lookup = opts.get(
        CONF_ENABLE_ZMANIM_LOOKUP,
        initial.get(CONF_ENABLE_ZMANIM_LOOKUP, DEFAULT_ENABLE_ZMANIM_LOOKUP),
    )

    enable_luach_pdf = opts.get(
        CONF_ENABLE_LUACH_PDF,
        initial.get(CONF_ENABLE_LUACH_PDF, DEFAULT_ENABLE_LUACH_PDF),
    )

    # Resolve and store geo+tz config (with caching to avoid repeated API calls)
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    
    # Check if we have cached location data AND if HA coordinates haven't changed
    cached = initial.get("resolved_location")
    # Invalidate any cached snap that uses the legacy Kiryas Joel
    # coordinates — those have been superseded by the publisher's
    # actual luach coords (41.341, -74.1679). Without this check,
    # existing installs would keep the old centroid forever even
    # after updating YidCal.
    if (
        cached
        and cached.get("city") == "Kiryas Joel"
        and cached.get("lat") == 41.34202
        and cached.get("lon") == -74.1762
    ):
        _LOGGER.info(
            "YidCal: Migrating cached Kiryas Joel snap to luach-aligned coords"
        )
        cached = None

    # Invalidate any cached snap that uses the legacy Monsey coordinates —
    # the curated community-centroid database (places.py) now supplies the
    # Monsey centroid as (41.12, -74.07), aligned with the published luach
    # source. Old installs cached the previous hand-set values.
    if (
        cached
        and cached.get("city") == "Monsey"
        and cached.get("lat") == 41.11121
        and cached.get("lon") == -74.06848
    ):
        _LOGGER.info(
            "YidCal: Migrating cached Monsey snap to luach-aligned coords"
        )
        cached = None

    if cached and cached.get("source_lat") == latitude and cached.get("source_lon") == longitude:
        # Use cached location
        city = cached["city"]
        state = cached["state"]
        lat = cached["lat"]
        lon = cached["lon"]
        tzname = cached["tzname"]
        _LOGGER.debug("YidCal: Using cached location: %s, %s", city, state)
    else:
        # First-time setup or coordinates changed: resolve and cache
        if cached:
            _LOGGER.info("YidCal: HA coordinates changed, re-resolving location")
        else:
            _LOGGER.info("YidCal: First-time setup, resolving location from coordinates")
        
        city, state, lat, lon, tzname = await resolve_location_from_coordinates(
            hass, latitude, longitude
        )

        if city:
            # Save to entry data for future loads
            hass.config_entries.async_update_entry(
                entry,
                data={
                    **initial,
                    "resolved_location": {
                        "city": city,
                        "state": state,
                        "lat": lat,
                        "lon": lon,
                        "tzname": tzname,
                        "source_lat": latitude,
                        "source_lon": longitude,
                    }
                }
            )
            _LOGGER.info("YidCal: Cached resolved location: %s, %s", city, state)
        else:
            # Resolution failed — most commonly Nominatim being unreachable
            # because HA came up before the network did. Run this session on
            # the raw HA coordinates but do NOT cache the failure: caching it
            # would freeze the empty result forever, since the
            # source_lat/source_lon check above would then treat it as a
            # valid snap on every future boot. Leaving the cache untouched
            # means the next reload or restart simply retries.
            _LOGGER.warning(
                "YidCal: location resolution failed; using raw HA coordinates "
                "(%.5f, %.5f) for this session and retrying on next "
                "reload/restart",
                latitude,
                longitude,
            )

    # Watch for Options saves. Two deliberate details here:
    #   1) Registered only AFTER the resolved-location cache write above, so
    #      the async_update_entry() call during first-time setup (or a
    #      coordinate migration) can't re-trigger the setup that is still
    #      running.
    #   2) Wrapped in entry.async_on_unload() so the listener is removed on
    #      every unload. Without this, each reload stacked one more copy of
    #      the listener, and since every copy schedules a full reload, each
    #      Options save doubled the number of back-to-back reloads for the
    #      rest of the HA session (1 → 2 → 4 → 8 …), which looked like
    #      YidCal re-initializing over and over until a host reboot.
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    # Store per-entry options
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_IS_IN_ISRAEL: is_in_israel,
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havdala,
        "tallis_tefilin_offset": tallis,
        "day_label_language": day_label,
        CONF_HAFTORAH_MINHAG: haftorah_minhag,
        CONF_PARSHA_METZORA_DISPLAY: parsha_metzora_display,
        CONF_INCLUDE_ATTR_SENSORS: include_attrs,
        CONF_INCLUDE_DATE: include_date,
        CONF_INCLUDE_SEFIRAH_SHORT_IN_FULL: include_sefirah_short_in_full,
        # Yurtzeit new fields
        CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
        CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
        CONF_YURTZEIT_DATABASES: databases,
        # Misc
        CONF_SLICHOS_LABEL_ROLLOVER: slichos_label_rollover,
        CONF_KIDDUSH_LEVANA_START: kiddush_levana_start,
        CONF_UPCOMING_LOOKAHEAD_DAYS: upcoming_lookahead,
        CONF_TIME_FORMAT: time_format,
        # Early Entry (NEW)
        CONF_ENABLE_EARLY_SHABBOS: enable_early_shabbos,
        CONF_EARLY_SHABBOS_MODE: early_shabbos_mode,
        CONF_EARLY_SHABBOS_PLAG_METHOD: early_shabbos_plag_method,
        CONF_EARLY_SHABBOS_FIXED_TIME: early_shabbos_fixed_time,
        CONF_EARLY_SHABBOS_APPLY_RULE: early_shabbos_apply_rule,
        CONF_EARLY_SHABBOS_SUNSET_AFTER: early_shabbos_sunset_after,

        CONF_ENABLE_EARLY_YOMTOV: enable_early_yomtov,
        CONF_EARLY_YOMTOV_MODE: early_yomtov_mode,
        CONF_EARLY_YOMTOV_PLAG_METHOD: early_yomtov_plag_method,
        CONF_EARLY_YOMTOV_FIXED_TIME: early_yomtov_fixed_time,
        CONF_EARLY_YOMTOV_INCLUDE: early_yomtov_include,
        CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS: early_yomtov_allow_second_days,

        CONF_KORBANOS_YUD_GIMMEL_MIDOS: korbanos_yud_gimmel_midos,
        CONF_MISHNE_TORAH_HOSHANA_RABBA: mishne_torah_hoshana_rabba,
        CONF_ENABLE_DAF_HAYOMI: enable_daf_hayomi,
        CONF_ENABLE_MULTIDAY_CANDLES: enable_multiday_candles,
        CONF_ENABLE_ZMANIM_LOOKUP: enable_zmanim_lookup,
    }

    # Store global config for sensors
    hass.data[DOMAIN]["config"] = {
        "candle": candle,
        "havdala": havdala,
        "diaspora": diaspora,
        "is_in_israel": is_in_israel,
        "strip_nikud": strip,
        "latitude": lat,
        "longitude": lon,
        "tzname": tzname,
        "city": f"{city}, {state}".strip(", "),
        "tallis_tefilin_offset": tallis,
        "day_label_language": day_label,
        "include_date": include_date,
        "include_sefirah_short_in_full": include_sefirah_short_in_full,
        "havdalah_offset": havdala,
        CONF_HAFTORAH_MINHAG: haftorah_minhag,
        CONF_PARSHA_METZORA_DISPLAY: parsha_metzora_display,
        # Yurtzeit new fields (so sensors can read them directly if desired)
        CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
        CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
        CONF_YURTZEIT_DATABASES: databases,
        CONF_SLICHOS_LABEL_ROLLOVER: slichos_label_rollover,
        CONF_KIDDUSH_LEVANA_START: kiddush_levana_start,
        CONF_TIME_FORMAT: time_format,
        # Early Entry (NEW)
        CONF_ENABLE_EARLY_SHABBOS: enable_early_shabbos,
        CONF_EARLY_SHABBOS_MODE: early_shabbos_mode,
        CONF_EARLY_SHABBOS_PLAG_METHOD: early_shabbos_plag_method,
        CONF_EARLY_SHABBOS_FIXED_TIME: early_shabbos_fixed_time,
        CONF_EARLY_SHABBOS_APPLY_RULE: early_shabbos_apply_rule,
        CONF_EARLY_SHABBOS_SUNSET_AFTER: early_shabbos_sunset_after,

        CONF_ENABLE_EARLY_YOMTOV: enable_early_yomtov,
        CONF_EARLY_YOMTOV_MODE: early_yomtov_mode,
        CONF_EARLY_YOMTOV_PLAG_METHOD: early_yomtov_plag_method,
        CONF_EARLY_YOMTOV_FIXED_TIME: early_yomtov_fixed_time,
        CONF_EARLY_YOMTOV_INCLUDE: early_yomtov_include,
        CONF_EARLY_YOMTOV_ALLOW_SECOND_DAYS: early_yomtov_allow_second_days,
        
        "korbanos_yud_gimmel_midos": korbanos_yud_gimmel_midos,
        "mishne_torah_hoshana_rabba": mishne_torah_hoshana_rabba,
        CONF_ENABLE_DAF_HAYOMI: enable_daf_hayomi,
        CONF_ENABLE_MULTIDAY_CANDLES: enable_multiday_candles,
        CONF_ENABLE_ZMANIM_LOOKUP: enable_zmanim_lookup,
    }

    # Register the yidcal.check_zmanim service if the Zmanim Lookup sensor
    # is enabled. Unregister if it was previously registered but has now
    # been turned off (on a reload).
    if enable_zmanim_lookup:
        _async_register_check_zmanim_service(hass)
    else:
        if hass.services.has_service(DOMAIN, SERVICE_CHECK_ZMANIM):
            hass.services.async_remove(DOMAIN, SERVICE_CHECK_ZMANIM)

    # Register the yidcal.generate_luach service if the Luach PDF
    # feature is enabled. Same on/off-on-reload semantics as check_zmanim.
    from .yidcal_lib.luach_service import (
        async_register_service as _async_register_luach_service,
        async_remove_service as _async_remove_luach_service,
    )
    if enable_luach_pdf:
        _async_register_luach_service(hass)
        # Keep the fixed-name yearly luach JSON current, refreshed on
        # Erev Rosh Hashanah for the incoming year (self-healing). Reuses
        # the generate_luach service in json_only mode.
        from .yidcal_lib.luach_auto_json import async_setup_erev_rh_json
        async_setup_erev_rh_json(hass)
    else:
        _async_remove_luach_service(hass)
        from .yidcal_lib.luach_auto_json import async_shutdown_erev_rh_json
        async_shutdown_erev_rh_json(hass)

    # Yurtzeit list-management services are always available — they
    # only touch www/yidcal-data/*.txt and don't depend on any optional
    # toggle. Used by the yidcal-yurtzeit-config-card.
    _async_register_yurtzeit_services(hass)

    # ── Zmanim single-source-of-truth coordinator ──────────────────
    # Computes the day's zmanim once per location; every zman sensor
    # subscribes instead of each building its own ZmanimCalendar.
    # Created and started BEFORE platform setup so entities can grab
    # it (via get_zmanim_coordinator) during their own async_setup.
    from .zmanim_coordinator import ZmanimCoordinator, COORDINATOR_KEY

    _zmanim_coord = ZmanimCoordinator(hass)
    hass.data[DOMAIN][COORDINATOR_KEY] = _zmanim_coord
    await _zmanim_coord.async_start()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)



    # Keep the ticking countdown sensors out of the recorder/logbook
    # without any user configuration — see quiet_recorder.py.
    from .fast_timer_sensors import SILENCED_ENTITY_IDS
    from .quiet_recorder import async_silence_entities
    entry.async_on_unload(async_silence_entities(hass, SILENCED_ENTITY_IDS))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the user hits Submit on the Options page.

    A reload re-runs async_setup_entry(), which re-reads entry.options and
    rebuilds hass.data[DOMAIN] from scratch — so nothing needs to be parsed
    or copied here. Reloading directly inside the update listener is the
    standard HA pattern; the old 1-second async_call_later +
    call_soon_threadsafe dance (a workaround for an options-flow reload race
    in much older HA cores) scheduled timers that were never cancelled on
    unload and is not needed on any core >= the 2023.7 minimum.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    # Remove the check_zmanim service on full unload so it doesn't
    # linger if the user removes the integration.
    if hass.services.has_service(DOMAIN, SERVICE_CHECK_ZMANIM):
        hass.services.async_remove(DOMAIN, SERVICE_CHECK_ZMANIM)
    # Remove the generate_luach service on full unload.
    from .yidcal_lib.luach_service import async_remove_service as _remove_luach
    _remove_luach(hass)
    # Stop the Erev-RH luach JSON auto-generator's timers.
    from .yidcal_lib.luach_auto_json import async_shutdown_erev_rh_json
    async_shutdown_erev_rh_json(hass)
    # Drop yurtzeit list services on full unload too.
    for svc in (SERVICE_SET_YURTZEIT_MUTED, SERVICE_SET_YURTZEIT_CUSTOM):
        if hass.services.has_service(DOMAIN, svc):
            hass.services.async_remove(DOMAIN, svc)

    # Stop the zmanim coordinator's scheduled-refresh timer so it
    # doesn't fire after unload/reload.
    from .zmanim_coordinator import COORDINATOR_KEY
    _zc = (hass.data.get(DOMAIN, {}) or {}).get(COORDINATOR_KEY)
    if _zc is not None:
        _zc.async_shutdown_timer()
        hass.data[DOMAIN].pop(COORDINATOR_KEY, None)

    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
