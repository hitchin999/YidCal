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
        # define start/end
        start = (m == "תשרי" and (day > 22 or (day == 22 and now >= dawn))) or \
                (m in ["חשון","כסלו","טבת","שבט","אדר","אדר א","אדר ב"]) or \
                (m == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
        return "מוריד הגשם" if start and now >= dawn else "מוריד הטל"

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
        # calculate havdala
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        havdala_time = sun_times["sunset"] + timedelta(minutes=self._havdalah_offset)
        # compute Hebrew date
        hd = PHebrewDate.from_pydate(today)
        day, m = hd.day, hd.month_name(hebrew=True)
        # determine in-window (Kislev 5+ through Nisan 14)
        in_window = (m == "כסלו" and day >= 5) or \
                    (m in ["טבת","שבט","אדר","אדר א","אדר ב"]) or \
                    (m == "ניסן" and day < 15)
        # return based on window and time
        if now >= havdala_time and in_window:
            return "ותן טל ומטר לברכה"
        return "ותן ברכה"
