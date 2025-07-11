#/config/custom_components/yidcal/sensor.py
from __future__ import annotations
import logging
import homeassistant.util.dt as dt_util
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDevice

from astral import LocationInfo
from astral.sun import sun

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_state_change_event,
    async_track_sunset,
    async_track_time_change,
)

from hdate.converters import gdate_to_jdn
from hdate.hebrew_date import HebrewDate as HHebrewDate

import pyluach.dates as pdates
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .yidcal_lib.helper import YidCalHelper, MoladDetails
from .yidcal_lib.sfirah_helper import SfirahHelper
from .sfirah_sensor import SefirahCounter, SefirahCounterMiddos
from .special_shabbos_sensor import SpecialShabbosSensor
from .parsha_sensor import ParshaSensor
from .date_sensor import DateSensor
from .perek_avot_sensor import PerekAvotSensor
from .holiday_sensor import HolidaySensor
from .full_display_sensor import FullDisplaySensor
from .morid_tal_sensors import MoridGeshemSensor, TalUMatarSensor
from .special_prayer_sensor import SpecialPrayerSensor
from .zman_sensors import ZmanErevSensor, ZmanMotziSensor
from .zman_krias_shma_mga import SofZmanKriasShmaMGASensor
from .zman_chatzos_hayom import ChatzosHayomSensor
from .zman_alos import AlosSensor
from .zman_tefilah_mga import SofZmanTefilahMGASensor
from .zman_netz import NetzSensor
from .zman_tefilah_gra import SofZmanTefilahGRASensor
from .zman_krias_shma_gra import SofZmanKriasShmaGRASensor
from .zman_talis_tefilin import ZmanTalisTefilinSensor
from .zman_mincha_gedola import MinchaGedolaSensor
from .zman_mincha_ketana import MinchaKetanaSensor
from .zman_plag_hamincha import PlagHaMinchaSensor
from .zman_shkia import ShkiaSensor
from .zman_maariv_60 import ZmanMaariv60Sensor
from .zman_maariv_rt  import ZmanMaarivRTSensor
from .zman_chatzos_haleila import ChatzosHaLailaSensor
from .tehilim_daily_sensor import TehilimDailySensor
from .day_label_hebrew import DayLabelHebrewSensor
from .ishpizin_sensor import IshpizinSensor
from .zman_chumetz import (
    SofZmanAchilasChumetzSensor,
    SofZmanSriefesChumetzSensor,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


DAY_MAPPING = {
    "Sunday": "זונטאג",
    "Monday": "מאנטאג",
    "Tuesday": "דינסטאג",
    "Wednesday": "מיטוואך",
    "Thursday": "דאנערשטאג",
    "Friday": "פרייטאג",
    "Shabbos": "שבת",
}

ENG2HEB = {
    "Nisan":   "ניסן",
    "Iyar":    "אייר",
    "Sivan":   "סיון",
    "Tammuz":  "תמוז",
    "Av":      "אב",
    "Elul":    "אלול",
    "Tishrei": "תשרי",
    "Cheshvan":"חשון",
    "Kislev":  "כסלו",
    "Tevet":   "טבת",
    "Shvat":   "שבט",
    "Adar":    "אדר",     # when it’s a 12-month year
    "Adar I":  "אדר א",   # leap year month 12
    "Adar II": "אדר ב",   # leap year month 13
}



TIME_OF_DAY = {
    "am": lambda h: "פארטאגס" if h < 6 else "צופרי",
    "pm": lambda h: "נאכמיטאג" if h < 6 else "ביינאכט",
}



async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up YidCal and related sensors with user-configurable offsets."""
    yidcal_helper = YidCalHelper(hass.config)

    # Pull user-configured offsets
    opts = hass.data[DOMAIN][entry.entry_id]
    candle_offset = opts.get("candlelighting_offset", 15)
    havdalah_offset = opts.get("havdalah_offset", 72)

    # Prepare helpers
    sfirah_helper = SfirahHelper(hass, havdalah_offset)
    strip_nikud = entry.options.get("strip_nikud", False)

    async_add_entities([
        MoladSensor(hass, yidcal_helper, candle_offset, havdalah_offset),
        DayLabelYiddishSensor(hass, candle_offset, havdalah_offset),
        SpecialShabbosSensor(),
        SefirahCounter(hass, sfirah_helper, strip_nikud, havdalah_offset),
        SefirahCounterMiddos(hass, sfirah_helper, strip_nikud, havdalah_offset),
        RoshChodeshToday(hass, yidcal_helper, havdalah_offset),
        ParshaSensor(hass),
        DateSensor(hass, havdalah_offset),
        PerekAvotSensor(hass),
        HolidaySensor(hass, candle_offset, havdalah_offset),
        FullDisplaySensor(hass),
        MoridGeshemSensor(hass, yidcal_helper),
        TalUMatarSensor(hass, yidcal_helper, havdalah_offset),
        SpecialPrayerSensor(hass, candle_offset, havdalah_offset),
        ZmanErevSensor(hass, candle_offset, havdalah_offset),
        ZmanMotziSensor(hass, candle_offset, havdalah_offset),
        SofZmanKriasShmaMGASensor(hass),
        ChatzosHayomSensor(hass),
        AlosSensor(hass),
        SofZmanTefilahMGASensor(hass),
        NetzSensor(hass),
        SofZmanTefilahGRASensor(hass),
        SofZmanKriasShmaGRASensor(hass),
        ZmanTalisTefilinSensor(hass),
        MinchaGedolaSensor(hass),
        MinchaKetanaSensor(hass),
        PlagHaMinchaSensor(hass),
        ShkiaSensor(hass),
        ZmanMaariv60Sensor(hass),
        ZmanMaarivRTSensor(hass),
        ChatzosHaLailaSensor(hass),
        TehilimDailySensor(hass, yidcal_helper),
        DayLabelHebrewSensor(hass, candle_offset, havdalah_offset),
        SofZmanAchilasChumetzSensor(hass, candle_offset, havdalah_offset),
        SofZmanSriefesChumetzSensor(hass, candle_offset, havdalah_offset),
        IshpizinSensor(hass, havdalah_offset),
    ], update_before_add=True)



class MoladSensor(YidCalDevice, SensorEntity):
    _attr_name = "Molad"

    def __init__(
        self,
        hass: HomeAssistant,
        helper: YidCalHelper,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "molad"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_native_value = None
        self._attr_extra_state_attributes: dict[str, any] = {}


    async def _handle_minute_tick(self, now):
        """Called every minute by async_track_time_interval."""
        await self.async_update()
        
    async def async_added_to_hass(self) -> None:
        """Register immediate update and once-per-minute polling."""
        await super().async_added_to_hass()

        # Immediate first update
        await self.async_update()

        # Schedule async_update() once per minute via base-class wrapper
        self._register_interval(
            self.hass,
            self._handle_minute_tick,
            timedelta(minutes=1),
        )

    async def async_update(self, now=None) -> None:
        # ───────────────────────────────────────────────────────
        # 1) Use Home Assistant’s clock for “today”
        # ───────────────────────────────────────────────────────
        today = dt_util.now().date()
        jdn = gdate_to_jdn(today)
        heb = HHebrewDate.from_jdn(jdn)

        # Choose base_date exactly as before
        if heb.day < 3:
            base_date = today - timedelta(days=15)
        else:
            base_date = today

        try:
            details: MoladDetails = self.helper.get_molad(base_date)
        except Exception as e:
            _LOGGER.error("Molad update failed: %s", e)
            self._attr_native_value = None
            return

        m = details.molad
        h, mi = m.hours, m.minutes
        tod = TIME_OF_DAY[m.am_or_pm](h)
        chal = m.chalakim
        chal_txt = "חלק" if chal == 1 else "חלקים"
        hh12 = h % 12 or 12

        tz = ZoneInfo(self.hass.config.time_zone)
        if now:
            now_local = now.astimezone(tz)
        else:
            now_local = dt_util.now().astimezone(tz)

        loc = LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.hass.config.time_zone,
        )

        motzei = False
        if m.day == "Shabbos":
            sd = sun(loc.observer, date=now_local.date(), tzinfo=tz)
            motzei_start = sd["sunset"] + timedelta(minutes=self._havdalah_offset)
            next_mid = (now_local.replace(hour=0, minute=0) + timedelta(days=1)).replace(tzinfo=tz)
            motzei = motzei_start <= now_local < next_mid

        if motzei:
            day_yd = 'מוצש"ק'
            state = f"מולד {day_yd}, {mi} מינוט און {chal} {chal_txt} נאך {hh12}"
        else:
            day_yd = DAY_MAPPING.get(m.day, m.day)
            state = f"מולד {day_yd} {tod}, {mi} מינוט און {chal} {chal_txt} נאך {hh12}"

        self._attr_native_value = state

        # ───────────────────────────────────────────────────────
        # 2) Rosh Chodesh attributes (unchanged)
        # ───────────────────────────────────────────────────────
        rc = details.rosh_chodesh
        rc_mid = [f"{gd.isoformat()}T00:00:00Z" for gd in rc.gdays]

        rc_night = []
        for gd in rc.gdays:
            prev = gd - timedelta(days=1)
            sd = sun(loc.observer, date=prev, tzinfo=tz)
            rc_night.append((sd["sunset"] + timedelta(minutes=self._havdalah_offset)).isoformat())

        rc_days = [DAY_MAPPING.get(d, d) for d in rc.days]
        rc_text = rc_days[0] if len(rc_days) == 1 else " & ".join(rc_days)
        

        # ───────────────────────────────────────────────────────
        # 3) Compute the molad’s Hebrew‐month via pyluach (fixed)
        # ───────────────────────────────────────────────────────
        hd = PHebrewDate.from_pydate(today)
        if hd.day < 3:
            target_year, target_month = hd.year, hd.month
        else:
            try:
                PHebrewDate(hd.year, hd.month + 1, 1)
                target_year, target_month = hd.year, hd.month + 1
            except ValueError:
                target_year, target_month = hd.year + 1, 1

        # 2) Grab pyluach’s English‐string (CALL month_name())
        english_month = PHebrewDate(target_year, target_month, 1).month_name()
        #     e.g. "Av", "Adar", "Adar I", or "Adar II"

        # 3) Translate to Hebrew
        molad_month_name = ENG2HEB.get(english_month, english_month)

        self._attr_extra_state_attributes = {
            "day": day_yd,
            "hours": h,
            "minutes": mi,
            "time_of_day": tod,
            "chalakim": chal,
            "friendly": state,
            #"rosh_chodesh_midnight": rc_mid,
            "rosh_chodesh_nightfall": rc_night,
            "rosh_chodesh": rc_text,
            "rosh_chodesh_days": rc_days,
            "is_shabbos_mevorchim": details.is_shabbos_mevorchim,
            "is_upcoming_shabbos_mevorchim": details.is_upcoming_shabbos_mevorchim,
            "month_name": molad_month_name,  # now a string, not a method
        }


    def update(self) -> None:
        self.hass.async_create_task(self.async_update())

    @property
    def icon(self) -> str:
        return "mdi:calendar-star"


class DayLabelYiddishSensor(YidCalDevice, SensorEntity):
    """Sensor for standalone day label in Yiddish."""

    _attr_name = "Day Label Yiddish"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "day_label_yiddish"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._state: str | None = None

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.hass.config.time_zone,
        )
        current = datetime.now(tz)
        s = sun(loc.observer, date=current.date(), tzinfo=tz)
        candle = s["sunset"] - timedelta(minutes=self._candle)
        havdalah = s["sunset"] + timedelta(minutes=self._havdalah)

        # Hebrew date
        g = pdates.GregorianDate(current.year, current.month, current.day)
        hdate = g.to_heb()
        if current >= s["sunset"]:
            hdate = PHebrewDate(hdate.year, hdate.month, hdate.day) + 1

        # Holiday
        #is_tov = bool(hdate.festival(israel=False, include_working_days=False))

        # Shabbos
        wd = current.weekday()
        is_shab = (wd == 4 and current >= candle) or (wd == 5 and current < havdalah)

        if is_shab:
            lbl = "שבת קודש"
        #elif is_tov:
            #lbl = "יום טוב"
        elif wd == 4 and current.hour >= 12:
            lbl = 'ערש\"ק'
        elif wd == 5 and current >= havdalah:
            lbl = 'מוצש\"ק'
        else:
            days = ["זונטאג","מאנטאג","דינסטאג","מיטוואך","דאנערשטאג","פרייטאג","שבת"]
            idx = {6:0,0:1,1:2,2:3,3:4,4:5,5:6}[wd]
            lbl = days[idx]

        self._state = lbl

    async def async_added_to_hass(self) -> None:
        """Register initial update and hourly polling via async_track_time_interval."""
        await super().async_added_to_hass()

        # 1) Initial state calculation
        await self.async_update()

        # 2) Poll once every hour on the event loop
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )


class ShabbosMevorchimSensor(YidCalDevice, BinarySensorEntity):
    _attr_name = "Shabbos Mevorchim"

    def __init__(
        self,
        hass: HomeAssistant,
        helper: YidCalHelper,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "shabbos_mevorchim"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self.helper = helper
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset

        self._attr_is_on = False

    async def async_added_to_hass(self) -> None:
        """Register update triggers (hourly + sunset offsets)."""
        await super().async_added_to_hass()

        # 1) Immediate update on startup
        await self.async_update()

        # 2) Hourly refresh (on event loop)
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )

        # 3a) Sunset – candle_offset (on event loop)
        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=-self._candle_offset),
        )

        # 3b) Sunset + havdalah_offset (on event loop)
        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )
    async def async_update(self, now: datetime | None = None) -> None:
        """ON from Fri candle-lighting until Sat havdalah, only for a Mevorchim Shabbos."""
        try:
            tz = ZoneInfo(self.hass.config.time_zone)
            today_date = date.today()
            weekday = today_date.weekday()

            # 1) Check if this is the right Shabbos:
            is_mev = False
            if weekday == 4:  # Friday → tomorrow is Saturday
                is_mev = self.helper.is_shabbos_mevorchim(today_date + timedelta(days=1))
            elif weekday == 5:  # Saturday itself
                is_mev = self.helper.is_shabbos_mevorchim(today_date)

            if not is_mev:
                self._attr_is_on = False
                return

            # 2) Compute on/off times
            loc = LocationInfo(
                latitude=self.hass.config.latitude,
                longitude=self.hass.config.longitude,
                timezone=self.hass.config.time_zone,
            )
            # Friday sunset
            sun_today = sun(loc.observer, date=today_date, tzinfo=tz)
            fri_sunset = sun_today["sunset"]

            # Saturday sunset
            sat_date = today_date + timedelta(days=(5 - weekday))
            sun_sat = sun(loc.observer, date=sat_date, tzinfo=tz)
            sat_sunset = sun_sat["sunset"]

            # ON at Friday candle-lighting
            on_time = fri_sunset - timedelta(minutes=self._candle_offset)
            # OFF at Saturday havdalah
            off_time = sat_sunset + timedelta(minutes=self._havdalah_offset)

            now_local = (now or datetime.now(tz))

            self._attr_is_on = (on_time <= now_local < off_time)

        except Exception as e:
            _LOGGER.error("ShabbosMevorchim failed: %s", e)
            self._attr_is_on = False

    @property
    def icon(self) -> str:
        return "mdi:star-outline"


class UpcomingShabbosMevorchimSensor(YidCalDevice, BinarySensorEntity):
    _attr_name = "Upcoming Shabbos Mevorchim"
    _attr_unique_id = "yidcal_upcoming_shabbos_mevorchim"
    _attr_entity_id = "binary_sensor.yidcal_upcoming_shabbos_mevorchim"

    def __init__(self, hass: HomeAssistant, helper: YidCalHelper) -> None:
        super().__init__()
        slug = "upcoming_shabbos_mevorchim"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._attr_is_on = False
        
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # 1) Initial state
        await self.async_update()

        # 2) Poll every hour
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )

        # 3) Also re-check at Friday candle-lighting
        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=-self.helper._candle_offset),
        )
    
        
    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        today = date.today()
        flag = self.helper.get_molad(today).is_upcoming_shabbos_mevorchim

        if flag and today.weekday() == 4:
            # after Friday candle-lighting → go OFF
            loc = LocationInfo(
                latitude=self.hass.config.latitude,
                longitude=self.hass.config.longitude,
                timezone=self.hass.config.time_zone,
            )
            sun_today = sun(loc.observer, date=today, tzinfo=tz)
            candle = sun_today["sunset"] - timedelta(minutes=self.helper._candle_offset)
            now_local = (now or datetime.now(tz))
            if now_local >= candle:
                flag = False

        self._attr_is_on = bool(flag)

    @property
    def icon(self) -> str:
        return "mdi:star-outline"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # 1) Initial state
        await self.async_update()

        # 2) Poll every hour on the event loop
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )

class RoshChodeshToday(YidCalDevice, SensorEntity):
    """True during each day of Rosh Chodesh; shows א׳/ב׳ when there are two days."""

    _attr_name = "Rosh Chodesh Today"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, hass: HomeAssistant, helper: YidCalHelper, havdalah_offset: int) -> None:
        super().__init__()
        slug = "rosh_chodesh_today"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._havdalah_offset = havdalah_offset
        self._attr_native_value = None

    # ──────────────────────────────
    # Set up listeners
    # ──────────────────────────────
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # 1) Immediate update
        await self.async_update()

        # 2) Hourly refresh on event loop
        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )

        # 3) Anytime "sensor.yidcal_molad" changes, run async_update() on the event loop
        async_track_state_change_event(
            self.hass,
            ["sensor.yidcal_molad"],
            self._handle_molad_change,  # see below
        )

        # 4) Sunset + havdalah offset (on event loop)
        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )
        
    async def _handle_molad_change(self, event) -> None:
        """Called when the Molad sensor’s state changes—just re‐run async_update."""
        await self.async_update()

    # ──────────────────────────────
    # Core calculation
    # ──────────────────────────────
    async def async_update(self, _now: datetime | None = None) -> None:
        """Compute whether *now* is inside any Rosh-Chodesh interval."""
        tz = ZoneInfo(self.hass.config.time_zone)
        now = _now or datetime.now(tz)

        main = self.hass.states.get("sensor.yidcal_molad")
        attr = main.attributes if main else {}
        nf_list = attr.get("rosh_chodesh_nightfall") or []
        month = attr.get("month_name", "")

        # Convert strings → datetime
        nf_datetimes: list[datetime] = [
            dt if isinstance(dt, datetime) else datetime.fromisoformat(dt)
            for dt in nf_list
        ]

        # Find which Rosh Chodesh period (if any) is active
        active_index: int | None = None
        for i, start in enumerate(nf_datetimes):
            end = (
                nf_datetimes[i + 1]
                if i + 1 < len(nf_datetimes)
                else start + timedelta(days=1)
            )
            if start <= now < end:
                active_index = i
                break

        # Build the display string
        if active_index is not None:
            if len(nf_datetimes) == 1:
                val = f"ראש חודש {month}"
            else:
                prefix = ("א", "ב")[active_index] + "׳"
                val = f"{prefix} ד׳ראש חודש {month}"
        else:
            val = "Not Rosh Chodesh Today"

        self._attr_native_value = val

    # ──────────────────────────────
    # Availability
    # ──────────────────────────────
    @property
    def available(self) -> bool:
        main = self.hass.states.get("sensor.yidcal_molad")
        return bool(main and main.attributes.get("rosh_chodesh_nightfall"))
