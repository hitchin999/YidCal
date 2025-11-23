from __future__ import annotations

import logging
import os
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers.event import async_call_later
from timezonefinder import TimezoneFinder

from .const import DOMAIN
from .config_flow import (
    # General / existing
    CONF_INCLUDE_ATTR_SENSORS,
    CONF_INCLUDE_DATE,
    CONF_ENABLE_WEEKLY_YURTZEIT,  # (keep existing key name & behavior)
    CONF_SLICHOS_LABEL_ROLLOVER,
    CONF_UPCOMING_LOOKAHEAD_DAYS,
    DEFAULT_UPCOMING_LOOKAHEAD_DAYS,
    CONF_IS_IN_ISRAEL,
    DEFAULT_IS_IN_ISRAEL,
    # NEW Yurtzeit
    CONF_ENABLE_YURTZEIT_DAILY,
    CONF_YURTZEIT_DATABASES,
    # Legacy single-select (fallback only)
    CONF_YAHRTZEIT_DATABASE,
    CONF_TIME_FORMAT,
    DEFAULT_TIME_FORMAT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72
DEFAULT_TALLIS_TEFILIN_OFFSET = 22
DEFAULT_DAY_LABEL_LANGUAGE = "yiddish"

# Legacy default for migration fallback
DEFAULT_YAHRTZEIT_DATABASE = "standard"
DEFAULT_SLICHOS_LABEL_ROLLOVER = "havdalah"


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


async def resolve_location_from_coordinates(hass, latitude, longitude):
    """Reverse lookup borough, then forward-geocode that place to snap to its centroid."""

    # Hard-code Monroe NY
    if 41.2 <= latitude <= 41.45 and -74.3 <= longitude <= -74.0:
        return "Kiryas Joel", "NY", 41.34202, -74.1762, hass.config.time_zone

    # Hard-code Monsey NY
    if 41.05 <= latitude <= 41.17 and -74.15 <= longitude <= -73.99:
        return "Monsey", "NY", 41.11121, -74.06848, hass.config.time_zone

    try:
        # 1) Reverse-lookup borough
        def blocking_lookup():
            from geopy.geocoders import Nominatim

            geolocator = Nominatim(user_agent="yidcal")
            loc = geolocator.reverse((latitude, longitude), language="en", timeout=10)
            addr = loc.raw.get("address", {}) if loc else {}
            city = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("hamlet")
                or ""
            )
            borough = (
                addr.get("city_district")
                or addr.get("borough")
                or addr.get("suburb")
                or addr.get("neighbourhood")
            )
            if city == "New York" and borough:
                city = borough
            state = addr.get("state", "")
            return city, state

        city, state = await hass.async_add_executor_job(blocking_lookup)

        # 2) Forward-geocode only "City, State" to get the official centroid
        def blocking_forward():
            from geopy.geocoders import Nominatim

            geolocator = Nominatim(user_agent="yidcal")
            query = f"{city}, {state}"
            loc = geolocator.geocode(query, exactly_one=True, timeout=10)
            if not loc:
                raise ValueError(f"Could not geocode '{query}'")
            return loc.latitude, loc.longitude

        lat, lon = await hass.async_add_executor_job(blocking_forward)
    except Exception as e:
        _LOGGER.warning("Geocoding failed (%s), falling back to HA lat/lon", e)
        city, state = "", ""
        lat, lon = latitude, longitude

    # 3) Timezone lookup
    def get_tzname(lat, lon):
        return TimezoneFinder().timezone_at(lng=lon, lat=lat) or "UTC"

    try:
        tzname = await hass.async_add_executor_job(get_tzname, lat, lon)
    except Exception:
        _LOGGER.warning("Timezone lookup failed, falling back to HA time_zone")
        tzname = hass.config.time_zone

    return city, state, lat, lon, tzname or "UTC"


# ───────────────────────────────────────────────────────────────────────────────
# Home Assistant integration lifecycle
# ───────────────────────────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YidCal from a config entry."""
    # Create sample files before anything else
    await create_sample_files(hass)

    entry.add_update_listener(_async_update_options)

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
    include_attrs = opts.get(
        CONF_INCLUDE_ATTR_SENSORS,
        initial.get(CONF_INCLUDE_ATTR_SENSORS, True),
    )
    include_date = opts.get(
        CONF_INCLUDE_DATE,
        initial.get(CONF_INCLUDE_DATE, False),
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

    # NEW: daily toggle + multi-DB list with legacy fallback
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
    upcoming_lookahead = opts.get(
        CONF_UPCOMING_LOOKAHEAD_DAYS,
        initial.get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS),
    )

    # Resolve and store geo+tz config
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    city, state, lat, lon, tzname = await resolve_location_from_coordinates(
        hass, latitude, longitude
    )

    # Store per-entry options
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_IS_IN_ISRAEL: is_in_israel,
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havdala,
        "tallis_tefilin_offset": tallis,
        "day_label_language": day_label,
        CONF_INCLUDE_ATTR_SENSORS: include_attrs,
        CONF_INCLUDE_DATE: include_date,
        # Yurtzeit new fields
        CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
        CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
        CONF_YURTZEIT_DATABASES: databases,
        # Misc
        CONF_SLICHOS_LABEL_ROLLOVER: slichos_label_rollover,
        CONF_UPCOMING_LOOKAHEAD_DAYS: upcoming_lookahead,
        CONF_TIME_FORMAT: time_format,
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
        "havdalah_offset": havdala,
        # Yurtzeit new fields (so sensors can read them directly if desired)
        CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
        CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
        CONF_YURTZEIT_DATABASES: databases,
        CONF_SLICHOS_LABEL_ROLLOVER: slichos_label_rollover,
        CONF_TIME_FORMAT: time_format,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the user hits Submit on the Options page."""
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
    include_attrs = opts.get(
        CONF_INCLUDE_ATTR_SENSORS,
        initial.get(CONF_INCLUDE_ATTR_SENSORS, True),
    )
    include_date = opts.get(
        CONF_INCLUDE_DATE,
        initial.get(CONF_INCLUDE_DATE, False),
    )
    enable_weekly = opts.get(
        CONF_ENABLE_WEEKLY_YURTZEIT,
        initial.get(CONF_ENABLE_WEEKLY_YURTZEIT, False),
    )
    is_in_israel = opts.get(
        CONF_IS_IN_ISRAEL,
        initial.get(CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL),
    )

    # NEW: daily toggle + multi-DB list with legacy fallback
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
    upcoming_lookahead = opts.get(
        CONF_UPCOMING_LOOKAHEAD_DAYS,
        initial.get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_IS_IN_ISRAEL: is_in_israel,
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havdala,
        "tallis_tefilin_offset": tallis,
        "day_label_language": day_label,
        CONF_INCLUDE_ATTR_SENSORS: include_attrs,
        CONF_INCLUDE_DATE: include_date,
        # Yurtzeit new fields
        CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
        CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
        CONF_YURTZEIT_DATABASES: databases,
        # Misc
        CONF_SLICHOS_LABEL_ROLLOVER: slichos_label_rollover,
        CONF_UPCOMING_LOOKAHEAD_DAYS: upcoming_lookahead,
        CONF_TIME_FORMAT: time_format,
    }

    # Schedule the reload shortly after to apply new options
    async_call_later(
        hass,
        1,
        lambda now: _delayed_reload(hass, entry.entry_id),
    )

def _delayed_reload(hass: HomeAssistant, entry_id: str) -> None:
    """Helper for async_call_later: switch back to the event loop and reload."""
    _LOGGER.debug("YidCal: scheduling reload of entry %s", entry_id)
    hass.loop.call_soon_threadsafe(
        lambda: hass.async_create_task(hass.config_entries.async_reload(entry_id))
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
