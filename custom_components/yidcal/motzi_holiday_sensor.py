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
    _attr_icon = "mdi:checkbox-marked-circle-outline"

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

        # Display name in UI
        self._attr_name = friendly_name
        # Unique ID that HA will store in its registry
        self._forced_unique_id = unique_id
        self._attr_unique_id = unique_id

        # We store this internally; the property below will return it
        self._forced_entity_id = f"binary_sensor.{unique_id}"

        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state: bool = False

        # Update every minute
        async_track_time_interval(hass, self.async_update, timedelta(minutes=1))

    @property
    def entity_id(self) -> str:
        """Always return exactly this, preventing HA from slugifying the Hebrew name."""
        return self._forced_entity_id

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        """Ignore HA’s attempt to overwrite entity_id."""
        return

    @property
    def unique_id(self) -> str:
        """Expose the same unique_id so HA will allow UI editing."""
        return self._forced_unique_id

    async def async_added_to_hass(self) -> None:
        """Restore last on/off state after restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._state = (last.state == "on")

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        """Every minute, check if “motzei <holiday>” should be ON (sunset+offset → 02:00)."""
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today_date = now.date()

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # Look at yesterday’s Hebrew date:
        yesterday_date = today_date - timedelta(days=1)
        hd_prev = PHebrewDate.from_pydate(yesterday_date)
        prev_name = hd_prev.holiday(hebrew=True, prefix_day=True)

        # If yesterday was our holiday, compute its sunset + havdalah_offset:
        motzei_start: datetime.datetime | None = None
        if prev_name == self.HOLIDAY_NAME:
            z_prev = sun(loc.observer, date=yesterday_date, tzinfo=tz)
            prev_sunset = z_prev["sunset"]
            motzei_start = prev_sunset + timedelta(minutes=self._havdalah_offset)

        # Keep the sensor ON until 02:00 local:
        today_two_am = datetime.datetime.combine(today_date, time(hour=2, minute=0), tzinfo=tz)

        self._state = bool(motzei_start and (motzei_start <= now < today_two_am))


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
            holiday_name="יום כיפור",
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


class MotzeiSukkosSensor(MotzeiHolidaySensor):
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


class MotzeiShavuosSensor(MotzeiHolidaySensor):
    """
    “מוצאי שבועות”:
    ON from (yesterday’s sunset + havdalah_offset) until 2 AM.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="ב׳ שבועות",
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
            holiday_name="ב׳ ראש השנה",
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
    ON from sunset on 17 Tammuz + havdalah_offset until 2 AM.
    """

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="י״ז בתמוז",
            friendly_name="מוצאי צום שבעה עשר בתמוז",
            unique_id="yidcal_motzei_shiva_usor_btammuz",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        """Override so that 17 Tammuz motzai = today's sunset + offset (fast days start at dawn)."""
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today_date = now.date()

        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )

        # 1) Compute sunset for today
        z_today = sun(loc.observer, date=today_date, tzinfo=tz)
        today_sunset = z_today["sunset"]

        # 2) Check if today is 17 Tammuz exactly ("י״ז בתמוז")
        hd_today = PHebrewDate.from_pydate(today_date)
        today_name = hd_today.holiday(hebrew=True, prefix_day=True)

        motzei_start: datetime.datetime | None = None

        if today_name == self.HOLIDAY_NAME:
            # Fast ends at today’s sunset + havdalah_offset
            motzei_start = today_sunset + timedelta(minutes=self._havdalah_offset)
        else:
            # Fallback to base‐class logic: was it yesterday’s Yom Tov?
            # (for multi‐day Yom Tovim that start at candle‐lighting)
            # Reuse the exact same code from MotzeiHolidaySensor.async_update:
            yesterday_date = today_date - timedelta(days=1)
            hd_prev = PHebrewDate.from_pydate(yesterday_date)
            prev_name = hd_prev.holiday(hebrew=True, prefix_day=True)
            if prev_name == self.HOLIDAY_NAME:
                z_prev = sun(loc.observer, date=yesterday_date, tzinfo=tz)
                motzei_start = z_prev["sunset"] + timedelta(minutes=self._havdalah_offset)

        # 3) Sensor stays ON until 02:00 local time the next morning
        today_two_am = datetime.datetime.combine(today_date, time(hour=2, minute=0), tzinfo=tz)
        self._state = bool(motzei_start and (motzei_start <= now < today_two_am))
      
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
