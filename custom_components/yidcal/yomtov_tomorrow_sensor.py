# custom_components/yidcal/yomtov_tomorrow_sensor.py
"""
Binary sensor: "Yom Tov Tomorrow"

ON when tomorrow (civil calendar date + 1) is a Yom Tov day, OFF otherwise.
A simple all-day flag — from midnight to midnight — useful for morning prep
automations (unlike the Erev sensors, which track a specific window).

Ported from the Control4 "Advanced Hebcal" driver's Yomtov_Tomorrow variable.

Attributes:
  Today_Is_Yom_Tov:   True when today itself is already Yom Tov
                      (distinguishes "prep day" from "day 1 of a 2-day YT")
  Tomorrow_Date:      ISO date being evaluated
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .yidcal_lib.calcache import is_yom_tov as _cached_is_yom_tov


class YomTovTomorrowSensor(YidCalSpecialDevice, BinarySensorEntity):
    """Binary sensor that is ON when tomorrow is Yom Tov."""

    _attr_name = "Yom Tov Tomorrow"
    _attr_icon = "mdi:calendar-arrow-right"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "yomtov_tomorrow"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)

        # caches
        self._today_is_yt: bool = False
        self._tomorrow_iso: str | None = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()
        # Cheap cached-date lookup; the minute tick catches midnight rollover,
        # manual clock jumps, and test-mode date walks promptly.
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now=None) -> None:
        now_local = (now or datetime.now(self._tz)).astimezone(self._tz)
        today = now_local.date()
        tomorrow = today + timedelta(days=1)

        self._attr_is_on = _cached_is_yom_tov(tomorrow, self._diaspora)
        self._today_is_yt = _cached_is_yom_tov(today, self._diaspora)
        self._tomorrow_iso = tomorrow.isoformat()

        if self._added:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        return {
            "Today_Is_Yom_Tov": self._today_is_yt,
            "Tomorrow_Date": self._tomorrow_iso or "",
        }
