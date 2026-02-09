# custom_components/yidcal/dst_sensor.py
"""
Binary sensor for Daylight Saving Time (DST).

ON when the configured timezone is currently observing DST, OFF otherwise.

Attributes:
  Now:              ISO current local time
  UTC_Offset:       Current UTC offset (e.g. "-04:00")
  DST_Offset:       The DST component of the offset (e.g. "1:00:00" or "0:00:00")
  Timezone:         The configured timezone name
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from .device import YidCalSpecialDevice
from .const import DOMAIN


class DSTSensor(YidCalSpecialDevice, BinarySensorEntity):
    """Binary sensor that is ON when Daylight Saving Time is active."""

    _attr_name = "DST"
    _attr_icon = "mdi:clock-fast"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "dst"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._tzname = cfg["tzname"]

        # caches
        self._now_local: datetime | None = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()
        # Check every 60 seconds (DST transitions are rare but this keeps
        # the "Now" attribute fresh and catches the switch promptly)
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now=None) -> None:
        now_local = (now or datetime.now(self._tz)).astimezone(self._tz)
        self._now_local = now_local

        # dst() returns a timedelta; non-zero means DST is active
        dst_offset = now_local.dst()
        self._attr_is_on = dst_offset is not None and dst_offset.total_seconds() > 0

        if self._added:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        attrs: dict[str, str | bool] = {}
        if self._now_local:
            attrs["Now"] = self._now_local.isoformat()
            utc_off = self._now_local.strftime("%z")
            attrs["UTC_Offset"] = f"{utc_off[:3]}:{utc_off[3:]}"
            dst = self._now_local.dst()
            attrs["DST_Offset"] = str(dst) if dst else "0:00:00"
        attrs["Timezone"] = self._tzname
        return attrs
