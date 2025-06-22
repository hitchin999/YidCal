# /config/custom_components/yidcal/__init__.py
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers.event import async_call_later
import logging

from timezonefinder import TimezoneFinder

from .const import DOMAIN
from .config_flow import CONF_INCLUDE_ATTR_SENSORS

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72


async def resolve_location_from_coordinates(hass, latitude, longitude):
    """Reverse lookup borough, then forward-geocode that place to snap to its centroid."""

    def blocking_lookup():
        from geopy.geocoders import Nominatim

        geolocator = Nominatim(user_agent="yidcal")
        loc = geolocator.reverse((latitude, longitude), language="en", timeout=10)
        address = loc.raw.get("address", {}) if loc else {}

        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("hamlet")
            or ""
        )
        borough = (
            address.get("city_district")
            or address.get("borough")
            or address.get("suburb")
            or address.get("neighbourhood")
        )
        if city == "New York" and borough:
            city = borough

        state = address.get("state", "")
        postcode = address.get("postcode", "")
        return city, state, postcode

    city, state, postcode = await hass.async_add_executor_job(blocking_lookup)

    query = f"{city}, {state}".strip(", ")

    def blocking_forward():
        from geopy.geocoders import Nominatim

        geolocator = Nominatim(user_agent="yidcal")
        loc = geolocator.geocode(query, exactly_one=True, timeout=10)
        if not loc:
            raise ValueError(f"Could not geocode '{query}'")
        return loc.latitude, loc.longitude

    lat, lon = await hass.async_add_executor_job(blocking_forward)

    def get_tzname(lat, lon):
        return TimezoneFinder().timezone_at(lng=lon, lat=lat) or "UTC"

    tzname = await hass.async_add_executor_job(get_tzname, lat, lon)
    return city, state, lat, lon, tzname or "UTC"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YidCal from a config entry."""
    entry.add_update_listener(_async_update_options)

    initial = entry.data or {}
    opts = entry.options or {}

    strip   = opts.get("strip_nikud", initial.get("strip_nikud", False))
    candle  = opts.get(
        "candlelighting_offset",
        initial.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
    )
    havdala = opts.get(
        "havdalah_offset",
        initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET),
    )
    include_attrs = opts.get(
        CONF_INCLUDE_ATTR_SENSORS,
        initial.get(CONF_INCLUDE_ATTR_SENSORS, True),
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
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havdala,
        CONF_INCLUDE_ATTR_SENSORS: include_attrs,
    }
    # Store global config for sensors
    hass.data[DOMAIN]["config"] = {
        "candle": candle,
        "havdala": havdala,
        "diaspora": True,
        "strip_nikud": strip,
        "latitude": lat,
        "longitude": lon,
        "tzname": tzname,
        "city": f"{city}, {state}".strip(", "),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the user hits Submit on the Options page."""
    # Re-parse options and update hass.data for this entry
    initial = entry.data or {}
    opts = entry.options or {}

    strip   = opts.get("strip_nikud", initial.get("strip_nikud", False))
    candle  = opts.get(
        "candlelighting_offset",
        initial.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
    )
    havdala = opts.get(
        "havdalah_offset",
        initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET),
    )
    include_attrs = opts.get(
        CONF_INCLUDE_ATTR_SENSORS,
        initial.get(CONF_INCLUDE_ATTR_SENSORS, True),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havdala,
        CONF_INCLUDE_ATTR_SENSORS: include_attrs,
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
