#/config/custom_components/yidcal/binary_sensor.py
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.const import STATE_ON
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import (
    async_track_time_interval,
    async_track_state_change_event,
    async_track_time_change,
    async_track_sunset,
)
from homeassistant.core import HomeAssistant
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.util import dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from .zman_sensors import get_geo
from hdate import HDateInfo
from hdate.translator import set_language

set_language("he")

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from .yidcal_lib.helper import YidCalHelper
from .sensor import ShabbosMevorchimSensor, UpcomingShabbosMevorchimSensor
from .no_music_sensor import NoMusicSensor
from .upcoming_yomtov_sensor import UpcomingYomTovSensor
from .slichos_sensor import SlichosSensor 
from .nine_days_sensor import NineDaysSensor
from .motzi_holiday_sensor import (
    MotzeiYomKippurSensor,
    MotzeiPesachSensor,
    MotzeiSukkosSensor,
    MotzeiShavuosSensor,
    MotzeiRoshHashanaSensor,
    MotzeiShivaUsorBTammuzSensor,
    MotzeiTishaBavSensor,
    MotziSensor,
)

from .const import DOMAIN
from .config_flow import CONF_INCLUDE_ATTR_SENSORS
from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

# ─── Rounding helpers ────────────────────────────────────────────────────────
def round_half_up(dt: datetime) -> datetime:
    """Round dt to nearest minute: <30s floor, ≥30s ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)

def round_ceil(dt: datetime) -> datetime:
    # always bump to the next minute then strip seconds
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
    
# ─── Your override map ────────────────────────────────────────────────────────
SLUG_OVERRIDES: dict[str, str] = {
    "א׳ סליחות":             "alef_selichos",
    "ערב ראש השנה":          "erev_rosh_hashana",
    "ראש השנה א׳":           "rosh_hashana_1",
    "ראש השנה ב׳":           "rosh_hashana_2",
    "ראש השנה א׳ וב׳":       "rosh_hashana_1_2",
    "מוצאי ראש השנה":        "motzei_rosh_hashana",
    "צום גדליה":             "tzom_gedalia",
    "שלוש עשרה מדות":        "shlosh_asrei_midos",
    "ערב יום כיפור":          "erev_yom_kippur",
    "יום הכיפורים":          "yom_kippur",
    "מוצאי יום הכיפורים":      "motzei_yom_kippur",
    "ערב סוכות":             "erev_sukkos",
    "סוכות":                 "sukkos",
    "סוכות א׳":              "sukkos_1",
    "סוכות ב׳":              "sukkos_2",
    "סוכות א׳ וב׳":           "sukkos_1_2",
    "א׳ דחול המועד סוכות":     "chol_hamoed_sukkos_1",
    "ב׳ דחול המועד סוכות":     "chol_hamoed_sukkos_2",
    "ג׳ דחול המועד סוכות":      "chol_hamoed_sukkos_3",
    "ד׳ דחול המועד סוכות":      "chol_hamoed_sukkos_4",    
    "חול המועד סוכות":       "chol_hamoed_sukkos",
    "שבת חול המועד סוכות":      "shabbos_chol_hamoed_sukkos",
    "הושענא רבה":            "hoshanah_rabbah",
    "שמיני עצרת":            "shemini_atzeres",
    "שמחת תורה":             "simchas_torah",
    "שמיני עצרת/שמחת תורה":     "shemini_atzeres_simchas_torah",
    "מוצאי סוכות":            "motzei_sukkos",
    "אסרו חג סוכות":         "isri_chag_sukkos",
    "ערב חנוכה":             "erev_chanukah",
    "חנוכה":                 "chanukah",
    "זאת חנוכה":             "zos_chanukah",
    "שובבים":               "shovavim",
    "שובבים ת\"ת":          "shovavim_tat",
    "צום עשרה בטבת":         "tzom_asura_beteves",
    "ט\"ו בשבט":             "tu_bishvat",
    "תענית אסתר":            "taanis_esther",
    "פורים":                "purim",
    "שושן פורים":           "shushan_purim",
    "ליל בדיקת חמץ":        "leil_bedikas_chumetz",
    "ערב פסח":              "erev_pesach",
    "פסח":                   "pesach",
    "פסח א׳":               "pesach_1",
    "פסח ב׳":               "pesach_2",
    "פסח א׳ וב׳":           "pesach_1_2",
    "א׳ דחול המועד פסח":      "chol_hamoed_pesach_1",
    "ב׳ דחול המועד פסח":      "chol_hamoed_pesach_2",
    "ג׳ דחול המועד פסח":      "chol_hamoed_pesach_3",
    "ד׳ דחול המועד פסח":      "chol_hamoed_pesach_4",
    "חול המועד פסח":        "chol_hamoed_pesach",
    "שבת חול המועד פסח":        "shabbos_chol_hamoed_pesach",
    "שביעי של פסח":         "shviei_shel_pesach",
    "אחרון של פסח":         "achron_shel_pesach",
    "שביעי/אחרון של פסח":       "shviei_achron_shel_pesach",
    "מוצאי פסח":            "motzei_pesach",
    "אסרו חג פסח":          "isri_chag_pesach",
    "פסח שני":             "pesach_sheini",
    "ל\"ג בעומר":            "lag_baomer",
    "ערב שבועות":           "erev_shavuos",
    "שבועות א׳":             "shavuos_1",
    "שבועות ב׳":             "shavuos_2",
    "שבועות א׳ וב׳":          "shavuos_1_2",
    "מוצאי שבועות":           "motzei_shavuos",
    "אסרו חג שבועות":         "isri_chag_shavuos",
    "צום שבעה עשר בתמוז":      "shiva_usor_btammuz",
    "מוצאי צום שבעה עשר בתמוז":  "motzei_shiva_usor_btammuz",
    "ערב תשעה באב":           "erev_tisha_bav",
    "תשעה באב":              "tisha_bav",
    "תשעה באב נדחה":          "tisha_bav_nidche",
    "מוצאי תשעה באב":         "motzei_tisha_bav",
    "יום כיפור קטן":            "yom_kipur_kuten",
    "ראש חודש":              "rosh_chodesh",
    "שבת ראש חודש":          "shabbos_rosh_chodesh",
}

# ─── The fixed dynamic‐attribute binary sensor ────────────────────────────────

class HolidayAttributeBinarySensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """Mirrors one attribute from sensor.yidcal_holiday, with restore-on-reboot."""

    def __init__(self, hass: HomeAssistant, attr_name: str) -> None:
        super().__init__()
        self.hass = hass
        self.attr_name = attr_name

        # Display info
        self._attr_name = f"{attr_name}"
        slug = SLUG_OVERRIDES.get(attr_name) or (
            attr_name.lower().replace(" ", "_")
                      .replace("׳", "").replace('"', "")
        )
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_icon = "mdi:checkbox-marked-circle-outline"
        self._attr_extra_state_attributes = {}

    def _schedule_update(self, *_args) -> None:
        """Thread-safe scheduling of async_update on the event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    async def async_added_to_hass(self) -> None:
        """Restore, do an initial update, and register listeners."""
        await super().async_added_to_hass()

        # 1) Restore last known state
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == STATE_ON)

        # 2) One immediate update if source exists
        if self.hass.states.get("sensor.yidcal_holiday"):
            await self.async_update()

        # 3a) Update on attribute-change events (store unsubscribe)
        unsub_state = async_track_state_change_event(
            self.hass,
            "sensor.yidcal_holiday",
            self._schedule_update,
        )
        self._register_listener(unsub_state)

        # 3b) Poll once a minute (use base-class wrapper so unsubscribe is saved)
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now=None) -> None:
        """Fetch the latest binary state from sensor.yidcal_holiday's attributes."""
        src = self.hass.states.get("sensor.yidcal_holiday")
        self._attr_is_on = bool(src and src.attributes.get(self.attr_name, False))

class ErevHolidaySensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from alos ha-shachar until candle-lighting on Erev-Shabbos or any Erev-Yom-Tov."""
    _attr_name = "Erev"
    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__()
        slug = "erev"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset
        self._diaspora = True

        # timezone + placeholder geo
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._geo: GeoLocation | None = None
        self._attr_extra_state_attributes: dict[str, str | bool] = {}
        self._havdalah = cfg.get("havdalah_offset", 72)

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == STATE_ON)

        # load geo once
        self._geo = await get_geo(self.hass)
        # initial calculation
        await self.async_update()

        # poll every minute
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        today = now.date()

        if not self._geo:
            return

        # compute raw dawn & candle thresholds
        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        sunrise = cal_today.sunrise().astimezone(self._tz)
        sunset = cal_today.sunset().astimezone(self._tz)

        raw_alos = sunrise - timedelta(minutes=72)
        raw_candle = sunset - timedelta(minutes=self._candle)

        # apply half-up rounding to on/off thresholds
        alos = round_half_up(raw_alos)
        candle = round_half_up(raw_candle)

        # festival flags
        hd_today = HDateInfo(today, diaspora=self._diaspora)
        is_yomtov_today = hd_today.is_yom_tov
        hd_tomorrow = HDateInfo(today + timedelta(days=1), diaspora=self._diaspora)
        is_yomtov_tomorrow = hd_tomorrow.is_yom_tov
        is_shabbos_today = (today.weekday() == 5)

        # raw conditions
        raw_erev_shabbos = today.weekday() == 4
        raw_erev_holiday = is_yomtov_tomorrow and not is_yomtov_today

        # suppressed conditions using rounded thresholds
        is_erev_shabbos = raw_erev_shabbos and not is_yomtov_today and now < candle
        is_erev_holiday = raw_erev_holiday and not is_shabbos_today and now >= alos

        # compute final on/off
        in_current = (alos <= now < candle)
        self._attr_is_on = in_current and (is_erev_shabbos or is_erev_holiday)

        # blocked debug flags
        blocked_shabbos = raw_erev_shabbos and is_yomtov_today
        blocked_holiday = raw_erev_holiday and is_shabbos_today

        # find next window start/end and round them (melacha-day only; no YT→YT, no Sat→YT)
        next_start = next_end = None

        for i in range(32):
            d       = today + timedelta(days=i)
            hd_d    = HDateInfo(d, diaspora=self._diaspora)
            hd_d1   = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)
            is_yt   = hd_d.is_yom_tov
            is_yt1  = hd_d1.is_yom_tov
            is_fri  = (d.weekday() == 4)
            is_sat  = (d.weekday() == 5)

            # Eligible Erev days (match the binary's ON logic):
            #  A) Erev Shabbos: Friday and NOT YT today
            #  B) Weekday → YT: NOT Saturday, NOT YT today, YT tomorrow
            is_erev_shabbos     = is_fri and not is_yt
            is_erev_hol_weekday = (not is_sat) and (not is_yt) and is_yt1

            if not (is_erev_shabbos or is_erev_hol_weekday):
                continue

            cal_d     = ZmanimCalendar(geo_location=self._geo, date=d)
            sunrise_d = cal_d.sunrise().astimezone(self._tz)
            sunset_d  = cal_d.sunset().astimezone(self._tz)

            raw_start = sunrise_d - timedelta(minutes=72)
            raw_end   = sunset_d  - timedelta(minutes=self._candle)  # end is before sunset for these cases

            # If this is today's window and we've already passed the end, skip to the next candidate
            if d == today and now >= raw_end:
                continue

            next_start = round_half_up(raw_start)
            next_end   = round_half_up(raw_end)
            break

        # --- Eruv Tavshilin (only when the upcoming YT span includes Friday) ---
        eruv_tavshilin = False
        if raw_erev_holiday:
            # Tomorrow starts a Yom Tov cluster; find its last day.
            span_start = today + timedelta(days=1)
            if HDateInfo(span_start, diaspora=self._diaspora).is_yom_tov:
                span_end = span_start
                while HDateInfo(span_end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                    span_end += timedelta(days=1)

                # Need Eruv if ANY day in the YT span is Friday (covers Thu–Fri→Shabbos and Fri–Shabbos)
                includes_friday = any(
                    (span_start + timedelta(days=i)).weekday() == 4
                    for i in range((span_end - span_start).days + 1)
                )

                if includes_friday:
                    # Attribute ON only during today's Erev-YT window: Alos → Shkiah
                    shkiah = round_half_up(sunset)
                    eruv_tavshilin = (alos <= now < shkiah)

        # ── Build attributes ──
        attrs: dict[str, str | bool] = {
            "Now": now.isoformat(),
            # Only true if we're inside the current Erev window AND this is the matching type
            "Is_Erev_Shabbos":  bool(self._attr_is_on and raw_erev_shabbos),
            "Is_Erev_Holiday":  bool(self._attr_is_on and raw_erev_holiday),

            "Blocked_Erev_Shabbos": blocked_shabbos,
            "Blocked_Erev_Holiday": blocked_holiday,
            "Is_Yom_Tov_Today":     is_yomtov_today,
            "Is_Shabbos_Today":     is_shabbos_today,
            "Eruv_Tavshilin":       eruv_tavshilin,
        }
        if next_start and next_end:
            attrs.update({
                "Next_Erev_Window_Start": next_start.isoformat(),
                "Next_Erev_Window_End": next_end.isoformat(),
            })
        self._attr_extra_state_attributes = attrs

class NoMeluchaSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from candle-lighting until havdalah on Shabbos & multi-day Yom Tov."""
    _attr_name = "No Melucha"
    _attr_icon = "mdi:briefcase-variant-off"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        slug = "no_melucha"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._diaspora = True
        self._candle = candle_offset
        self._havdalah = havdalah_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._geo: GeoLocation | None = None
        self._attr_extra_state_attributes: dict[str, str | bool] = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now: datetime | None = None) -> None:
        """Turn on from candle‑lighting Day 1 through havdalah on last day."""
        now = dt_util.now().astimezone(self._tz)
        today = now.date()
        if not self._geo:
            return

        festival_name = None
        raw_start = raw_end = None

        # 1) Scan Yom-Tov clusters that are ACTIVE now (contain `now`)
        for delta in range(-1, 32):
            d = today + timedelta(days=delta)
            hd = HDateInfo(d, diaspora=self._diaspora)

            # consider only the FIRST day of each contiguous festival cluster
            if not hd.is_yom_tov or HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                continue

            # find the end day of this contiguous festival
            end_d = d
            while HDateInfo(end_d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                end_d += timedelta(days=1)

            # cluster window: from candle-lighting before day 1 through havdalah after last day
            cal_prev = ZmanimCalendar(geo_location=self._geo, date=d - timedelta(days=1))
            start_dt = cal_prev.sunset().astimezone(self._tz) - timedelta(minutes=self._candle)

            cal_end = ZmanimCalendar(geo_location=self._geo, date=end_d)
            end_dt = cal_end.sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)

            # *** only choose this cluster if it's actually in effect now ***
            if start_dt <= now < end_dt:
                festival_name = str(hd.holidays[0])
                raw_start = start_dt
                raw_end = end_dt
                break

        # 2) If no Yom-Tov is ACTIVE now, fall back to Shabbos
        if raw_start is None:
            wd = today.weekday()
            friday = today - timedelta(days=(wd - 4) % 7)
            saturday = friday + timedelta(days=1)

            cal_f = ZmanimCalendar(geo_location=self._geo, date=friday)
            start_dt = cal_f.sunset().astimezone(self._tz) - timedelta(minutes=self._candle)

            cal_s = ZmanimCalendar(geo_location=self._geo, date=saturday)
            end_dt = cal_s.sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)

            # If that Shabbos already ended, move to NEXT Shabbos (upcoming)
            if now >= end_dt:
                friday += timedelta(days=7)
                saturday += timedelta(days=7)
                cal_f = ZmanimCalendar(geo_location=self._geo, date=friday)
                start_dt = cal_f.sunset().astimezone(self._tz) - timedelta(minutes=self._candle)
                cal_s = ZmanimCalendar(geo_location=self._geo, date=saturday)
                end_dt = cal_s.sunset().astimezone(self._tz) + timedelta(minutes=self._havdalah)

            festival_name = "שבת"
            raw_start = start_dt
            raw_end = end_dt


        # 3) Round thresholds and decide state
        window_start = round_half_up(raw_start)
        window_end   = round_ceil(raw_end)
        in_window    = (window_start <= now < window_end)

        self._attr_is_on = in_window

        active_festival   = festival_name if in_window else None
        upcoming_festival = None if in_window else festival_name

        self._attr_extra_state_attributes = {
            "Now":                   now.isoformat(),
            "Active_Festival_Name":  active_festival,
            "Upcoming_Festival_Name": upcoming_festival,
            "Window_Start":          window_start.isoformat(),
            "Window_End":            window_end.isoformat(),
            "In_Window":             in_window,
            "Is_Yom_Tov":            (active_festival is not None and active_festival != "שבת"),
            "Is_Shabbos":            (active_festival == "שבת"),
        }

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    cfg = hass.data[DOMAIN][entry.entry_id]
    candle = cfg["candlelighting_offset"]
    havdalah = cfg["havdalah_offset"]
    include_attrs = entry.options.get(
        CONF_INCLUDE_ATTR_SENSORS,
        cfg.get(CONF_INCLUDE_ATTR_SENSORS, True),
    )

    helper = YidCalHelper(hass.config)
    helper._candle_offset   = candle
    helper._havdalah_offset = havdalah
    
    entities: list[BinarySensorEntity] = [
        ShabbosMevorchimSensor(hass, helper, candle, havdalah),
        UpcomingShabbosMevorchimSensor(hass, helper),
        NoMeluchaSensor(hass, candle, havdalah),
        ErevHolidaySensor(hass, candle),
        SlichosSensor(hass, candle, havdalah),
        NoMusicSensor(hass, candle, havdalah),
        UpcomingYomTovSensor(hass, candle, havdalah),
        NineDaysSensor(hass, candle, havdalah),
        MotziSensor(hass, candle, havdalah),
    ]
    if include_attrs:
        for name in SLUG_OVERRIDES:
            entities.append(HolidayAttributeBinarySensor(hass, name))

    async_add_entities(entities, update_before_add=True)
