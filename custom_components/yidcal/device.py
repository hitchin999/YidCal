# custom_components/yidcal/device.py
from __future__ import annotations

from datetime import timedelta
from collections.abc import Callable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval, async_track_sunset
from homeassistant.helpers.entity import Entity

DOMAIN = "yidcal"


class YidCalDevice(Entity):
    """Base mixin for ALL YidCal entities: shared DeviceInfo + listener management."""

    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_main")},
        name="YidCal",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="A Yiddish Calendar Integration",
        entry_type="service",
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()
        self._listener_unsubs: list[Callable[[], None]] = []

    # --- Listener helpers (usable by any subclass) ---
    def _register_listener(self, unsub: Callable[[], None]) -> None:
        self._listener_unsubs.append(unsub)

    def _register_interval(self, hass, callback, interval: timedelta):
        """Register an interval callback and remember its unsubscribe."""
        unsub = async_track_time_interval(hass, callback, interval)
        self._register_listener(unsub)
        return unsub

    def _register_sunset(self, hass, callback, offset: timedelta | None = None):
        """Register a sunset-based callback and remember its unsubscribe."""
        unsub = async_track_sunset(hass, callback, offset=offset)
        self._register_listener(unsub)
        return unsub

    async def async_will_remove_from_hass(self) -> None:
        """On entity removal, clean up any registered listeners."""
        for unsub in self._listener_unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._listener_unsubs.clear()
        await super().async_will_remove_from_hass()


# Use this for *Zmanim* entities so they show under a separate device
class YidCalZmanDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_zmanim")},
        name="YidCal — Zmanim",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Zmanim Times",
        entry_type="service",
    )


# Use this for the per-attribute mirror sensors from HolidaySensor
class YidCalAttrDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_holiday_attributes")},
        name="YidCal — Holiday Attribute Sensors",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Holiday Attribute Flags",
        entry_type="service",
    )


# Display / Rich-label sensors live here
class YidCalDisplayDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_display")},
        name="YidCal — Display",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Rich Display & Labels",
        entry_type="service",
    )
    
    
# Special policy / operational binary sensors
class YidCalSpecialDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_special_binaries")},
        name="YidCal — Special Binary Sensors",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Special Binary Sensors",
        entry_type="service",
    )
