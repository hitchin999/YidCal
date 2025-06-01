# motzi_holiday_sensor.py

from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

"""
Base class for “מוצאי <holiday>” sensors.
Subclasses must set:
  - HOLIDAY_NAME   : exact Hebrew string as yielded by HDP.holiday(hebrew=True, prefix_day=True)
  - _attr_name     : the friendly name, e.g. "מוצאי יום הכיפורים"
  - _attr_unique_id: a unique_id such as "yidcal_motzei_yom_kippur"
They will be ON starting at (yesterday’s sunset + havdalah_offset) until today at 02:00 local time.
"""

class MotzeiHolidaySensor(BinarySensorEntity, RestoreEntity):
    _attr_icon = "mdi:calendar-star"  # you can override per‐holiday if you like

    def __init__(
        self,
        hass: HomeAssistant,
        holiday_name: str,
        friendly_name: str,
        unique_id: str,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        self.hass = hass
        self.HOLIDAY_NAME = holiday_name
        self._attr_name = friendly_name
        self._attr_unique_id = unique_id

        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        # internal boolean state
        self._state: bool = False

        # schedule periodic updates (every minute)
        async_track_time_interval(hass, self.async_update, timedelta(minutes=1))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._state = (last.state == "on")

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today_date = now.date()

        # Build LocationInfo from HA config
        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Compute yesterday’s date and Hebrew date
        yesterday_date = today_date - timedelta(days=1)
        hd_prev = PHebrewDate.from_pydate(yesterday_date)
        prev_name = hd_prev.holiday(hebrew=True, prefix_day=True)

        # 1) If yesterday’s holiday name exactly matches our HOLIDAY_NAME,
        #    compute yesterday’s sunset + havdalah_offset => motzei_start.
        motzei_start: datetime.datetime | None = None
        if prev_name == self.HOLIDAY_NAME:
            z_prev = sun(loc.observer, date=yesterday_date, tzinfo=tz)
            prev_sunset = z_prev["sunset"]
            motzei_start = prev_sunset + timedelta(minutes=self._havdalah_offset)

        # 2) Define today at 02:00 (local) as motzei_end
        today_two_am = datetime.datetime.combine(today_date, time(hour=2, minute=0), tzinfo=tz)

        # 3) Determine ON/OFF:
        #    ON if motzei_start is not None AND motzei_start ≤ now < today_two_am
        if motzei_start is not None and (motzei_start <= now < today_two_am):
            self._state = True
        else:
            self._state = False


#
# ─── Subclasses: one for each “major” Yom Tov ───────────────────────────────────
#

class MotzeiYomKippurSensor(MotzeiHolidaySensor):
    """
    “מוצאי יום הכיפורים”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="יום הכיפורים",
            friendly_name="מוצאי יום הכיפורים",
            unique_id="yidcal_motzei_yom_kippur",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiPesachSensor(MotzeiHolidaySensor):
    """
    “מוצאי פסח”:
    Pesach I ends on day 15 Nisan at sunset + offset; ON until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="אחרון של פסח",
            friendly_name="מוצאי פסח",
            unique_id="yidcal_motzei_pesach",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiSukkotSensor(MotzeiHolidaySensor):
    """
    “מוצאי סוכות”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="שמחת תורה",
            friendly_name="מוצאי סוכות",
            unique_id="yidcal_motzei_sukkos",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiShavuotSensor(MotzeiHolidaySensor):
    """
    “מוצאי שבועות”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="שבועות ב׳",
            friendly_name="מוצאי שבועות",
            unique_id="yidcal_motzei_shavuos",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiRoshHashanaSensor(MotzeiHolidaySensor):
    """
    “מוצאי ראש השנה”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="ראש השנה ב׳",
            friendly_name="מוצאי ראש השנה",
            unique_id="yidcal_motzei_rosh_hashana",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


#
# ─── New subclasses for “צום שבעה עשר בתמוז” and “תשעה באב” ────────────────────
#

class MotzeiShivaUsorBTammuzSensor(MotzeiHolidaySensor):
    """
    “מוצאי צום שבעה עשר בתמוז”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="צום שבעה עשר בתמוז",
            friendly_name="מוצאי צום שבעה עשר בתמוז",
            unique_id="yidcal_motzei_shiva_usor_btammuz",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiTishaBavSensor(MotzeiHolidaySensor):
    """
    “מוצאי תשעה באב”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="תשעה באב",
            friendly_name="מוצאי תשעה באב",
            unique_id="yidcal_motzei_tisha_bav",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )
