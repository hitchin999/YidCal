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
from .upcoming_holiday_sensor import UpcomingYomTovSensor
from .nine_days_sensor import NineDaysSensor
from .motzi_holiday_sensor import (
    MotzeiYomKippurSensor,
    MotzeiPesachSensor,
    MotzeiSukkosSensor,
    MotzeiShavuosSensor,
    MotzeiRoshHashanaSensor,
    MotzeiShivaUsorBTammuzSensor,
    MotzeiTishaBavSensor,
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
    """Round dt up to next minute if any seconds, else keep minute."""
    if dt.second >= 1:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


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
    "סוכות א׳":              "sukkos_1",
    "סוכות ב׳":              "sukkos_2",
    "סוכות א׳ וב׳":           "sukkos_1_2",
    "א׳ דחול המועד סוכות":     "chol_hamoed_sukkos_1",
    "ב׳ דחול המועד סוכות":     "chol_hamoed_sukkos_2",
    "ג׳ דחול המועד סוכות":      "chol_hamoed_sukkos_3",
    "ד׳ דחול המועד סוכות":      "chol_hamoed_sukkos_4",    
    "חול המועד סוכות":       "chol_hamoed_sukkos",
    "הושענא רבה":            "hoshanah_rabbah",
    "שמיני עצרת":            "shemini_atzeres",
    "שמחת תורה":             "simchas_torah",
    "מוצאי סוכות":            "motzei_sukkos",
    "ערב חנוכה":             "erev_chanukah",
    "חנוכה":                 "chanukah",
    "שובבים":               "shovavim",
    "שובבים ת\"ת":          "shovavim_tat",
    "צום עשרה בטבת":         "tzom_asura_beteves",
    "ט\"ו בשבט":             "tu_bishvat",
    "תענית אסתר":            "taanis_esther",
    "פורים":                "purim",
    "שושן פורים":           "shushan_purim",
    "ליל בדיקת חמץ":        "leil_bedikas_chumetz",
    "ערב פסח":              "erev_pesach",
    "פסח א׳":               "pesach_1",
    "פסח ב׳":               "pesach_2",
    "פסח א׳ וב׳":           "pesach_1_2",
    "א׳ דחול המועד פסח":      "chol_hamoed_pesach_1",
    "ב׳ דחול המועד פסח":      "chol_hamoed_pesach_2",
    "ג׳ דחול המועד פסח":      "chol_hamoed_pesach_3",
    "ד׳ דחול המועד פסח":      "chol_hamoed_pesach_4",
    "חול המועד פסח":        "chol_hamoed_pesach",
    "שביעי של פסח":         "shviei_shel_pesach",
    "אחרון של פסח":         "achron_shel_pesach",
    "מוצאי פסח":            "motzei_pesach",
    "ל\"ג בעומר":            "lag_baomer",
    "ערב שבועות":           "erev_shavuos",
    "שבועות א׳":             "shavuos_1",
    "שבועות ב׳":             "shavuos_2",
    "שבועות א׳ וב׳":          "shavuos_1_2",
    "מוצאי שבועות":           "motzei_shavuos",
    "צום שבעה עשר בתמוז":      "shiva_usor_btammuz",
    "מוצאי צום שבעה עשר בתמוז":  "motzei_shiva_usor_btammuz",
    "ערב תשעה באב":           "erev_tisha_bav",
    "תשעה באב":              "tisha_bav",
    "תשעה באב נדחה":          "tisha_bav_nidche",
    "מוצאי תשעה באב":         "motzei_tisha_bav",
    "ראש חודש":              "rosh_chodesh",
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

        # find next window start/end and round them
        next_start = next_end = None
        for i in range(32):
            d = today + timedelta(days=i)
            hd_d = HDateInfo(d, diaspora=self._diaspora)
            hd_d1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)
            yomd = hd_d.is_yom_tov
            yomd1 = hd_d1.is_yom_tov
            shd = d.weekday() == 5
            ev_sh = (d.weekday() == 4 and not yomd)
            ev_hol = (yomd1 and not shd)
            if not (ev_sh or ev_hol):
                continue

            cal_d = ZmanimCalendar(geo_location=self._geo, date=d)
            sunrise_d = cal_d.sunrise().astimezone(self._tz)
            sunset_d = cal_d.sunset().astimezone(self._tz)
            raw_start = sunrise_d - timedelta(minutes=72)
            raw_end = sunset_d - timedelta(minutes=self._candle)

            # skip if today’s window already past
            if d == today and now >= raw_end:
                continue

            next_start = round_half_up(raw_start)
            next_end = round_half_up(raw_end)
            break

        # build attributes
        attrs: dict[str, str | bool] = {
            "now": now.isoformat(),
            "is_erev_shabbos": is_erev_shabbos,
            "is_erev_holiday": is_erev_holiday,
            "blocked_erev_shabbos": blocked_shabbos,
            "blocked_erev_holiday": blocked_holiday,
            "is_yomtov_today": is_yomtov_today,
            "is_shabbos_today": is_shabbos_today,
        }
        if next_start and next_end:
            attrs.update({
                "next_erev_window_start": next_start.isoformat(),
                "next_erev_window_end": next_end.isoformat(),
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
        now = dt_util.now().astimezone(self._tz)
        today = now.date()

        if not self._geo:
            return

        # compute raw window start/end
        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        s_eve = cal_today.sunset().astimezone(self._tz)  # eve‐sunset for today
        # for multi‐day YT you'd compute start_date… but this is your original logic
        sunset_today = s_eve  # reused for brevity
        candle_time = sunset_today - timedelta(minutes=self._candle)

        # festival detection (unchanged from original)…
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        if is_yomtov:
            start_date = check_date
            while HDateInfo(start_date - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start_date -= timedelta(days=1)
            end_date = check_date
            while HDateInfo(end_date + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                end_date += timedelta(days=1)
            info = HDateInfo(start_date, diaspora=self._diaspora)
            festival_name = str(info.holidays[0])
        else:
            wd = today.weekday()
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                start_date = today - timedelta(days=1)
            else:
                days_to_friday = (4 - wd) % 7
                start_date = today + timedelta(days=days_to_friday)
            end_date = start_date + timedelta(days=1)
            festival_name = "שבת"

        # recompute eve/final for full window
        cal_eve = ZmanimCalendar(geo_location=self._geo, date=(start_date - timedelta(days=1) if is_yomtov else start_date))
        s_eve = cal_eve.sunset().astimezone(self._tz)
        cal_final = ZmanimCalendar(geo_location=self._geo, date=end_date)
        s_final = cal_final.sunset().astimezone(self._tz)

        raw_start = s_eve - timedelta(minutes=self._candle)
        raw_end = s_final + timedelta(minutes=self._havdalah)

        # rounding per your request
        window_start = round_half_up(raw_start)
        window_end = round_ceil(raw_end)

        in_window = window_start <= now < window_end

        self._attr_is_on = in_window
        erev = self.hass.states.get("binary_sensor.yidcal_erev")
        erev_attrs = erev.attributes if erev else {}

        self._attr_extra_state_attributes = {
            "now": now.isoformat(),
            "festival_name": festival_name if (festival_name == "שבת" and in_window) or festival_name != "שבת" else None,
            "is_erev_holiday": erev_attrs.get("is_erev_holiday", False),
            "is_erev_shabbos": erev_attrs.get("is_erev_shabbos", False),
            "is_yomtov": is_yomtov,
            "is_shabbos": (festival_name == "שבת" and in_window),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "in_window": in_window,
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
    
    entities: list[BinarySensorEntity] = [
        ShabbosMevorchimSensor(hass, helper, candle, havdalah),
        UpcomingShabbosMevorchimSensor(hass, helper),
        NoMeluchaSensor(hass, candle, havdalah),
        ErevHolidaySensor(hass, candle),
        NoMusicSensor(hass, candle, havdalah),
        UpcomingYomTovSensor(hass, candle, havdalah),
        NineDaysSensor(hass, candle, havdalah),
    ]
    if include_attrs:
        for name in SLUG_OVERRIDES:
            entities.append(HolidayAttributeBinarySensor(hass, name))

    async_add_entities(entities, update_before_add=True)
