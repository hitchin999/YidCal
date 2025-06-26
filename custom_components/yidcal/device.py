# yidcal/device.py

from datetime import timedelta
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval, async_track_sunset
from homeassistant.helpers.entity import Entity

DOMAIN = "yidcal"


class YidCalDevice(Entity):
    """Mixin for all YidCal entities to share one DeviceInfo and manage listeners."""

    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_main")},
        name="YidCal",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="A Yiddish Calendar Integration",
        entry_type="service",
    )

    def __init__(self, *args, **kwargs):
        """Initialize listener tracking list."""
        super().__init__()
        # Store unsubscribe callbacks from any async_track_... listener
        self._listener_unsubs: list[callable] = []

    def _register_listener(self, unsub: callable) -> None:
        """Keep track of an unsubscribe function so we can cancel later."""
        self._listener_unsubs.append(unsub)

    def _register_interval(self, hass, callback, interval: timedelta):
        """
        Wrapper around async_track_time_interval.
        Registers the listener and saves its unsubscribe callback.
        """
        unsub = async_track_time_interval(hass, callback, interval)
        self._register_listener(unsub)
        return unsub

    def _register_sunset(self, hass, callback, offset: timedelta = None):
        """
        Wrapper around async_track_sunset.
        Registers the listener and saves its unsubscribe callback.
        """
        unsub = async_track_sunset(hass, callback, offset=offset)
        self._register_listener(unsub)
        return unsub

    async def async_will_remove_from_hass(self) -> None:
        """
        Called when Home Assistant is about to remove this entity.
        Cancel all stored listeners before cleanup.
        """
        # Cancel any active listeners
        for unsub in self._listener_unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._listener_unsubs.clear()

        # Allow parent classes to also clean up if needed
        await super().async_will_remove_from_hass()
