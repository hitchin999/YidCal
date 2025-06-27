from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_interval

from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

class UpcomingYomTovSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """Binary sensor that turns on 7 days before the next Yom Tov
    and turns off at the holiday's candle-lighting time. Attributes show the
    next holiday, its date, and the next on-time."""

    _attr_name = "Upcoming Yom Tov"
    _attr_icon = "mdi:calendar-star-outline"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_unique_id = "yidcal_upcoming_yomtov"
        self.entity_id = "binary_sensor.yidcal_upcoming_yomtov"

        self._attr_is_on = False
        self._attr_extra_state_attributes: dict[str, str] = {
            "next_holiday": "",
            "date": "",
            "next_on": "",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Restore last state/attributes
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == "on")
            self._attr_extra_state_attributes = {
                "next_holiday": last.attributes.get("next_holiday", ""),
                "date": last.attributes.get("date", ""),
                "next_on": last.attributes.get("next_on", ""),
            }
        # Initial update
        await self.async_update()
        # 1-minute polling for time changes
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()

        # --- Find the very next holiday (up to 1 year ahead) ---
        next_name = ""
        next_date: datetime.date | None = None
        for j in range(1, 366):
            d2 = today + timedelta(days=j)
            hd2 = PHebrewDate.from_pydate(d2)
            name2 = hd2.holiday(hebrew=True)
            if name2 and HDateInfo(d2, diaspora=True).is_yom_tov:
                next_name = name2
                next_date = d2
                break

        # Default OFF
        is_on = False
        next_on_time: datetime.datetime | None = None

        if next_date:
            # Compute turn-on time: 7 days before at midnight
            turn_on_date = next_date - timedelta(days=7)
            next_on_time = datetime.datetime(
                turn_on_date.year,
                turn_on_date.month,
                turn_on_date.day,
                0,
                0,
                tzinfo=tz
            )

            # Compute candle-lighting off time: previous day's sunset - candle_offset
            loc = LocationInfo(
                name="home", region="", timezone=self.hass.config.time_zone,
                latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
            )
            prev_day = next_date - timedelta(days=1)
            s = sun(loc.observer, date=prev_day, tzinfo=tz)
            off_time = s["sunset"] - timedelta(minutes=self._candle_offset)

            # Determine ON state
            is_on = (now >= next_on_time) and (now < off_time)

        # Build attributes
        attrs: dict[str, str] = {
            "next_holiday": next_name,
            "date": next_date.isoformat() if next_date else "",
            "next_on": next_on_time.isoformat() if next_on_time else "",
        }

        # Apply state and attributes
        self._attr_is_on = is_on
        self._attr_extra_state_attributes = attrs
