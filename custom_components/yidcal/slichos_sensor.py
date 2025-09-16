from __future__ import annotations

import datetime
from datetime import timedelta
from zoneinfo import ZoneInfo
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.event import async_track_time_change

from zmanim.zmanim_calendar import ZmanimCalendar
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalDevice
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    candle_offset = entry.options.get("candle_offset", 18)
    havdalah_offset = entry.options.get("havdalah_offset", 50)
    async_add_entities([SlichosSensor(hass, candle_offset, havdalah_offset)], update_before_add=True)


class SlichosSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    Continuous Slichos window:
      ON  = Alef Slichos Motzi  → Erev Yom Kippur candle-lighting
      OFF = whenever 'festival day' (with havdalah-roll) is RH (1–2 Tishrei) or Shabbos
    """

    _attr_name = "Slichos"
    _attr_icon = "mdi:book-open-variant"
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        slug = "slichos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        self._attr_is_on = False
        self._attr_extra_state_attributes: dict[str, str | bool | int] = {}

    def _schedule_update(self, *_args) -> None:
        """Thread-safe schedule helper."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state or "").lower() == "on"
            self._attr_extra_state_attributes = dict(last.attributes)

        # 1) Regular minute-by-minute interval
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

        # 2) Top-of-minute cron (handles manual clock jumps)
        unsub_cron = async_track_time_change(self.hass, self._schedule_update, second=0)
        self._register_listener(unsub_cron)

        # Initial update
        await self.async_update()

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None:
            return

        tz = ZoneInfo(self.hass.config.time_zone)
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        actual_date = now.date()

        geo = await get_geo(self.hass)

        # Today's sunset & roll to "festival day" at havdalah
        cal_today = ZmanimCalendar(geo_location=geo, date=actual_date)
        sunset_today = cal_today.sunset().astimezone(tz)
        havdalah_cut_today = sunset_today + timedelta(minutes=self._havdalah_offset)
        festival_date = actual_date + timedelta(days=1) if now >= havdalah_cut_today else actual_date
        hd_fest = PHebrewDate.from_pydate(festival_date)

        # ---- Choose correct High Holidays cycle (THIS year's) ----
        # If we're before Tishrei (months 1..6), the upcoming Tishrei is year+1
        target_year = hd_fest.year if hd_fest.month >= 7 else hd_fest.year + 1

        # ---- Global start: Alef Slichos Motzi ----
        tishrei1_greg = PHebrewDate(target_year, 7, 1).to_pydate()
        rh_wd = tishrei1_greg.weekday()  # Mon=0 ... Sun=6

        # Shabbos immediately BEFORE Rosh Hashanah
        pre_rh = tishrei1_greg - timedelta(days=1)
        alef_shabbos = pre_rh - timedelta(days=((pre_rh.weekday() - 5) % 7))

        # If RH is Monday or Tuesday → Alef Slichos is a WEEK EARLIER
        if rh_wd in (0, 1):
            alef_shabbos -= timedelta(days=7)

        # Start = that Motzi's havdalah (sunset + havdalah_offset)
        alef_start = (
            ZmanimCalendar(geo_location=geo, date=alef_shabbos)
            .sunset().astimezone(tz)
            + timedelta(minutes=self._havdalah_offset)
        )

        # ---- Global end: Erev YK candle-lighting (9 Tishrei) ----
        erev_yk_greg = PHebrewDate(target_year, 7, 9).to_pydate()
        cal_eyk = ZmanimCalendar(geo_location=geo, date=erev_yk_greg)
        erev_yk_candle = cal_eyk.sunset().astimezone(tz) - timedelta(minutes=self._candle_offset)

        in_global_window = (alef_start <= now < erev_yk_candle)

        # ---- Exclusions as real-time windows (Candle → Havdalah) ----
        
        # 1) Shabbos window (this week)
        wd = actual_date.weekday()                 # Mon=0 .. Sat=5, Sun=6
        friday = actual_date - timedelta(days=(wd - 4) % 7)
        saturday = friday + timedelta(days=1)
        
        cal_f = ZmanimCalendar(geo_location=geo, date=friday)
        shabbos_start = cal_f.sunset().astimezone(tz) - timedelta(minutes=self._candle_offset)
        
        cal_s = ZmanimCalendar(geo_location=geo, date=saturday)
        shabbos_end = cal_s.sunset().astimezone(tz) + timedelta(minutes=self._havdalah_offset)
        
        excluded_shabbos = (shabbos_start <= now < shabbos_end)
        
        # 2) Rosh Hashanah window (Tishrei 1–2 of target_year)
        tishrei1_greg = PHebrewDate(target_year, 7, 1).to_pydate()
        tishrei2_greg = tishrei1_greg + timedelta(days=1)
        
        cal_eve_rh = ZmanimCalendar(geo_location=geo, date=tishrei1_greg - timedelta(days=1))
        rh_start = cal_eve_rh.sunset().astimezone(tz) - timedelta(minutes=self._candle_offset)
        
        cal_rh2 = ZmanimCalendar(geo_location=geo, date=tishrei2_greg)
        rh_end = cal_rh2.sunset().astimezone(tz) + timedelta(minutes=self._havdalah_offset)
        
        excluded_rosh_hashanah = (rh_start <= now < rh_end)
        
        # Final state
        is_on = in_global_window and not (excluded_shabbos or excluded_rosh_hashanah)


        self._attr_is_on = is_on
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            #"Festival_Gregorian": festival_date.isoformat(),
            #"Festival_Hebrew": f"{hd_fest.month}/{hd_fest.day}/{hd_fest.year}",
            "Global_Start_Alef_Slichos_Motzi": alef_start.isoformat(),
            "Global_End_Erev_YK_Candle": erev_yk_candle.isoformat(),
            "Excluded_Rosh_Hashanah": excluded_rosh_hashanah,
            "Excluded_Shabbos": excluded_shabbos,
            "In_Global_Window": in_global_window,
            #"RH_Weekday": rh_wd,
            #"Shabbos_Start": shabbos_start.isoformat(),
            #"Shabbos_End": shabbos_end.isoformat(),
            #"RH_Start": rh_start.isoformat(),
            #"RH_End": rh_end.isoformat(),
        }


