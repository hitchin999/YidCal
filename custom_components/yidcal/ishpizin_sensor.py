from __future__ import annotations
import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

ISHPIZIN_NAMES = ["אברהם", "יצחק", "יעקב", "משה", "אהרן", "יוסף", "דוד"]
ISHPIZIN_STATES = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

# Yiddish weekdays (Monday=0 .. Sunday=6)
WEEKDAYS_YI = [
    "מאנטאג",     # Monday
    "דינסטאג",    # Tuesday
    "מיטוואך",    # Wednesday
    "דאנערשטאג",  # Thursday
    "פרייטאג",    # Friday
    "שבת קודש",   # Saturday
    "זונטאג",     # Sunday
]


def _hebrew_day_label(i: int) -> str:
    """Return the Yom Tov / Chol Hamoed label, with הושענא רבה for the last day."""
    yom_tov = ["א", "ב"]
    chol = ["א", "ב", "ג", "ד"]
    if i <= 1:
        return f"{yom_tov[i]}׳ דיום טוב"
    elif i == 6:
        return "הושענא רבה"
    else:
        return f"{chol[i - 2]}׳ דחול המועד"


class IshpizinSensor(YidCalDevice, RestoreEntity, SensorEntity):
    """Ishpizin sensor — keeps enum state, exposes schedule in attributes."""

    _attr_icon = "mdi:account-group"
    _attr_device_class = "enum"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._havdalah_offset = havdalah_offset
        self._attr_unique_id = "yidcal_ishpizin"
        self.entity_id = "sensor.yidcal_ishpizin"
        self._attr_name = "Ishpizin"
        self._attr_native_value = ""
        self._attr_extra_state_attributes = {
            f"אושפיזא ד{name}": False for name in ISHPIZIN_NAMES
        }

    @property
    def options(self) -> list[str]:
        return ISHPIZIN_STATES

    @property
    def native_value(self) -> str:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ISHPIZIN_STATES:
            self._attr_native_value = last.state
            for key in self._attr_extra_state_attributes:
                if key in last.attributes:
                    self._attr_extra_state_attributes[key] = last.attributes.get(key, False)
        await self.async_update()
        async_track_time_interval(self.hass, self.async_update, timedelta(minutes=1))

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()
        heb_year = PHebrewDate.from_pydate(today).year

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Reset attributes/flags
        attrs: dict[str, object] = {f"אושפיזא ד{name}": False for name in ISHPIZIN_NAMES}
        lines: list[str] = []
        active_state = ""

        for i, name in enumerate(ISHPIZIN_NAMES):
            # Hebrew calendar date for this Ushpizin: 15..21 Tishrei of heb_year
            gdate = PHebrewDate(heb_year, 7, 15 + i).to_pydate()

            # The NIGHT of this Hebrew day begins at sunset of the previous civil day
            prev_gdate = gdate - timedelta(days=1)

            # Weekday label should reflect the night start
            weekday_yi = WEEKDAYS_YI[gdate.weekday()]
            label = _hebrew_day_label(i)

            # Build schedule entry aligned to the night start
            entry = f"{weekday_yi} {label}:\nאושפיזא ד{name}."
            lines.append(entry)

            # Correct window: [sunset(prev_gdate)+offset, sunset(gdate)+offset)
            s_prev = sun(loc.observer, date=prev_gdate, tzinfo=tz)
            start = s_prev["sunset"] + timedelta(minutes=self._havdalah_offset)

            s_curr = sun(loc.observer, date=gdate, tzinfo=tz)
            end = s_curr["sunset"] + timedelta(minutes=self._havdalah_offset)

            if start <= now < end:
                active_state = f"אושפיזא ד{name}"
                attrs[active_state] = True

        # Keep only valid enum state
        self._attr_native_value = active_state if active_state in ISHPIZIN_STATES else ""
        self._attr_name = "Ishpizin"

        # Pretty “list-like” schedule + backward-compat states list
        attrs["Ishpizin Schedule"] = "\n\n".join(lines)
        attrs["Possible states"] = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

        self._attr_extra_state_attributes = attrs
