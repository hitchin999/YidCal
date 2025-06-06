# /config/custom_components/yidcal/__init__.py
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers.event import async_call_later
import logging

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YidCal from a config entry."""
    entry.add_update_listener(_async_update_options)

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

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havd,
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
    havd = opts.get(
        "havdalah_offset", initial.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET)
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "strip_nikud": strip,
        "candlelighting_offset": candle,
        "havdalah_offset": havd,
    }

    # Schedule the reload one second later, but ensure the reload itself
    # is called on the event loop via hass.loop.call_soon_threadsafe(...)
    async_call_later(
        hass,
        1,
        lambda now: _delayed_reload(hass, entry.entry_id),
    )


def _delayed_reload(hass: HomeAssistant, entry_id: str) -> None:
    """Helper for async_call_later: switch back to the event loop and reload."""
    _LOGGER.debug("YidCal: scheduling reload of entry %s", entry_id)
    # Use call_soon_threadsafe so we’re back on HA’s main loop before creating the task
    hass.loop.call_soon_threadsafe(
        lambda: hass.async_create_task(hass.config_entries.async_reload(entry_id))
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
