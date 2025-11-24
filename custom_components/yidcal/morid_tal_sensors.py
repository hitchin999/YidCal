"""
custom_components/yidcal/morid_tal_sensors.py

Defines two YidCal sensors using pyluach for Hebrew date computation with continuous windows:
- MoridGeshemSensor: switches to 'מוריד הגשם' at dawn on 22 Tishrei, stays until dawn on 15 Nisan, otherwise 'מוריד הטל'.
- TalUMatarSensor:
    • In Israel: switches to 'ותן טל ומטר לברכה' at Maariv of 7 Cheshvan, stays until the first night of Pesach (halachic roll at sunset + havdalah offset).
    • In Diaspora: switches to 'ותן טל ומטר לברכה' at Maariv of Dec 4 (Dec 5 in Gregorian leap years), stays until the first night of Pesach.
"""
from __future__ import annotations
from datetime import timedelta, datetime, date
from zoneinfo import ZoneInfo
from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.util.dt import now as dt_now
from astral.sun import sun
from astral import LocationInfo
from pyluach.dates import HebrewDate as PHebrewDate
from .device import YidCalDisplayDevice
from .const import DOMAIN
import calendar

class MoridGeshemSensor(YidCalDisplayDevice, SensorEntity):
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

class TalUMatarSensor(YidCalDisplayDevice, SensorEntity):
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
        cfg = hass.data[DOMAIN]["config"]
        self._diaspora: bool = cfg.get("diaspora", True)

    @property
    def native_value(self) -> str:
        now = dt_now()
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )

        # Halachic date (flip at sunset + havdalah offset)
        today = now.date()
        sun_times_today = sun(loc.observer, date=today, tzinfo=tz)
        havdala_today = sun_times_today["sunset"] + timedelta(minutes=self._havdalah_offset)
        halachic_date = today + (timedelta(days=1) if now >= havdala_today else timedelta(days=0))
        hd_hal = PHebrewDate.from_pydate(halachic_date)

        # ---------- End boundary: after the first night of Pesach we say "ותן ברכה" ----------
        # i.e., for halachic dates 15 Nisan and onward (Hebrew months: Nisan==1)
        if hd_hal.month == 1 and hd_hal.day >= 15:
            return "ותן ברכה"

        # ---------- Start boundary ----------
        if self._diaspora:
            # Diaspora: Dec 4 (Dec 5 in Gregorian leap years), at Maariv
            # Pick the current season’s December in the civil year of the *current* halachic date
            # Jan–Apr → previous December; May–Dec → this December
            dec_year = now.year - 1 if now.month <= 4 else now.year
            start_day = 5 if calendar.isleap(dec_year) else 4
            start_gdate = date(dec_year, 12, start_day)
            start_sunset = sun(loc.observer, date=start_gdate, tzinfo=tz)["sunset"]
            start_dt = start_sunset + timedelta(minutes=self._havdalah_offset)
            if now >= start_dt:
                return "ותן טל ומטר לברכה"
            else:
                return "ותן ברכה"
        else:
            # Israel: 7 Cheshvan (Maariv) until Pesach
            # Hebrew months: Nisan=1, Iyar=2, ..., Tishrei=7, Cheshvan=8
            if (hd_hal.month == 8 and hd_hal.day >= 7) or (9 <= hd_hal.month <= 13) or (hd_hal.month == 1 and hd_hal.day < 15):
                return "ותן טל ומטר לברכה"
            return "ותן ברכה"
