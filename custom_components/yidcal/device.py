# custom_components/yidcal/device.py
from __future__ import annotations

import inspect
from datetime import timedelta, datetime
from collections.abc import Callable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_time_change,
    async_track_sunset,
)
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

    def _publishing(self, callback):
        """Wrap a scheduled callback so the entity PUBLISHES when it finishes.

        Most YidCal entities compute into ``self._attr_*`` inside
        ``async_update`` and never call ``async_write_ha_state``, leaving
        publication to the entity platform's own poll (``should_poll``
        defaults to True, ~30s cadence anchored to platform SETUP time). The
        value computed on the aligned :00 tick is therefore correct but not
        VISIBLE until an arbitrary second later -- a 20:26:00 shkia cut
        showing up in the logbook as 20:26:41, and the recorded second
        changing on every restart.

        Wrapping here, rather than appending a write inside each
        ``async_update``, is deliberate: it runs after the callback returns,
        so it still publishes for update methods that bail out through an
        early ``return``. Publishing an unchanged state is free -- HA's state
        machine drops a set whose state and attributes both compare equal --
        so the platform poll remains a harmless safety net.

        Objects that are not entities (e.g. the zmanim coordinator) have no
        ``async_write_ha_state`` and are skipped.
        """
        async def _wrapped(*args, **kwargs):
            result = callback(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
            writer = getattr(self, "async_write_ha_state", None)
            if (
                writer is not None
                and getattr(self, "hass", None) is not None
                and getattr(self, "entity_id", None)
            ):
                writer()

        return _wrapped

    def _register_interval(self, hass, callback, interval: timedelta):
        """Register a periodic callback and remember its unsubscribe.

        A 1-minute cadence is registered as a wall-clock tick at :00
        seconds instead of a plain interval. async_track_time_interval
        fires 60s after REGISTRATION, so its ticks inherit whatever
        second the entity happened to be set up at (:41, :44, ...).
        Sensors here compare against zmanim rounded to :00, so the
        boundary would be crossed on the minute but not EVALUATED until
        the next tick -- reporting the flip up to 59s late (a state
        change logged at 20:26:41 for a 20:26:00 shkia cut).
        async_track_time_change(second=0) evaluates exactly on the
        minute, matching the rounded zmanim and the sensors that already
        use that primitive directly. Other cadences (30-minute, hourly)
        keep the plain interval -- alignment only matters at the
        minute resolution the zmanim are rounded to.
        """
        if interval == timedelta(minutes=1):
            unsub = async_track_time_change(
                hass, self._publishing(callback), second=0
            )
        else:
            unsub = async_track_time_interval(
                hass, self._publishing(callback), interval
            )
        self._register_listener(unsub)
        return unsub

    def _register_sunset(self, hass, callback, offset: timedelta | None = None):
        """Register a sunset-based callback and remember its unsubscribe."""
        unsub = async_track_sunset(hass, self._publishing(callback), offset=offset)
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
        name="YidCal — Special Sensors",
        manufacturer="Yoel Goldstein/Vaayer LLC",
        model="Special Sensors",
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
