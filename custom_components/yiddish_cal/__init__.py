# /config/custom_components/yiddish_cal/__init__.py
"""Yiddish Calendar integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Yiddish Cal from a config entry."""
    # Listen for option updates
    entry.add_update_listener(_async_update_options)

    # Store user options in hass.data for sensor use
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": entry.options.get("strip_nikud", False),
        "candlelighting_offset": entry.options.get("candlelighting_offset", 15),
        "havdalah_offset": entry.options.get("havdalah_offset", 72),
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when config entry options are updated."""
    # Update stored options
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": entry.options.get("strip_nikud", False),
        "candlelighting_offset": entry.options.get("candlelighting_offset", 15),
        "havdalah_offset": entry.options.get("havdalah_offset", 72),
    }
    # Reload the integration to apply new options
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Remove stored data
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
