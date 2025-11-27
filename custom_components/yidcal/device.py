# custom_components/yidcal/device.py
from __future__ import annotations

from datetime import timedelta, datetime
from collections.abc import Callable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval, async_track_sunset
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .config_flow import CONF_TIME_FORMAT, DEFAULT_TIME_FORMAT


class YidCalDevice(Entity):
    """Base mixin for ALL YidCal entities: shared DeviceInfo + listener management
    + shared formatting helpers.
    """

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

        # Optional cache. If a subclass sets this, we’ll use it.
        # Otherwise we will read live from hass.data.
        self._time_format: str | None = None

    # --- Time format helpers (usable by any subclass) ---
    def _get_time_format(self) -> str:
        """Return configured time format ('12' or '24')."""
        # Priority:
        # 1) subclass cached value
        if self._time_format in ("12", "24"):
            return self._time_format

        # 2) global config set in __init__.py
        try:
            cfg = self.hass.data.get(DOMAIN, {}).get("config", {})
            fmt = cfg.get(CONF_TIME_FORMAT, DEFAULT_TIME_FORMAT)
            return fmt if fmt in ("12", "24") else DEFAULT_TIME_FORMAT
        except Exception:
            return DEFAULT_TIME_FORMAT

    def _format_simple_time(self, dt_local: datetime, fmt: str | None = None) -> str:
        """Format a local datetime into your *_Simple attrs honoring 12/24 option."""
        fmt = fmt or self._get_time_format()

        if fmt == "24":
            return dt_local.strftime("%H:%M")

        # 12-hour (your current style)
        hour = dt_local.hour % 12 or 12
        minute = dt_local.minute
        ampm = "AM" if dt_local.hour < 12 else "PM"
        return f"{hour}:{minute:02d} {ampm}"

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


class YidCalAttrDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_holiday_attributes")},
        name="YidCal — Holiday Attribute Sensors",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Holiday Attribute Flags",
        entry_type="service",
    )


class YidCalDisplayDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_display")},
        name="YidCal — Display",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Rich Display & Labels",
        entry_type="service",
    )


class YidCalSpecialDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_special_binaries")},
        name="YidCal — Special Binary Sensors",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Special Binary Sensors",
        entry_type="service",
    )


class YidCalEarlyDevice(YidCalDevice):
    _attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, "yidcal_early_shabbos_yt")},
        name="YidCal — Early Shabbos YT",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Early Shabbos / Yom Tov Controls",
        entry_type="service",
    )
