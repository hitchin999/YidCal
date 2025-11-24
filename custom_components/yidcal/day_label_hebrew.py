from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from .zman_sensors import get_geo

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

        self._geo = None
        self._tz = ZoneInfo(self.hass.config.time_zone)

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        cfg = self.hass.data[DOMAIN]["config"]
        tz = ZoneInfo(cfg["tzname"])
        now_local = (now or dt_util.now()).astimezone(tz)
        today = now_local.date()

        # Zmanim sunset (same engine as ZmanMotzi / ZmanErev)
        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=today)
            .sunset()
            .astimezone(tz)
        )

        raw_candle   = sunset - timedelta(minutes=self._candle_offset)
        raw_havdalah = sunset + timedelta(minutes=self._havdalah_offset)

        candle   = _round_half_up(raw_candle)
        havdalah = _round_ceil(raw_havdalah)

        wd = now_local.weekday()  # Mon=0 ... Sun=6
        is_shabbat = (wd == 4 and now_local >= candle) or (wd == 5 and now_local < havdalah)

        if is_shabbat:
            lbl = "שבת קודש"
        elif wd == 4 and now_local.hour >= 12:
            lbl = "ערב שבת"
        elif wd == 5 and now_local >= havdalah:
            lbl = "מוצאי שבת"
        else:
            days = ["יום א׳", "יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳"]
            wd_to_idx = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5}
            lbl = days[wd_to_idx[wd]]

        self._state = lbl

        hebrew_days = ["יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳", "שבת קודש", "יום א׳"]
        self._attr_extra_state_attributes.update(
            {"today_label": hebrew_days[wd]}
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        await self.async_update()

        # flip at sunset+havdalah using same boundary as others
        self._register_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )

        # top-of-minute sync
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )
