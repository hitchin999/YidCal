"""
custom_components/yidcal/morid_tal_sensors.py

Defines two new YidCal sensors using pyluach for Hebrew date computation:
- MoridGeshemSensor: returns 'מוריד הגשם' or 'מוריד הטל' after alos.
- TalUMatarSensor: returns 'ותן ברכה' or 'ותן טל ומטר לברכה', flipping only at 5 Kislev and 15 Nisan at havdala.
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
    """Rain blessing sensor: returns 'מוריד הגשם' or 'מוריד הטל' after alos."""
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
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        alos = sun_times["sunrise"] - timedelta(minutes=72)
        if now < alos:
            return ""
        hd = PHebrewDate.from_pydate(today)
        day = hd.day
        m = hd.month_name(hebrew=True)
        rainy = (
            (m == "תשרי" and day >= 22)
            or m in ["חשון","כסלו","טבת","שבט","אדר","אדר א","אדר ב"]
            or (m == "ניסן" and day < 15)
        )
        return "מוריד הגשם" if rainy else "מוריד הטל"

class TalUMatarSensor(YidCalDevice, SensorEntity):
    """Tal U'Matar sensor: 'ותן ברכה' or 'ותן טל ומטר לברכה', flipping only at transitions."""
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
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        havdala = sun_times["sunset"] + timedelta(minutes=self._havdalah_offset)
        hd = PHebrewDate.from_pydate(today)
        day = hd.day
        m = hd.month_name(hebrew=True)
        in_window = (
            (m == "כסלו" and day >= 5)
            or m in ["טבת","שבט","אדר","אדר א","אדר ב"]
            or (m == "ניסן" and day < 15)
        )
        start_switch = (m == "כסלו" and day == 5)
        end_switch   = (m == "ניסן" and day == 15)
        # Handle transition days at havdala
        if start_switch or end_switch:
            if now >= havdala:
                return "ותן טל ומטר לברכה" if in_window else "ותן ברכה"
            return "ותן ברכה" if in_window else "ותן טל ומטר לברכה"
        # On non-transition days, static insertion
        return "ותן טל ומטר לברכה" if in_window else "ותן ברכה"
