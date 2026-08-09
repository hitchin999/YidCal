"""
custom_components/yidcal/day_label_hebrew_full.py

sensor.yidcal_day_label_hebrew_full — the day of the week written out in
Hebrew (יום ראשון, יום שני, יום שלישי …) rather than abbreviated.

YidCal already had two day labels: Yiddish (זונטאג, מאנטאג) and Hebrew
short (יום א׳, יום ב׳). This is the third, full form, and it is
selectable as the Full Display sensor's day label alongside the other
two — see ``day_label_language`` in the config flow.

The window rules are deliberately identical to
``sensor.yidcal_day_label_hebrew``: Friday afternoon reads ערב שבת,
candle-lighting through havdalah reads שבת קודש, and havdalah until
midnight reads מוצאי שבת. Only the wording differs, so the three labels
can be swapped in the Full Display without changing when it flips.

Alongside the label, ``Hebrew_Date`` carries the Hebrew calendar date and
``Full`` carries the two joined — e.g. ``יום ראשון י״ג אב תשפ״ו`` — for
anyone who wants the whole line from one attribute.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .const import DOMAIN
from .date_sensor import get_hebrew_month_name, _normalize_hebrew_punct
from .device import YidCalDevice
from .yidcal_lib.helper import int_to_hebrew
from .yidcal_lib.zman_compute import (
    round_ceil as _round_ceil,
    round_half_up as _round_half_up,
    sunset_for_date,
)
from .zman_sensors import get_geo

# Python weekday (Mon=0 … Sun=6) → full Hebrew weekday label.
# Shabbos deliberately stays bare שבת: every wd==5 path resolves to
# שבת קודש or מוצאי שבת below, so this value only ever surfaces in the
# Weekday attribute, where יום שבת would read wrong.
_SPELLED_OUT = {
    6: "יום ראשון",
    0: "יום שני",
    1: "יום שלישי",
    2: "יום רביעי",
    3: "יום חמישי",
    4: "יום שישי",
    5: "שבת",
}

POSSIBLE_STATES = [
    "יום ראשון",
    "יום שני",
    "יום שלישי",
    "יום רביעי",
    "יום חמישי",
    "יום שישי",
    "ערב שבת",
    "שבת קודש",
    "מוצאי שבת",
]


def hebrew_date_string(pydate) -> str:
    """``י״ג אב תשפ״ו`` for a Gregorian date, matching sensor.yidcal_date."""
    heb = PHebrewDate.from_pydate(pydate)
    return _normalize_hebrew_punct(
        f"{int_to_hebrew(heb.day)} "
        f"{get_hebrew_month_name(heb.month, heb.year)} "
        f"{int_to_hebrew(heb.year % 1000)}"
    )


class DayLabelHebrewFullSensor(YidCalDevice, SensorEntity):
    """Full Hebrew weekday label, with the Hebrew date as an attribute."""

    _attr_name = "Day Label Hebrew Full"
    _attr_icon = "mdi:calendar-text"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = POSSIBLE_STATES
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "day_label_hebrew_full"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = int(candle_offset)
        self._havdalah_offset = int(havdalah_offset)

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

        self._state: str | None = None
        self._geo = None
        self._attr_extra_state_attributes: dict[str, str] = {}

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

        # Flip at havdalah, and re-check on every aligned :00 tick so the
        # ערב שבת / מוצאי שבת edges land on the minute like every other
        # YidCal label.
        self._register_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._publishing(self.async_update),
                second=0,
            )
        )

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        tz = self._tz
        now_local = (now or dt_util.now()).astimezone(tz)
        today = now_local.date()

        sunset = sunset_for_date(geo=self._geo, tz=tz, base_date=today)
        candle = _round_half_up(sunset - timedelta(minutes=self._candle_offset))
        havdalah = _round_ceil(sunset + timedelta(minutes=self._havdalah_offset))

        wd = now_local.weekday()  # Mon=0 … Sat=5, Sun=6
        is_shabbos = (wd == 4 and now_local >= candle) or (
            wd == 5 and now_local < havdalah
        )

        if is_shabbos:
            label = "שבת קודש"
        elif wd == 4 and now_local.hour >= 12:
            label = "ערב שבת"
        elif wd == 5 and now_local >= havdalah:
            label = "מוצאי שבת"
        else:
            label = _SPELLED_OUT[wd]

        self._state = label

        # The Hebrew date rolls at havdalah, exactly like sensor.yidcal_date.
        date_for_hebrew = today + timedelta(days=1) if now_local >= havdalah else today
        hebrew_date = hebrew_date_string(date_for_hebrew)

        self._attr_extra_state_attributes = {
            "Hebrew_Date": hebrew_date,
            "Weekday": _SPELLED_OUT[wd],
            "Full": f"{label} {hebrew_date}",
            "possible_states": POSSIBLE_STATES,
        }
