# /config/custom_components/yidcal/__init__.py
"""Yiddish Calendar integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]

# Default offsets (minutes)
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YidCal from a config entry."""
    # Listen for option updates
    entry.add_update_listener(_async_update_options)

    # Merge entry.data (initial install) with entry.options (subsequent edits)
    initial = entry.data or {}
    opts = entry.options or {}

    strip = opts.get("strip_nikud", initial.get("strip_nikud", False))
    candle = opts.get(
        "candlelighting_offset",
        initial.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
    )
    havd = opts.get(
        "havdalah_offset", initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET)
    )

    # Store the merged values for sensor use
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havd,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when config entry options are updated."""
    initial = entry.data or {}
    opts = entry.options or {}

    strip = opts.get("strip_nikud", initial.get("strip_nikud", False))
    candle = opts.get(
        "candlelighting_offset",
        initial.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
    )
    havd = opts.get(
        "havdalah_offset", initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET)
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havd,
    }

    # We no longer reload the entire integration here.
    # Each sensor will automatically pick up the new offsets on its next update.


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
