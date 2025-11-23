from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.event import async_track_time_change
from astral import LocationInfo
from astral.sun import sun

from .device import YidCalDevice
from .const import DOMAIN

def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)

def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class DayLabelHebrewSensor(YidCalDevice, SensorEntity):
    """Sensor for standalone day label in Hebrew."""

    _attr_name = "Day Label Hebrew"

    # Possible states
    _possible_states = [
        "יום א׳",
        "יום ב׳",
        "יום ג׳",
        "יום ד׳",
        "יום ה׳",
        "יום ו׳",
        "ערב שבת",
        "שבת קודש",
        "מוצאי שבת",
    ]

    # ENUM sensor with dropdown options in Automations UI
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _possible_states

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
        self._attr_extra_state_attributes = {
            "possible_states": self._possible_states,
            "today_label": None,
        }

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_update(self, now=None) -> None:
        cfg = self.hass.data[DOMAIN]["config"]
        tz = ZoneInfo(cfg["tzname"])
        now = datetime.now(tz)
        today = now.date()

        # calculate sunset and candle/havdalah times
        loc = LocationInfo(
            name="home",
            region="",
            timezone=cfg["tzname"],
            latitude=cfg["latitude"],
            longitude=cfg["longitude"],
        )
        s = sun(loc.observer, date=today, tzinfo=tz)
        raw_candle   = s["sunset"] - timedelta(minutes=self._candle_offset)
        raw_havdalah = s["sunset"] + timedelta(minutes=self._havdalah_offset)

        candle   = _round_half_up(raw_candle)
        havdalah = _round_ceil(raw_havdalah)

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
            # regular weekdays only
            days = ["יום א׳", "יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳"]
            wd_to_idx = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5}
            lbl = days[wd_to_idx[wd]]

        self._state = lbl

        # update attributes with today's label only
        hebrew_days = ["יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳", "שבת קודש", "יום א׳"]
        self._attr_extra_state_attributes.update(
            {
                "today_label": hebrew_days[wd],
            }
        )

    async def async_added_to_hass(self) -> None:
        """Register initial update and hourly polling."""
        await super().async_added_to_hass()

        # initial state
        await self.async_update()

        # precise updates: flip at sunset+havdalah
        self._register_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )
        # and also once per minute *on the minute* so it lines up with Zman Motzi
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )
