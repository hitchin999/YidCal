"""
custom_components/yidcal/morid_tal_sensors.py

Defines two YidCal sensors using pyluach for Hebrew date computation with continuous windows:
- MoridGeshemSensor: switches to 'מוריד הגשם' at dawn on 22 Tishrei, stays until dawn on 15 Nisan, otherwise 'מוריד הטל'.
- TalUMatarSensor: switches to 'ותן טל ומטר לברכה' at havdala on 5 Kislev, stays until havdala on 15 Nisan, otherwise 'ותן ברכה'.
"""
from __future__ import annotations
from datetime import timedelta
from zoneinfo import ZoneInfo
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.util.dt import now as dt_now
from astral.sun import sun
from astral import LocationInfo
from pyluach.dates import HebrewDate as PHebrewDate
from .device import YidCalDevice

class MoridGeshemSensor(YidCalDevice, SensorEntity):
    """Rain blessing sensor: continuous window at dawn."""
    _attr_name = "Morid Geshem or Tal"

    def __init__(self, hass: HomeAssistant, helper) -> None:
        super().__init__()
        slug = "morid_geshem_or_tal"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper

    @property
    def native_value(self) -> str:
        now = dt_now()
        today = now.date()
        # calculate dawn (alos)
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        dawn = sun_times["sunrise"] - timedelta(minutes=72)
        # compute Hebrew date
        hd = PHebrewDate.from_pydate(today)
        day, m = hd.day, hd.month_name(hebrew=True)
        # Define start and end for boundaries
        is_start_day = (m == "תשרי" and day == 22)
        is_end_day = (m == "ניסן" and day == 15)
        # Adjusted in_window for middle days only (Tishrei 23+ through Nisan 14)
        in_middle = (m == "תשרי" and day > 22) or \
                    (m in ["חשון", "כסלו", "טבת", "שבט", "אדר", "אדר א", "אדר ב"]) or \
                    (m == "ניסן" and day < 15)
        # Logic for continuous window
        if is_start_day:
            return "מוריד הגשם" if now >= dawn else "מוריד הטל"
        elif is_end_day:
            return "מוריד הגשם" if now < dawn else "מוריד הטל"
        elif in_middle:
            return "מוריד הגשם"
        else:
            return "מוריד הטל"

class TalUMatarSensor(YidCalDevice, SensorEntity):
    """Tal U'Matar sensor: continuous window at havdala."""
    _attr_name = "Tal U'Matar"

    def __init__(self, hass: HomeAssistant, helper, havdalah_offset: int) -> None:
        super().__init__()
        slug = "tal_umatar"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._havdalah_offset = havdalah_offset

    @property
    def native_value(self) -> str:
        now = dt_now()
        today = now.date()
        # Calculate havdala (unchanged)
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        havdala_time = sun_times["sunset"] + timedelta(minutes=self._havdalah_offset)
        # Compute Hebrew date (unchanged)
        hd = PHebrewDate.from_pydate(today)
        day, m = hd.day, hd.month_name(hebrew=True)
        # Define start and end for boundaries
        is_start_day = (m == "כסלו" and day == 5)
        is_end_day = (m == "ניסן" and day == 15)
        # Adjusted in_window for middle days only (Kislev 6+ through Nisan 14)
        in_middle = (m == "כסלו" and day > 5) or \
                    (m in ["טבת", "שבט", "אדר", "אדר א", "אדר ב"]) or \
                    (m == "ניסן" and day < 15)
        # Logic for continuous window
        if is_start_day:
            return "ותן טל ומטר לברכה" if now >= havdala_time else "ותן ברכה"
        elif is_end_day:
            return "ותן טל ומטר לברכה" if now < havdala_time else "ותן ברכה"
        elif in_middle:
            return "ותן טל ומטר לברכה"
        else:
            return "ותן ברכה"
