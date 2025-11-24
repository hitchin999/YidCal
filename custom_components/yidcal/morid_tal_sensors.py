"""
custom_components/yidcal/morid_tal_sensors.py

Defines two YidCal sensors using pyluach for Hebrew date computation with continuous windows:
- MoridGeshemSensor: switches to 'מוריד הגשם' at dawn (alos) on 22 Tishrei,
  stays until dawn on 15 Nisan, otherwise 'מוריד הטל'.
- TalUMatarSensor:
    • In Israel: switches to 'ותן טל ומטר לברכה' at Maariv of 7 Cheshvan,
      stays until the first night of Pesach (halachic roll at sunset + havdalah offset).
    • In Diaspora: switches to 'ותן טל ומטר לברכה' at Maariv of Dec 4
      (Dec 5 in Gregorian leap years), stays until the first night of Pesach.
"""
from __future__ import annotations

import calendar
import datetime
from datetime import timedelta, date
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from pyluach.dates import HebrewDate as PHebrewDate
from zmanim.zmanim_calendar import ZmanimCalendar

from .device import YidCalDisplayDevice
from .const import DOMAIN
from .zman_sensors import get_geo


def _round_half_up(dt: datetime.datetime) -> datetime.datetime:
    """Round to nearest minute: <30s floor, ≥30s ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime.datetime) -> datetime.datetime:
    """Always bump to next full minute (Motzi-style)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class MoridGeshemSensor(YidCalDisplayDevice, SensorEntity):
    """Rain blessing sensor: continuous window at dawn (alos)."""

    _attr_name = "Morid Geshem or Tal"

    def __init__(self, hass: HomeAssistant, helper) -> None:
        super().__init__()
        slug = "morid_geshem_or_tal"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper

        self._tz = ZoneInfo(hass.config.time_zone)
        self._geo = None
        self._state: str | None = None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)

        await self.async_update()

        # Sync exactly with other sensors: update every minute on HH:MM:00
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # Dawn (alos) = sunrise - 72 min, rounded half-up
        sunrise = ZmanimCalendar(geo_location=self._geo, date=today).sunrise().astimezone(self._tz)
        raw_dawn = sunrise - timedelta(minutes=72)
        dawn = _round_half_up(raw_dawn)

        # Hebrew date based on CIVIL day (your original behavior)
        hd = PHebrewDate.from_pydate(today)
        day = hd.day
        m = hd.month_name(hebrew=True)

        # Boundaries
        is_start_day = (m == "תשרי" and day == 22)  # Shemini Atzeres morning switch
        is_end_day = (m == "ניסן" and day == 15)    # Pesach morning switch

        # Middle window: Tishrei 23+ through Nisan 14
        in_middle = (
            (m == "תשרי" and day > 22)
            or (m in ["חשון", "כסלו", "טבת", "שבט", "אדר", "אדר א", "אדר ב"])
            or (m == "ניסן" and day < 15)
        )

        if is_start_day:
            self._state = "מוריד הגשם" if now_local >= dawn else "מוריד הטל"
        elif is_end_day:
            self._state = "מוריד הגשם" if now_local < dawn else "מוריד הטל"
        elif in_middle:
            self._state = "מוריד הגשם"
        else:
            self._state = "מוריד הטל"

        self.async_write_ha_state()


class TalUMatarSensor(YidCalDisplayDevice, SensorEntity):
    """Tal U'Matar sensor: continuous window at havdalah (tzeis)."""

    _attr_name = "Tal U'Matar"

    def __init__(self, hass: HomeAssistant, helper, havdalah_offset: int) -> None:
        super().__init__()
        slug = "tal_umatar"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._havdalah_offset = havdalah_offset

        self._tz = ZoneInfo(hass.config.time_zone)
        self._geo = None
        self._state: str | None = None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)

        await self.async_update()

        # Sync exactly with other sensors: update every minute on HH:MM:00
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        cfg = self.hass.data[DOMAIN]["config"]
        diaspora = cfg.get("diaspora", True)

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        def sunset_on(d: date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

        # Halachic roll at sunset + havdalah_offset (rounded ceil)
        raw_hav_today = sunset_on(today) + timedelta(minutes=self._havdalah_offset)
        hav_today = _round_ceil(raw_hav_today)

        halachic_date = today + (timedelta(days=1) if now_local >= hav_today else timedelta(days=0))
        hd_hal = PHebrewDate.from_pydate(halachic_date)

        # End boundary: after first night of Pesach, switch to "ותן ברכה"
        if hd_hal.month == 1 and hd_hal.day >= 15:
            self._state = "ותן ברכה"
            self.async_write_ha_state()
            return

        # Start boundary
        if diaspora:
            # Diaspora: Dec 4 (Dec 5 in Gregorian leap years) at Maariv
            dec_year = now_local.year - 1 if now_local.month <= 4 else now_local.year
            start_day = 5 if calendar.isleap(dec_year) else 4
            start_gdate = date(dec_year, 12, start_day)

            raw_start_dt = sunset_on(start_gdate) + timedelta(minutes=self._havdalah_offset)
            start_dt = _round_ceil(raw_start_dt)

            self._state = "ותן טל ומטר לברכה" if now_local >= start_dt else "ותן ברכה"

        else:
            # Israel: 7 Cheshvan Maariv through Pesach
            if (
                (hd_hal.month == 8 and hd_hal.day >= 7)
                or (9 <= hd_hal.month <= 13)
                or (hd_hal.month == 1 and hd_hal.day < 15)
            ):
                self._state = "ותן טל ומטר לברכה"
            else:
                self._state = "ותן ברכה"

        self.async_write_ha_state()
