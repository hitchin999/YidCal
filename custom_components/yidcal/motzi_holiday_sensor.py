# motzi_holiday_sensor.py

from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_interval

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from hdate import HDateInfo

from zmanim.zmanim_calendar import ZmanimCalendar
from .device import YidCalDevice
from .zman_sensors import get_geo
from .const import DOMAIN




"""
Base class for “מוצאי <holiday>” sensors.
Subclasses must set:
  - HOLIDAY_NAME   : exact Hebrew string as yielded by PHebrewDate.holiday(hebrew=True, prefix_day=True)
  - _attr_name     : the friendly name, e.g. "מוצאי יום הכיפורים"
  - _attr_unique_id: a unique_id such as "yidcal_motzei_yom_kippur"

Logic for every “motzei” sensor:
  1) If *today’s* Hebrew date == HOLIDAY_NAME, holiday_date = today.
  2) Else if *yesterday’s* Hebrew date == HOLIDAY_NAME, holiday_date = yesterday.
  3) Otherwise, no motzei (OFF).
  4) If we have a holiday_date, then:
       motzei_start = sunset(holiday_date) + havdalah_offset,
       motzei_end   = (holiday_date + 1 day) at 02:00 local.
       Sensor is ON if motzei_start ≤ now < motzei_end.
"""

class MotzeiHolidaySensor(YidCalDevice, BinarySensorEntity, RestoreEntity):
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

        # Display name in UI (Hebrew)
        self._attr_name = friendly_name
        # Unique ID for HA entity registry
        self._forced_unique_id = unique_id
        self._attr_unique_id = unique_id

        # Force HA to use exactly this entity_id (no slugification)
        self._forced_entity_id = f"binary_sensor.{unique_id}"

        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state: bool = False

    @property
    def entity_id(self) -> str:
        """Return the exact entity_id, ignoring any slugification attempts."""
        return self._forced_entity_id

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        """Ignore HA’s attempts to overwrite entity_id."""
        return

    @property
    def unique_id(self) -> str:
        """Expose unique_id so HA can manage it in the UI."""
        return self._forced_unique_id

    async def async_added_to_hass(self) -> None:
        """Restore last ON/OFF state on restart."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._state = (last.state == "on")
            
        # Poll every minute (register via base class so unsubscribe is stored)
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        """
        Every minute, decide if “motzei <holiday>” should be ON.

        1) Determine holiday_date:
             - If today's Hebrew date == HOLIDAY_NAME, holiday_date = today.
             - Else if yesterday's Hebrew date == HOLIDAY_NAME, holiday_date = yesterday.
             - Else no holiday_date → OFF.
        2) If holiday_date is set:
             motzei_start = sunset(holiday_date) + havdalah_offset
             motzei_end   = (holiday_date + 1 day) at 02:00 local
             ON if motzei_start ≤ now < motzei_end.
        """
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

        # 1) Check today's Hebrew date
        holiday_date: datetime.date | None = None
        hd_today = PHebrewDate.from_pydate(today_date)
        if hd_today.holiday(hebrew=True, prefix_day=True) == self.HOLIDAY_NAME:
            holiday_date = today_date
        else:
            # 2) Check yesterday's Hebrew date
            yesterday_date = today_date - timedelta(days=1)
            hd_prev = PHebrewDate.from_pydate(yesterday_date)
            if hd_prev.holiday(hebrew=True, prefix_day=True) == self.HOLIDAY_NAME:
                holiday_date = yesterday_date

        # 3) If we found a holiday_date, compute motzei window
        if holiday_date:
            # sunset on the holiday_date
            z_hol = sun(loc.observer, date=holiday_date, tzinfo=tz)
            sunset_hol = z_hol["sunset"]
            motzei_start = sunset_hol + timedelta(minutes=self._havdalah_offset)

            # cutoff is holiday_date + 1 day at 02:00
            next_day = holiday_date + timedelta(days=1)
            motzei_end = datetime.datetime.combine(
                next_day, time(hour=2, minute=0), tzinfo=tz
            )

            self._state = (motzei_start <= now < motzei_end)
        else:
            self._state = False

#
# ─── Subclasses: each “מוצאי <holiday>”──────────────────────────────────────────
#

class MotzeiYomKippurSensor(MotzeiHolidaySensor):
    """מוצאי יום הכיפורים (ט״י תשרי)"""
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
    """מוצאי פסח (ט״ו ניסן)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="ח׳ פסח",
            friendly_name="מוצאי פסח",
            unique_id="yidcal_motzei_pesach",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiSukkosSensor(MotzeiHolidaySensor):
    """מוצאי סוכות (ט״ו תשרי)"""
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
    """מוצאי שבועות (ב׳ שבועות)"""
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
    """מוצאי ראש השנה (ב׳ תשרי)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="ב׳ ראש השנה",
            friendly_name="מוצאי ראש השנה",
            unique_id="yidcal_motzei_rosh_hashana",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiShivaUsorBTammuzSensor(MotzeiHolidaySensor):
    """מוצאי צום שבעה עשר בתמוז (י״ז בתמוז)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="י״ז בתמוז",
            friendly_name="מוצאי צום שבעה עשר בתמוז",
            unique_id="yidcal_motzei_shiva_usor_btammuz",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiTishaBavSensor(MotzeiHolidaySensor):
    """מוצאי תשעה באב (י״ט אב)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name="ט׳ באב",
            friendly_name="מוצאי תשעה באב",
            unique_id="yidcal_motzei_tisha_bav",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )
        

class MotziSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from havdalah on Shabbos or Yom Tov until 2 AM next day."""
    _attr_name = "Motzi"
    _attr_icon = "mdi:liquor"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "motzi"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass

        self._candle = candle_offset
        self._havdalah = havdalah_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._geo: ZmanimCalendar | None = None
        self._state = False
        self._attr_extra_state_attributes: dict[str, bool | str] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # restore last state
        last = await self.async_get_last_state()
        if last:
            self._state = (last.state == "on")
        # load geo once
        self._geo = await get_geo(self.hass)
        # initial calc
        await self.async_update()
        # poll every minute
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        now = (now or datetime.datetime.now(self._tz)).astimezone(self._tz)
        today = now.date()
        yesterday = today - timedelta(days=1)
        
        # raw detection
        raw_shabbos = (yesterday.weekday() == 5)
        raw_holiday = HDateInfo(today, diaspora=True).is_yom_tov

        if not self._geo:
            self._state = False
            return

        # compute yesterday's sunset
        cal_prev = ZmanimCalendar(geo_location=self._geo, date=yesterday)
        sunset_prev = cal_prev.sunset().astimezone(self._tz)

        # window: havdalah → 2 AM today
        start = sunset_prev + timedelta(minutes=self._havdalah)
        end = datetime.datetime.combine(today, time(2, 0), tzinfo=self._tz)

        in_window = (start <= now < end)
        self._state = in_window and (raw_shabbos or raw_holiday)



        # blocking: e.g. a raw shabbos motzi that coincides with a Yom Tov today
        is_yomtov_today = HDateInfo(today, diaspora=True).is_yom_tov
        is_shabbos_today = (today.weekday() == 5)

        attrs: dict[str, bool | str] = {
            "now": now.isoformat(),
            "is_motzi_shabbos": raw_shabbos,
            "is_motzi_yomtov": raw_holiday,
            "blocked_motzi_shabbos": raw_shabbos and is_yomtov_today,
            "blocked_motzi_yomtov": raw_holiday and is_shabbos_today,
        }
        if in_window:
            attrs["window_start"] = start.isoformat()
            attrs["window_end"] = end.isoformat()

        self._attr_extra_state_attributes = attrs
        
