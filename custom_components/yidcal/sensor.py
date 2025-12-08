#/config/custom_components/yidcal/sensor.py
from __future__ import annotations
import logging
import homeassistant.util.dt as dt_util
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDevice, YidCalDisplayDevice
from zmanim.zmanim_calendar import ZmanimCalendar
from .zman_sensors import get_geo

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
from .date_sensor import DateSensor, ChodeshSensor, YomLChodeshSensor
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
from .zman_plag_hamincha_mga import PlagHaMinchaMGASensor
from .zman_plag_hamincha_gra import PlagHaMinchaGRASensor
from .zman_shkia import ShkiaSensor
from .zman_maariv_60 import ZmanMaariv60Sensor
from .zman_maariv_rt  import ZmanMaarivRTSensor
from .zman_chatzos_haleila import ChatzosHaLailaSensor
from .tehilim_daily_sensor import TehilimDailySensor
from .tehilim_daily_pupa_sensor import TehilimDailyPupaSensor
from .day_label_hebrew import DayLabelHebrewSensor
from .ishpizin_sensor import IshpizinSensor
from .day_type import DayTypeSensor
from .zman_tzeis import ZmanTziesSensor
from .upcoming_holiday_sensor import UpcomingHolidaySensor
from .fast_timer_sensors import FastStartCountdownSensor, FastEndCountdownSensor
from .friday_is_rosh_chodesh_sensor import FridayIsRoshChodeshSensor
from .early_shabbos_yt_start_time_sensor import EarlyShabbosYtStartTimeSensor
from .haftorah_sensor import HaftorahSensor

from .yurtzeit_sensor import (
    YurtzeitSensor,
    YurtzeitWeeklySensor,
)
from .zman_chumetz import (
    SofZmanAchilasChumetzSensor,
    SofZmanSriefesChumetzSensor,
)
from .const import DOMAIN
from .config_flow import (
    CONF_ENABLE_WEEKLY_YURTZEIT,
    CONF_ENABLE_YURTZEIT_DAILY,
    CONF_YURTZEIT_DATABASES,
    CONF_YAHRTZEIT_DATABASE,   # legacy fallback
    DEFAULT_YAHRTZEIT_DATABASE,
    CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS,
    CONF_ENABLE_EARLY_YOMTOV,  DEFAULT_ENABLE_EARLY_YOMTOV,
)

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
    "Teves":   "טבת",
    "Shvat":   "שבט",
    "Adar":    "אדר",     # when it’s a 12-month year
    "Adar I":  "אדר א",   # leap year month 12
    "Adar II": "אדר ב",   # leap year month 13
}

def _round_half_up(dt: datetime) -> datetime:
    """Round dt to nearest minute: <30s floor, ≥30s ceil (matches Zman Erev)."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    """Always bump to the next minute (matches Zman Motzi)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
def _molad_time_of_day_jerusalem(jer_dt: datetime, jer_tzeis: datetime) -> str:
    """
    Yiddish time-of-day label based on JERUSALEM clock.

    - AM: hour buckets (Jerusalem hour).
    - PM: 'ביינאכט' only after Jerusalem tzeis; before that is 'נאכמיטאג'.
    """
    hour = jer_dt.hour

    # Morning side (Jerusalem)
    if hour < 12:
        if hour < 6:
            return "פארטאגס"
        if hour < 9:
            return "אינדערפרי"
        return "פארמיטאג"

    # PM side (Jerusalem)
    if jer_dt < jer_tzeis:
        return "נאכמיטאג"
    return "ביינאכט"

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up YidCal and related sensors with user-configurable offsets."""
    yidcal_helper = YidCalHelper(hass.config)

    # Pull user-configured offsets/options
    opts = hass.data[DOMAIN][entry.entry_id]
    candle_offset   = opts.get("candlelighting_offset", 15)
    havdalah_offset = opts.get("havdalah_offset", 72)

    # Map config-flow option → integration-wide diaspora flag
    from .config_flow import CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL
    is_in_israel = entry.options.get(
        CONF_IS_IN_ISRAEL,
        entry.data.get(CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL),
    )
    diaspora = not bool(is_in_israel)

    # Upcoming-holiday lookahead
    from .config_flow import CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS
    lookahead_days = opts.get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS)


    # Don’t overwrite the config prepared in __init__.py; only add missing aliases
    hass.data.setdefault(DOMAIN, {})
    cfg = hass.data[DOMAIN].setdefault("config", {})
    # Keep a single source of truth other sensors can read
    cfg["candlelighting_offset"] = candle_offset
    cfg["havdalah_offset"] = havdalah_offset
    cfg["candle_offset"] = candle_offset
    cfg["havdalah_offset"] = havdalah_offset
    cfg["diaspora"] = diaspora
    cfg["tzname"] = hass.config.time_zone

    # Prepare helpers
    sfirah_helper = await SfirahHelper.async_create(hass, havdalah_offset)
    strip_nikud = entry.options.get("strip_nikud", False)

    sensors = [
        MoladSensor(hass, yidcal_helper, candle_offset, havdalah_offset),
        DayLabelYiddishSensor(hass, candle_offset, havdalah_offset),
        SpecialShabbosSensor(),
        SefirahCounter(hass, sfirah_helper, strip_nikud, havdalah_offset),
        SefirahCounterMiddos(hass, sfirah_helper, strip_nikud, havdalah_offset),
        RoshChodeshToday(hass, yidcal_helper, havdalah_offset),
        ParshaSensor(hass),
        DateSensor(hass, havdalah_offset),
        ChodeshSensor(hass, havdalah_offset),
        YomLChodeshSensor(hass, havdalah_offset),
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
        PlagHaMinchaMGASensor(hass),
        PlagHaMinchaGRASensor(hass),
        ShkiaSensor(hass),
        ZmanMaariv60Sensor(hass),
        ZmanMaarivRTSensor(hass),
        ChatzosHaLailaSensor(hass),
        TehilimDailySensor(hass, yidcal_helper),
        TehilimDailyPupaSensor(hass, yidcal_helper),
        DayLabelHebrewSensor(hass, candle_offset, havdalah_offset),
        SofZmanAchilasChumetzSensor(hass, candle_offset, havdalah_offset),
        SofZmanSriefesChumetzSensor(hass, candle_offset, havdalah_offset),
        IshpizinSensor(hass, candle_offset, havdalah_offset),
        DayTypeSensor(hass, candle_offset, havdalah_offset),
        ZmanTziesSensor(hass, havdalah_offset),
        FastStartCountdownSensor(hass),
        FastEndCountdownSensor(hass),
        FridayIsRoshChodeshSensor(hass, yidcal_helper, havdalah_offset),
        HaftorahSensor(hass),
        UpcomingHolidaySensor(
            hass,
            candle_offset,
            havdalah_offset,
            lookahead_days=lookahead_days,
            horizon_days=14,
            update_interval_minutes=15,
        ),
    ]

    enable_es = entry.options.get(
        CONF_ENABLE_EARLY_SHABBOS,
        entry.data.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS),
    )
    enable_ey = entry.options.get(
        CONF_ENABLE_EARLY_YOMTOV,
        entry.data.get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV),
    )

    if enable_es or enable_ey:
        sensors.append(EarlyShabbosYtStartTimeSensor(hass, entry))

    # ─────────────────────────────────────────────────────────────
    # Yurtzeit sensors (per database, daily/weekly, legacy-safe)
    # ─────────────────────────────────────────────────────────────
    # Determine selected databases (new multi-select or legacy single)
    databases = (
        entry.options.get(CONF_YURTZEIT_DATABASES)
        or entry.data.get(CONF_YURTZEIT_DATABASES)
    )
    if not databases:
        legacy_db = (
            entry.options.get(CONF_YAHRTZEIT_DATABASE)
            or entry.data.get(CONF_YAHRTZEIT_DATABASE)
            or DEFAULT_YAHRTZEIT_DATABASE
        )
        databases = [legacy_db]

    enable_daily = entry.options.get(
        CONF_ENABLE_YURTZEIT_DAILY, entry.data.get(CONF_ENABLE_YURTZEIT_DAILY, True)
    )
    enable_weekly = entry.options.get(
        CONF_ENABLE_WEEKLY_YURTZEIT, entry.data.get(CONF_ENABLE_WEEKLY_YURTZEIT, False)
    )
    if enable_daily or enable_weekly:
        # Always keep legacy IDs for STANDARD, regardless of how many DBs are selected.
        # Satmar (or any other alt DBs) get suffixed entity_ids/unique_ids.
        # Ensure we add standard first (nice ordering) if present.
        ordered_dbs = []
        if "standard" in databases:
            ordered_dbs.append("standard")
        ordered_dbs += [db for db in databases if db != "standard"]

        if enable_daily:
            for db in ordered_dbs:
                sensors.append(
                    YurtzeitSensor(hass, havdalah_offset, database=db, legacy_ids=(db == "standard"))
                )
        if enable_weekly:
            for db in ordered_dbs:
                sensors.append(
                    YurtzeitWeeklySensor(hass, havdalah_offset, database=db, legacy_ids=(db == "standard"))
                )

    async_add_entities(sensors, update_before_add=True)

class MoladSensor(YidCalDisplayDevice, SensorEntity):
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
        # 1) Use Home Assistant’s clock (aware)
        tz = ZoneInfo(self.hass.config.time_zone)
        now_local = (now or dt_util.now()).astimezone(tz)
        today = now_local.date()
        
        # Local location (for RC nightfall etc.)
        loc = LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.hass.config.time_zone,
        )

        jdn = gdate_to_jdn(today)
        heb = HHebrewDate.from_jdn(jdn)

        # Choose base_date exactly as before (ONLY for molad/RC context)
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

        # ─── Shabbos Mevorchim: ON for the full Shabbos window (Fri candle → Sat havdalah) ───
        # Identify this Shabbos' Friday/Saturday and the window edges, then ask helper
        # if that Saturday is Mevorchim. Only then keep the flag ON for the whole window.
        is_mev_window = False
        in_shabbos_window = False
        wd = now_local.weekday()  # Mon=0 … Fri=4, Sat=5, Sun=6
        if wd in (4, 5):  # Friday or Saturday
            # Determine the Friday/Saturday pair for *this* Shabbos
            friday = today if wd == 4 else (today - timedelta(days=1))
            saturday = friday + timedelta(days=1)
        
            # Location for sun times
            loc = LocationInfo(
                latitude=self.hass.config.latitude,
                longitude=self.hass.config.longitude,
                timezone=self.hass.config.time_zone,
            )
            fri_sunset = sun(loc.observer, date=friday, tzinfo=tz)["sunset"]
            sat_sunset = sun(loc.observer, date=saturday, tzinfo=tz)["sunset"]
            shabbos_on  = fri_sunset - timedelta(minutes=self._candle_offset)
            shabbos_off = sat_sunset + timedelta(minutes=self._havdalah_offset)
        
            if shabbos_on <= now_local < shabbos_off:
                in_shabbos_window = True
                # Ask the helper about THIS Saturday (Gregorian) being Mevorchim
                is_mevorchim_this_week = self.helper.is_shabbos_mevorchim(saturday)
                is_mev_window = bool(is_mevorchim_this_week)
        
        # Forbid "upcoming" during the full Shabbos window; only compute it outside Shabbos
        if in_shabbos_window:
            is_upcoming_today = False
        else:
            is_upcoming_today = self.helper.is_upcoming_shabbos_mevorchim(today)

        m = details.molad
        h, mi = m.hours, m.minutes
        chal = m.chalakim
        chal_txt = "חלק" if chal == 1 else "חלקים"

        # Check if molad time is during motzei Shabbos (after havdalah) till Sunday 4am (Israel)
        is_special = False
        jer_tz = ZoneInfo("Asia/Jerusalem")
        jer_loc = LocationInfo(
            latitude=31.7683,
            longitude=35.2137,
            timezone="Asia/Jerusalem",
        )
        sd = sun(jer_loc.observer, date=m.date, tzinfo=jer_tz)
        jer_sunset = sd["sunset"]
        jer_tzeis = jer_sunset + timedelta(minutes=self._havdalah_offset)

        # Dynamic time-of-day label in Jerusalem
        tod_jer = _molad_time_of_day_jerusalem(m.dt, jer_tzeis)

        hav_end = jer_tzeis  # same boundary you were using, now named
        if m.day == "Shabbos" and m.dt >= hav_end:
            is_special = True
        elif m.day == "Sunday":
            four_am = datetime(
                m.date.year, m.date.month, m.date.day,
                4, 0,
                tzinfo=jer_tz
            )
            if m.dt < four_am:
                is_special = True

        # Friday-night special phrasing (JERUSALEM):
        # Jerusalem Friday after Jerusalem tzeis → "פרייטאג צונאכטס"
        friday_night = (
            not is_special
            and m.dt.weekday() == 4        # Friday in Jerusalem
            and m.dt >= jer_tzeis          # after Jerusalem tzeis
        )

        hh12 = h  # molad hour in Jerusalem
        if is_special:
            day_yd = 'מוצש"ק'
            tod_for_state = ""
        elif friday_night:
            day_yd = "פרייטאג"
            tod_for_state = "צונאכטס"
        else:
            day_yd = DAY_MAPPING.get(m.day, m.day)
            tod_for_state = tod_jer

        chal_phrase = "" if chal == 0 else f" און {chal} {'חלק' if chal == 1 else 'חלקים'}"

        if is_special:
            state = f"מולד {day_yd}, {mi} מינוט{chal_phrase} נאך {hh12}"
        else:
            state = f"מולד {day_yd} {tod_for_state}, {mi} מינוט{chal_phrase} נאך {hh12}"

        self._attr_native_value = state

        # 2) Rosh Chodesh attributes (unchanged)
        rc = details.rosh_chodesh
        rc_mid = [f"{gd.isoformat()}T00:00:00Z" for gd in rc.gdays]

        rc_night = []
        for gd in rc.gdays:
            prev = gd - timedelta(days=1)
            sd = sun(loc.observer, date=prev, tzinfo=tz)
            rc_night.append((sd["sunset"] + timedelta(minutes=self._havdalah_offset)).isoformat())

        rc_days = [DAY_MAPPING.get(d, d) for d in rc.days]
        rc_text = rc_days[0] if len(rc_days) == 1 else " & ".join(rc_days)

        # 3) Compute the molad’s Hebrew month name using the same rollover rules as helper
        hd = PHebrewDate.from_pydate(today)
        if hd.day < 3:
            target_year, target_month = hd.year, hd.month
        else:
            # Use helper’s next-month logic so we don’t mis-bump the year
            nxt = self.helper.get_next_numeric_month_year(today)
            target_year, target_month = nxt["year"], nxt["month"]

        molad_month_name = PHebrewDate(target_year, target_month, 1).month_name(True)
        
        # 4) Add Full_Molad attribute (use the same Yiddish phrasing as state)
        chal_phrase = "" if chal == 0 else f" און {chal} {'חלק' if chal == 1 else 'חלקים'}"

        if is_special:
            molad_part = f"מוצש\"ק, {mi} מינוט{chal_phrase} נאך {h}"
        else:
            molad_part = f"{day_yd} {tod_for_state}, {mi} מינוט{chal_phrase} נאך {h}"

        rc_text_yd = rc_days[0] if len(rc_days) == 1 else " און ".join(rc_days)
        if rc_days:
            full_molad = f"מולד חודש {molad_month_name} יהיה: {molad_part} - ראש חודש, {rc_text_yd}"
        else:
            full_molad = f"מולד חודש {molad_month_name} יהיה: {molad_part}"

        self._attr_extra_state_attributes = {
            "Day": day_yd,
            "Hours": h,
            "Minutes": mi,
            "Time_Of_Day": "" if is_special else tod_for_state,
            "Chalakim": chal,
            "Friendly": state,
            # "Rosh_Chodesh_Midnight": rc_mid,
            #"Rosh_Chodesh_Nightfall": rc_night,
            "Rosh_Chodesh": rc_text,
            "Rosh_Chodesh_Days": rc_days,
            # True for the ENTIRE Shabbos window only when it's a Mevorchim Shabbos
            "Is_Shabbos_Mevorchim": is_mev_window,
            "Is_Upcoming_Shabbos_Mevorchim": is_upcoming_today,
            "Month_Name": molad_month_name,
            "Full_Molad": full_molad,
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
        self._geo = None
        self._tz = ZoneInfo(self.hass.config.time_zone)

    @property
    def native_value(self) -> str | None:
        return self._state

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        current = (now or dt_util.now()).astimezone(self._tz)
        today = current.date()

        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=today)
            .sunset()
            .astimezone(self._tz)
        )

        raw_candle   = sunset - timedelta(minutes=self._candle)
        raw_havdalah = sunset + timedelta(minutes=self._havdalah)

        candle   = _round_half_up(raw_candle)
        havdalah = _round_ceil(raw_havdalah)

        wd = current.weekday()  # Mon=0 … Fri=4, Sat=5, Sun=6
        is_shab = (wd == 4 and current >= candle) or (wd == 5 and current < havdalah)

        if is_shab:
            lbl = "שבת קודש"
        elif wd == 4 and current.hour >= 12:
            lbl = 'ערש\"ק'
        elif wd == 5 and current >= havdalah:
            lbl = 'מוצש\"ק'
        else:
            days = ["זונטאג", "מאנטאג", "דינסטאג", "מיטוואך", "דאנערשטאג", "פרייטאג", "שבת"]
            idx = {6: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6}[wd]
            lbl = days[idx]

        self._state = lbl

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        await self.async_update()

        self._register_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah),
        )

        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
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
        self._geo = None
        self._tz = ZoneInfo(self.hass.config.time_zone)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        await self.async_update()

        async_track_time_interval(
            self.hass,
            self.async_update,
            timedelta(hours=1),
        )

        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=-self._candle_offset),
        )

        async_track_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )

        # NEW: keep in lockstep on the minute
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    async def async_update(self, now: datetime | None = None) -> None:
        try:
            if not self._geo:
                return

            now_local = (now or dt_util.now()).astimezone(self._tz)
            today = now_local.date()
            wd = today.weekday()  # 0=Mon … 4=Fri, 5=Sat

            if wd == 4:
                shabbos = today + timedelta(days=1)
                friday = today
                saturday = shabbos
            elif wd == 5:
                shabbos = today
                friday = today - timedelta(days=1)
                saturday = today
            else:
                self._attr_is_on = False
                return

            if not self.helper.is_shabbos_mevorchim(shabbos):
                self._attr_is_on = False
                return

            fri_sunset = (
                ZmanimCalendar(geo_location=self._geo, date=friday)
                .sunset()
                .astimezone(self._tz)
            )
            sat_sunset = (
                ZmanimCalendar(geo_location=self._geo, date=saturday)
                .sunset()
                .astimezone(self._tz)
            )

            raw_on  = fri_sunset - timedelta(minutes=self._candle_offset)
            raw_off = sat_sunset + timedelta(minutes=self._havdalah_offset)

            on_time  = _round_half_up(raw_on)
            off_time = _round_ceil(raw_off)

            self._attr_is_on = (on_time <= now_local < off_time)

        except Exception as e:
            _LOGGER.error("ShabbosMevorchim failed: %s", e)
            self._attr_is_on = False

    @property
    def icon(self) -> str:
        return "mdi:star-outline"

class UpcomingShabbosMevorchimSensor(YidCalDevice, BinarySensorEntity):
    _attr_name = "Upcoming Shabbos Mevorchim"

    def __init__(self, hass: HomeAssistant, helper: YidCalHelper, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        slug = "upcoming_shabbos_mevorchim"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id       = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_is_on = False
        self._geo = None
        self._tz = ZoneInfo(self.hass.config.time_zone)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        await self.async_update()

        async_track_time_interval(self.hass, self.async_update, timedelta(hours=1))

        async_track_sunset(self.hass, self.async_update, offset=timedelta(minutes=-self._candle_offset))
        async_track_sunset(self.hass, self.async_update, offset=timedelta(minutes=self._havdalah_offset))

        # NEW: lockstep minute beat
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()
        wd = today.weekday()

        # If we're inside Shabbos window, force OFF
        if wd in (4, 5):
            friday = today if wd == 4 else (today - timedelta(days=1))
            saturday = friday + timedelta(days=1)

            fri_sunset = (
                ZmanimCalendar(geo_location=self._geo, date=friday)
                .sunset()
                .astimezone(self._tz)
            )
            sat_sunset = (
                ZmanimCalendar(geo_location=self._geo, date=saturday)
                .sunset()
                .astimezone(self._tz)
            )

            raw_on  = fri_sunset - timedelta(minutes=self._candle_offset)
            raw_off = sat_sunset + timedelta(minutes=self._havdalah_offset)

            shabbos_on  = _round_half_up(raw_on)
            shabbos_off = _round_ceil(raw_off)

            if shabbos_on <= now_local < shabbos_off:
                self._attr_is_on = False
                return

        flag = self.helper.get_molad(today).is_upcoming_shabbos_mevorchim

        # After candles Friday, must be OFF
        if wd == 4:
            sunset_today = (
                ZmanimCalendar(geo_location=self._geo, date=today)
                .sunset()
                .astimezone(self._tz)
            )
            candle = _round_half_up(sunset_today - timedelta(minutes=self._candle_offset))
            if now_local >= candle:
                flag = False

        self._attr_is_on = bool(flag)

    @property
    def icon(self) -> str:
        return "mdi:star-outline"

class RoshChodeshToday(YidCalDisplayDevice, SensorEntity):
    """True during each day of Rosh Chodesh; shows א׳/ב׳ when there are two days."""

    _attr_name = "Rosh Chodesh Today"
    _attr_icon = "mdi:calendar-star"

    def __init__(self, hass: HomeAssistant, helper: YidCalHelper, havdalah_offset: int) -> None:
        super().__init__()
        slug = "rosh_chodesh_today"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self.helper = helper
        self._havdalah_offset = havdalah_offset
        self._attr_native_value = None

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._geo = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)

        await self.async_update()

        # Minute lockstep like DayType
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

        # Also re-evaluate at tzeis (sunset + offset)
        self._register_sunset(
            self.hass,
            self.async_update,
            offset=timedelta(minutes=self._havdalah_offset),
        )

        # Molad change can still force refresh
        self._register_listener(
            async_track_state_change_event(
                self.hass,
                ["sensor.yidcal_molad"],
                self._handle_molad_change,
            )
        )

    async def _handle_molad_change(self, event) -> None:
        await self.async_update()

    def _tzeis(self, d: date) -> datetime:
        """Rounded tzeis = sunset(d) + offset, Motzi-style."""
        sunset = (
            ZmanimCalendar(geo_location=self._geo, date=d)
            .sunset()
            .astimezone(self._tz)
        )
        return _round_ceil(sunset + timedelta(minutes=self._havdalah_offset))

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        # Match your Molad “base_date” rule near month start
        hd_now = PHebrewDate.from_pydate(today)
        base_date = (today - timedelta(days=15)) if hd_now.day < 3 else today

        try:
            rc = self.helper.get_rosh_chodesh_days(base_date)
            rc_gdays = list(rc.gdays or [])
        except Exception:
            rc_gdays = []

        if not rc_gdays:
            self._attr_native_value = "Not Rosh Chodesh Today"
            return

        # Month name from the RC day itself (pyluach Hebrew month name)
        month = PHebrewDate.from_pydate(rc_gdays[-1]).month_name(True)

        active_index: int | None = None

        # Each RC "day" is: tzeis(prev day) -> tzeis(this day)
        for i, gd in enumerate(rc_gdays):
            start = self._tzeis(gd - timedelta(days=1))
            end = self._tzeis(gd)
            if start <= now_local < end:
                active_index = i
                break

        if active_index is not None:
            if len(rc_gdays) == 1:
                val = f"ראש חודש {month}"
            else:
                prefix = ("א", "ב")[active_index] + "׳"
                val = f"{prefix} ד׳ראש חודש {month}"
        else:
            val = "Not Rosh Chodesh Today"

        self._attr_native_value = val

    @property
    def available(self) -> bool:
        return True
