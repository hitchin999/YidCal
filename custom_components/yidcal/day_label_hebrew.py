from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from astral import LocationInfo
from astral.sun import sun

from .device import YidCalDevice


class DayLabelHebrewSensor(YidCalDevice, SensorEntity):
    """Sensor for standalone day label in Hebrew."""

    _attr_name = "Day Label Hebrew"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "day_label_hebrew"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state: str | None = None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = datetime.now(tz)
        today = now.date()

        # calculate sunset and candle/havdalah times
        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )
        s = sun(loc.observer, date=today, tzinfo=tz)
        candle = s["sunset"] - timedelta(minutes=self._candle_offset)
        havdalah = s["sunset"] + timedelta(minutes=self._havdalah_offset)

        # determine if in Shabbat window
        wd = now.weekday()  # Monday=0 ... Sunday=6
        is_shabbat = (wd == 4 and now >= candle) or (wd == 5 and now < havdalah)

        if is_shabbat:
            lbl = "שבת קודש"
        elif wd == 4 and now.hour >= 12:
            lbl = "ערב שבת"
        elif wd == 5 and now >= havdalah:
            lbl = "מוצאי שבת"
        else:
            # regular weekdays and day labels
            days = ["יום א׳","יום ב׳","יום ג׳","יום ד׳","יום ה׳","יום ו׳","שבת"]
            wd_to_idx = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}
            lbl = days[wd_to_idx[wd]]

        self._state = lbl

    async def async_added_to_hass(self) -> None:
        """Register initial update and hourly polling."""
        await super().async_added_to_hass()

        # initial state
        await self.async_update()

        # poll hourly
        from homeassistant.helpers.event import async_track_time_interval
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )
