from __future__ import annotations

import datetime
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .device import YidCalDisplayDevice

_LOGGER = logging.getLogger(__name__)

ISHPIZIN_NAMES = ["אברהם", "יצחק", "יעקב", "משה", "אהרן", "יוסף", "דוד"]
ISHPIZIN_STATES = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

# Yiddish weekdays (Python weekday: Monday=0 .. Sunday=6)
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


# ---------------- Hebrew year formatting (5787 -> תשפ״ז) ---------------------

_GERESH = "\u05F3"     # ׳
_GERSHAYIM = "\u05F4"  # ״

_UNITS = {1: "א", 2: "ב", 3: "ג", 4: "ד", 5: "ה", 6: "ו", 7: "ז", 8: "ח", 9: "ט"}
_TENS = {10: "י", 20: "כ", 30: "ל", 40: "מ", 50: "נ", 60: "ס", 70: "ע", 80: "פ", 90: "צ"}
_HUNDREDS = {100: "ק", 200: "ר", 300: "ש", 400: "ת"}

def _hebrew_year_string(year: int) -> str:
    """
    Convert numeric Hebrew year (e.g., 5787) to standard letters like תשפ״ז.
    Omits thousands (5787 → 787), applies 15/16 rule, and inserts gershayim.
    """
    y = year % 1000  # drop thousands

    parts: list[str] = []

    # Hundreds (400,300,200,100)
    for h in (400, 300, 200, 100):
        if y >= h:
            parts.append(_HUNDREDS[h])
            y -= h

    # Tens 10–19 special cases (avoid יה/יו)
    if 10 <= y <= 19:
        if y == 15:
            parts.append("טו")
            y = 0
        elif y == 16:
            parts.append("טז")
            y = 0
        else:
            parts.append(_TENS[10])
            y -= 10

    # Remaining tens (90..10)
    for t in (90, 80, 70, 60, 50, 40, 30, 20, 10):
        if y >= t:
            parts.append(_TENS[t])
            y -= t
            break

    # Units (1..9)
    if y in _UNITS:
        parts.append(_UNITS[y])
        y = 0

    s = "".join(parts)

    # Add gershayim/geresh
    if len(s) >= 2:
        return s[:-1] + _GERSHAYIM + s[-1]
    elif len(s) == 1:
        return s + _GERESH
    return s


# ------------------------------- Sensor --------------------------------------

class IshpizinSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    """Ishpizin sensor — keeps enum state, exposes schedule in attributes.

    • Shows the *next* Hebrew year's schedule right after מוצאי שמחת תורה (diaspora: 23 Tishrei + havdalah offset).
    • Enum state remains tied to the *current* year's active window.
    • Adds 'די סקעדזשועל איז פאר יאר' (Hebrew letters, e.g., תשפ״ז).
    """

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
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        today = now.date()
        heb_year_now = PHebrewDate.from_pydate(today).year

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Flip schedule to NEXT YEAR after מוצאי שמחת תורה (diaspora: 23 Tishrei)
        st_gdate = PHebrewDate(heb_year_now, 7, 23).to_pydate()
        st_sun = sun(loc.observer, date=st_gdate, tzinfo=tz)
        motzaei_st = st_sun["sunset"] + timedelta(minutes=self._havdalah_offset)
        schedule_year = heb_year_now + 1 if now >= motzaei_st else heb_year_now

        # Build attributes (place YEAR first so it appears before the schedule)
        attrs: dict[str, object] = {}

        # Then the boolean flags (for backward-compat)
        for name in ISHPIZIN_NAMES:
            attrs[f"אושפיזא ד{name}"] = False

        lines: list[str] = []
        active_state = ""

        for i, name in enumerate(ISHPIZIN_NAMES):
            # 15–21 Tishrei nights for the displayed schedule year
            gdate = PHebrewDate(schedule_year, 7, 15 + i).to_pydate()
            prev_gdate = gdate - timedelta(days=1)

            # Weekday label: use the HEBREW DAY's weekday → gdate.weekday()
            # (e.g., if 15 Tishrei is Shabbos, show "שבת קודש", not "פרייטאג")
            weekday_yi = WEEKDAYS_YI[gdate.weekday()]
            label = _hebrew_day_label(i)

            lines.append(f"{weekday_yi} {label}:\nאושפיזא ד{name}.")

            # Active window for *current* year's state (not the displayed schedule):
            # [sunset(prev_gdate)+offset, sunset(gdate)+offset)
            s_prev = sun(loc.observer, date=prev_gdate, tzinfo=tz)
            start = s_prev["sunset"] + timedelta(minutes=self._havdalah_offset)
            s_curr = sun(loc.observer, date=gdate, tzinfo=tz)
            end = s_curr["sunset"] + timedelta(minutes=self._havdalah_offset)

            if schedule_year == heb_year_now and start <= now < end:
                active_state = f"אושפיזא ד{name}"
                attrs[active_state] = True

        # Enum state (empty outside window)
        self._attr_native_value = active_state if active_state in ISHPIZIN_STATES else ""
        self._attr_name = "Ishpizin"
        attrs["די סקעדזשועל איז פאר יאר"] = _hebrew_year_string(schedule_year)
        # Schedule text + possible states
        attrs["Ishpizin Schedule"] = "\n\n".join(lines)
        attrs["Possible states"] = [f"אושפיזא ד{name}" for name in ISHPIZIN_NAMES] + [""]

        self._attr_extra_state_attributes = attrs
