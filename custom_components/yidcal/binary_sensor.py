from __future__ import annotations
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.helpers.device_registry import DeviceEntryType
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

from zmanim.util.geo_location import GeoLocation
from .zman_sensors import get_geo
from .yidcal_lib.zman_compute import (
    round_half_up,
    round_ceil,
    sun_events_for_date,
    sunset_for_date,
)
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
from .no_melucha_shabbos_sensor import NoMeluchaShabbosSensor
from .no_melucha_yomtov_sensor import NoMeluchaYomTovSensor
from .bishul_allowed_sensor import BishulAllowedSensor
from .longer_shachris_sensor import LongerShachrisSensor
from .eruv_tavshilin import EruvTavshilinSensor
from .bein_hazmanim_sensor import BeinHazmonimSensor
from .dst_sensor import DSTSensor
from .erev_after_chatzos_sensor import ErevAfterChatzosSensor
from .erev_tisha_bav_after_chatzos_sensor import ErevTishaBavAfterChatzosSensor
from .tisha_bav_night_sensor import TishaBavNightSensor
from .longer_shabbos_shachris_sensor import LongerShabbosSensor
from .three_day_yomtov_sensor import ThreeDayYomTovSensor
from .yomtov_tomorrow_sensor import YomTovTomorrowSensor
from .hebrew_year_sensors import (
    LeapYearSensor,
    LeapYearNextYearSensor,
    ShmitaSensor,
)
from .kiddush_levana_sensors import KiddushLevanaSensor
from .pruzbol_sensors import PruzbolSensor
from .motzi_holiday_sensor import (
    MotzeiYomKippurSensor,
    MotzeiPesachSensor,
    MotzeiSukkosSensor,
    MotzeiShavuosSensor,
    MotzeiRoshHashanaSensor,
    MotzeiShivaUsorBTammuzSensor,
    MotzeiTishaBavSensor,
    MotziSensor,
    MotzeiLagBaOmerSensor,
    MotzeiShushanPurimSensor,
    MotzeiChanukahSensor,
)

from .const import DOMAIN
from .holiday_sensor import HolidaySensor
from .config_flow import CONF_INCLUDE_ATTR_SENSORS
from .device import YidCalDevice

_LOGGER = logging.getLogger(__name__)

# (Rounding helpers now come from yidcal_lib.zman_compute — single source.)

# ─── Your override map ────────────────────────────────────────────────────────
SLUG_OVERRIDES: dict[str, str] = {
    "א׳ סליחות":             "alef_selichos",
    "ערב ראש השנה":          "erev_rosh_hashana",
    "ראש השנה א׳":           "rosh_hashana_1",
    "ראש השנה ב׳":           "rosh_hashana_2",
    "ראש השנה א׳ וב׳":       "rosh_hashana_1_2",
    "מוצאי ראש השנה":        "motzei_rosh_hashana",
    "עשרת ימי תשובה":        "aseres_yemei_teshuva",
    "צום גדליה":             "tzom_gedalia",
    "שלוש עשרה מדות":        "shlosh_asrei_midos",
    "ערב יום כיפור":          "erev_yom_kippur",
    "יום הכיפורים":          "yom_kippur",
    "מוצאי יום הכיפורים":      "motzei_yom_kippur",
    "ערב סוכות":             "erev_sukkos",
    "סוכות (כל חג)":         "sukkos",
    "סוכות א׳":              "sukkos_1",
    "סוכות ב׳":              "sukkos_2",
    "סוכות א׳ וב׳":           "sukkos_1_2",
    "א׳ דחול המועד סוכות":     "chol_hamoed_sukkos_1",
    "ב׳ דחול המועד סוכות":     "chol_hamoed_sukkos_2",
    "ג׳ דחול המועד סוכות":      "chol_hamoed_sukkos_3",
    "ד׳ דחול המועד סוכות":      "chol_hamoed_sukkos_4",
    "ה׳ דחול המועד סוכות":      "chol_hamoed_sukkos_5",
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
    "ערב שבת חנוכה":          "erev_shabbos_chanukah",
    "שבת חנוכה":             "shabbos_chanukah",
    "שבת חנוכה ראש חודש":    "shabbos_chanukah_rosh_chodesh", 
    "א׳ דחנוכה":             "chanukah_day_1",
    "ב׳ דחנוכה":             "chanukah_day_2",
    "ג׳ דחנוכה":             "chanukah_day_3",
    "ד׳ דחנוכה":             "chanukah_day_4",
    "ה׳ דחנוכה":             "chanukah_day_5",
    "ו׳ דחנוכה":             "chanukah_day_6",
    "ז׳ דחנוכה":             "chanukah_day_7",
    "זאת חנוכה":             "zos_chanukah",
    "מוצאי חנוכה":           "motzei_chanukah",
    "שובבים":               "shovavim",
    "שובבים ת\"ת":          "shovavim_tat",
    "צום עשרה בטבת":         "tzom_asura_beteves",
    "חמשה עשר בשבט":        "tu_bishvat",
    "תענית אסתר":            "taanis_esther",
    "תענית אסתר מוקדם":        "taanis_esther_mukdam",
    "פורים":                "purim",
    "שושן פורים":           "shushan_purim",
    "מוצאי שושן פורים":     "motzei_shushan_purim",
    "ערב בדיקת חמץ":        "erev_bedikas_chumetz",
    "ליל בדיקת חמץ":        "leil_bedikas_chumetz",
    "ערב פסח":              "erev_pesach",
    "ערב פסח מוקדם":       "erev_pesach_mukdam",
    "שבת ערב פסח":         "shabbos_erev_pesach",
    "פסח (כל חג)":         "pesach",
    "פסח א׳":               "pesach_1",
    "פסח ב׳":               "pesach_2",
    "פסח א׳ וב׳":           "pesach_1_2",
    "א׳ דחול המועד פסח":      "chol_hamoed_pesach_1",
    "ב׳ דחול המועד פסח":      "chol_hamoed_pesach_2",
    "ג׳ דחול המועד פסח":      "chol_hamoed_pesach_3",
    "ד׳ דחול המועד פסח":      "chol_hamoed_pesach_4",
    "ה׳ דחול המועד פסח":      "chol_hamoed_pesach_5",
    "חול המועד פסח":        "chol_hamoed_pesach",
    "שבת חול המועד פסח":        "shabbos_chol_hamoed_pesach",
    "שביעי של פסח":         "shviei_shel_pesach",
    "אחרון של פסח":         "achron_shel_pesach",
    "שביעי/אחרון של פסח":       "shviei_achron_shel_pesach",
    "מוצאי פסח":            "motzei_pesach",
    "אסרו חג פסח":          "isri_chag_pesach",
    "פסח שני":             "pesach_sheini",
    "ל\"ג בעומר":            "lag_baomer",
    "מוצאי ל\"ג בעומר":      "motzei_lag_baomer",
    "ערב שבועות":           "erev_shavuos",
    "שבועות א׳":             "shavuos_1",
    "שבועות ב׳":             "shavuos_2",
    "שבועות א׳ וב׳":          "shavuos_1_2",
    "מוצאי שבועות":           "motzei_shavuos",
    "אסרו חג שבועות":         "isri_chag_shavuos",
    "צום שבעה עשר בתמוז":      "shiva_usor_btammuz",
    "מוצאי צום שבעה עשר בתמוז":  "motzei_shiva_usor_btammuz",
    "ערב תשעה באב":           "erev_tisha_bav",
    "ערב תשעה באב שחל בשבת":  "erev_tisha_bav_shechal_beshabbos",
    "תשעה באב":              "tisha_bav",
    "תשעה באב נדחה":          "tisha_bav_nidche",
    "מוצאי תשעה באב":         "motzei_tisha_bav",
    "ט\"ו באב":                "tu_bav",
    "יום כיפור קטן":            "yom_kipur_kuten",
    "ראש חודש":              "rosh_chodesh",
    "שבת ראש חודש":          "shabbos_rosh_chodesh",
    "ערב שבת":                "erev_shabbos",
    "ערב יום טוב":            "erev_yom_tov",
    "מוצאי שבת":              "motzi_shabbos",
    "מוצאי יום טוב":          "motzi_yom_tov",
    "א׳ דיום טוב":            "first_day_yom_tov",
    "ב׳ דיום טוב":           "second_day_yom_tov",
    "ערב שבת שחל ביום טוב":   "erev_shabbos_shechal_byomtov",
    "ערב יום טוב שחל בשבת":   "erev_yomtov_shechal_beshabbos",
    "מוצאי שבת שחל ביום טוב": "motzi_shabbos_shechal_byomtov",
    "מוצאי יום טוב שחל בשבת": "motzi_yomtov_shechal_beshabbos",
    "שבת ערב פורים":          "shabbos_erev_purim",
}

# ─── The fixed dynamic‐attribute binary sensor ────────────────────────────────

class HolidayAttributeBinarySensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """Mirrors one attribute from sensor.yidcal_holiday, with restore-on-reboot."""

    # All attribute mirrors will live under this separate Device
    _ATTR_DEVICE_IDENT = (DOMAIN, "yidcal_holiday_attributes")

    def __init__(self, hass: HomeAssistant, attr_name: str) -> None:
        super().__init__()
        self.hass = hass
        self.attr_name = attr_name

        self._attr_name = f"{attr_name}"
        slug = SLUG_OVERRIDES.get(attr_name) or (
            attr_name.lower().replace(" ", "_").replace("׳", "").replace('"', "")
        )
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_icon = "mdi:checkbox-marked-circle-outline"
        self._attr_extra_state_attributes = {}

    @property
    def device_info(self):
        return {
            "identifiers": {self._ATTR_DEVICE_IDENT},
            "name": "YidCal — Holiday Attribute Sensors",
            "manufacturer": "Yoel Goldstein/Vaayer LLC", 
            "model": "Holiday Attribute Sensors",
            "entry_type": DeviceEntryType.SERVICE,
        }

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

        # 3) React instantly when melacha/holiday changes (no duplicate listeners)
        self._register_listener(
            async_track_state_change_event(
                self.hass,
                "binary_sensor.yidcal_no_melucha",
                self._schedule_update,
            )
        )
        self._register_listener(
            async_track_state_change_event(
                self.hass,
                "sensor.yidcal_holiday",
                self._schedule_update,
            )
        )

        # 4) Poll exactly on the minute as a safety net
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._schedule_update,
                second=0,  # aligns with 5:49:00, 5:50:00, ...
            )
        )

    async def async_update(self, now=None) -> None:
        """Fetch the latest binary state from sensor.yidcal_holiday's attributes."""
        src = self.hass.states.get("sensor.yidcal_holiday")
        val = src.attributes.get(self.attr_name, False) if src else False
        self._attr_is_on = str(val).lower() == "true"

        # Publish immediately, on the aligned :00 tick. Without this the
        # method only mutates in-memory attributes -- nothing reaches HA
        # until the entity platform's OWN poll (should_poll defaults to
        # True, ~30s cadence anchored to platform setup) calls async_update
        # again and writes. That poll is why flips were logged at :41 /
        # :44 / :48 instead of the rounded minute. It still runs, harmlessly:
        # it recomputes the same values and writes no change.
        if self.hass is not None and self.entity_id:
            self.async_write_ha_state()


class ErevHolidaySensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from alos ha-shachar until entry-time on Erev Shabbos or any Erev-Yom-Tov.
       Entry-time = candle-lighting unless Early Shabbos / Early YT provides an earlier effective start.
    """
    _attr_name = "Erev"
    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__()
        slug = "erev"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset

        # Pull Israel/Chutz setting from integration config
        cfg_root = hass.data.get(DOMAIN, {}) or {}
        cfg_conf = cfg_root.get("config", {}) or {}
        self._diaspora = cfg_conf.get("diaspora", True)

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._geo: GeoLocation | None = None
        self._attr_extra_state_attributes: dict[str, str | bool] = {}
        self._havdalah = cfg.get("havdalah_offset", 72)

        # Cache of the most recent *actual* erev window (start, end, candle ref)
        # so non-erev days can publish the previous window with the effective
        # (possibly early) end that was really used.
        self._last_window: dict[str, datetime] | None = None

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == STATE_ON)

            # Seed the last-window cache from the previously published attrs
            # (they already hold the correct effective window). async_update
            # only uses the cache if its date is the most recent erev day.
            attrs = last.attributes or {}
            w_start = self._parse_early_dt(attrs.get("Erev_Window_Start"))
            w_end = self._parse_early_dt(attrs.get("Erev_Window_End"))
            w_candle = self._parse_early_dt(attrs.get("Candle_End_Cut"))
            if (
                w_start and w_end and w_candle
                and w_start < w_end
                and w_start.date() == w_end.date() == w_candle.date()
            ):
                self._last_window = {
                    "start": w_start,
                    "end": w_end,
                    "candle": w_candle,
                }

        self._geo = await get_geo(self.hass)
        await self.async_update()

        self._register_listener(
            async_track_state_change_event(
                self.hass,
                "binary_sensor.yidcal_no_melucha",
                self._schedule_update,
            )
        )
        self._register_listener(
            async_track_state_change_event(
                self.hass,
                "sensor.yidcal_holiday",
                self._schedule_update,
            )
        )

        self._register_listener(
            async_track_time_change(
                self.hass,
                self._schedule_update,
                second=0,
            )
        )

    # ---------------- Early-start helpers ----------------

    def _get_early_maps(self) -> tuple[dict, dict]:
        early_state = self.hass.states.get("sensor.yidcal_early_shabbos_yt_start_time")
        if not early_state:
            return {}, {}

        attrs = early_state.attributes or {}

        def pick(*names):
            for n in names:
                if n in attrs and isinstance(attrs[n], dict):
                    return attrs[n]
            return {}

        eff_shabbos = pick(
            "Effective shabbos start by date",
            "Effective_Shabbos_Start_By_Date",
            "effective_shabbos_start_by_date",
        )
        eff_yomtov = pick(
            "Effective yomtov start by date",
            "Effective_Yomtov_Start_By_Date",
            "effective_yomtov_start_by_date",
        )
        return eff_shabbos or {}, eff_yomtov or {}

    def _parse_early_dt(self, val):
        if not val:
            return None
        try:
            if isinstance(val, datetime):
                dt_local = val
            else:
                dt_local = datetime.fromisoformat(str(val))
            if dt_local.tzinfo is None:
                dt_local = dt_local.replace(tzinfo=self._tz)
            return dt_local.astimezone(self._tz)
        except Exception:
            return None

    def _effective_erev_end(
        self,
        erev_date,
        candle_end_cut: datetime,
        is_friday: bool,
        is_erev_holiday: bool,
        eff_shabbos_map: dict,
        eff_yomtov_map: dict,
    ) -> datetime:
        """Return earliest of candle_end_cut and any applicable early-start cutoffs."""
        cuts = [candle_end_cut]
        key = erev_date.isoformat()

        if is_friday:
            early_val = eff_shabbos_map.get(key)
            early_dt = self._parse_early_dt(early_val)
            if early_dt:
                cuts.append(round_half_up(early_dt))

        if is_erev_holiday:
            early_val = eff_yomtov_map.get(key)
            early_dt = self._parse_early_dt(early_val)
            if early_dt:
                cuts.append(round_half_up(early_dt))

        return min(cuts)

    # ---------------- Main update ----------------

    def _most_recent_erev_date(self, today) -> "date | None":
        """Most recent day before today that was a valid erev-window day
        (same predicate as the next-window finder)."""
        for i in range(1, 33):
            d = today - timedelta(days=i)
            hd_d = HDateInfo(d, diaspora=self._diaspora)
            if hd_d.is_yom_tov:
                continue
            if d.weekday() == 5:
                continue
            if d.weekday() == 4:
                return d
            if HDateInfo(d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                return d
        return None

    def _compute_window_for(
        self,
        d,
        eff_shabbos_map: dict,
        eff_yomtov_map: dict,
    ) -> dict:
        """Window (start, effective end, regular candle ref) for erev day d,
        mirroring the next-window finder's computation (shared cached zmanim)."""
        sunrise_d, sunset_d = sun_events_for_date(geo=self._geo, tz=self._tz, base_date=d)

        start_cut = round_half_up(sunrise_d - timedelta(minutes=72))
        candle_end_cut = round_half_up(sunset_d - timedelta(minutes=self._candle))

        is_fri = (d.weekday() == 4)
        is_yt1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov

        eff_end = self._effective_erev_end(
            erev_date=d,
            candle_end_cut=candle_end_cut,
            is_friday=is_fri,
            is_erev_holiday=is_yt1,
            eff_shabbos_map=eff_shabbos_map,
            eff_yomtov_map=eff_yomtov_map,
        )
        return {"start": start_cut, "end": eff_end, "candle": candle_end_cut}

    async def async_update(self, now: datetime | None = None) -> None:
        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        today = now.date()
        if not self._geo:
            return

        eff_shabbos_map, eff_yomtov_map = self._get_early_maps()

        # compute raw dawn & candle thresholds (shared cached zmanim)
        sunrise, sunset = sun_events_for_date(geo=self._geo, tz=self._tz, base_date=today)

        raw_alos   = sunrise - timedelta(minutes=72)
        raw_candle = sunset  - timedelta(minutes=self._candle)

        alos       = round_half_up(raw_alos)
        candle_cut = round_half_up(raw_candle)

        # ── Festival/weekday facts + melacha status ──
        hd_today         = HDateInfo(today, diaspora=self._diaspora)
        is_yomtov_today  = hd_today.is_yom_tov
        hd_tomorrow      = HDateInfo(today + timedelta(days=1), diaspora=self._diaspora)
        is_yomtov_tomorrow = hd_tomorrow.is_yom_tov

        is_friday_civil  = (today.weekday() == 4)
        is_shabbos_civil = (today.weekday() == 5)

        is_no_melucha    = self.hass.states.is_state("binary_sensor.yidcal_no_melucha", "on")
        is_shabbos_today = is_no_melucha and not is_yomtov_today

        raw_erev_shabbos = is_friday_civil
        raw_erev_holiday = is_yomtov_tomorrow and not is_yomtov_today

        # Effective end of erev window for today (may be early)
        erev_end_cut = self._effective_erev_end(
            erev_date=today,
            candle_end_cut=candle_cut,
            is_friday=is_friday_civil,
            is_erev_holiday=raw_erev_holiday and not is_shabbos_civil,
            eff_shabbos_map=eff_shabbos_map,
            eff_yomtov_map=eff_yomtov_map,
        )

        in_current = (alos <= now < erev_end_cut)

        is_erev_shabbos = in_current and raw_erev_shabbos and not is_yomtov_today
        is_erev_holiday = in_current and raw_erev_holiday and not is_shabbos_civil

        self._attr_is_on = (is_erev_shabbos or is_erev_holiday)

        blocked_shabbos = raw_erev_shabbos and is_yomtov_today
        blocked_holiday = raw_erev_holiday and is_shabbos_civil

        # --- Next window finder (respect effective early end) ---
        next_start = next_end = None
        for i in range(32):
            d = today + timedelta(days=i)
            hd_d  = HDateInfo(d, diaspora=self._diaspora)
            hd_d1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)

            is_yt  = hd_d.is_yom_tov
            is_yt1 = hd_d1.is_yom_tov
            is_fri = (d.weekday() == 4)
            is_sat = (d.weekday() == 5)

            is_erev_shabbos_d     = is_fri and not is_yt
            is_erev_hol_weekday_d = (not is_sat) and (not is_yt) and is_yt1

            if not (is_erev_shabbos_d or is_erev_hol_weekday_d):
                continue

            sunrise_d, sunset_d = sun_events_for_date(geo=self._geo, tz=self._tz, base_date=d)

            raw_start = sunrise_d - timedelta(minutes=72)
            raw_end   = sunset_d  - timedelta(minutes=self._candle)

            start_cut = round_half_up(raw_start)
            candle_end_cut = round_half_up(raw_end)

            eff_end = self._effective_erev_end(
                erev_date=d,
                candle_end_cut=candle_end_cut,
                is_friday=is_erev_shabbos_d or is_fri,
                is_erev_holiday=is_erev_hol_weekday_d,
                eff_shabbos_map=eff_shabbos_map,
                eff_yomtov_map=eff_yomtov_map,
            )

            if d == today and now >= eff_end:
                continue

            next_start = start_cut
            next_end   = eff_end
            break

        # --- Eruv Tavshilin (align with erev cutoff when it's an Erev YT day) ---
        eruv_tavshilin = False
        if raw_erev_holiday:
            span_start = today + timedelta(days=1)
            if HDateInfo(span_start, diaspora=self._diaspora).is_yom_tov:
                span_end = span_start
                while HDateInfo(span_end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                    span_end += timedelta(days=1)

                includes_friday = any(
                    (span_start + timedelta(days=i)).weekday() == 4
                    for i in range((span_end - span_start).days + 1)
                )
                if includes_friday:
                    eruv_tavshilin = (alos <= now < erev_end_cut)

        # --- Anchor for the published window attrs ---
        # On a valid erev-window day: today's window (and cache it).
        # Otherwise: the most recent erev window (cached effective values if
        # available, else recomputed — early times for past dates may no
        # longer be in the maps).
        today_qualifies = (
            (is_friday_civil and not is_yomtov_today)
            or (raw_erev_holiday and not is_shabbos_civil)
        )

        if today_qualifies:
            win_start, win_end, win_candle = alos, erev_end_cut, candle_cut
            self._last_window = {
                "start": win_start,
                "end": win_end,
                "candle": win_candle,
            }
        else:
            prev_d = self._most_recent_erev_date(today)
            if prev_d is None:
                win_start, win_end, win_candle = alos, erev_end_cut, candle_cut
            elif (
                self._last_window
                and self._last_window["start"].date() == prev_d
            ):
                win_start = self._last_window["start"]
                win_end = self._last_window["end"]
                win_candle = self._last_window["candle"]
            else:
                prev_win = self._compute_window_for(
                    prev_d, eff_shabbos_map, eff_yomtov_map
                )
                self._last_window = prev_win
                win_start = prev_win["start"]
                win_end = prev_win["end"]
                win_candle = prev_win["candle"]

        attrs: dict[str, str | bool] = {
            "Now": now.isoformat(),
            "Is_Erev_Shabbos":  is_erev_shabbos,
            "Is_Erev_Holiday":  is_erev_holiday,
            "Blocked_Erev_Shabbos": blocked_shabbos,
            "Blocked_Erev_Holiday": blocked_holiday,
            "Is_Yom_Tov_Today":     is_yomtov_today,
            "Is_Shabbos_Today":     is_shabbos_today,
            "Eruv_Tavshilin":       eruv_tavshilin,
            "Erev_Window_Start":    win_start.isoformat(),
            "Erev_Window_End":      win_end.isoformat(),
            "Candle_End_Cut":        win_candle.isoformat(),
        }
        if next_start and next_end:
            attrs.update({
                "Next_Erev_Window_Start": next_start.isoformat(),
                "Next_Erev_Window_End":   next_end.isoformat(),
            })
        self._attr_extra_state_attributes = attrs

class NoMeluchaSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from (possibly early) entry until havdalah on Shabbos & multi-day Yom Tov.

    Normal behavior: candle-lighting → havdalah.
    If Early Shabbos / Early Yom Tov is enabled and effective start times exist,
    this sensor will turn ON at those earlier times.
    """
    _attr_name = "No Melucha"
    _attr_icon = "mdi:briefcase-variant-off"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        slug = "no_melucha"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass

        # Pull Israel/Chutz setting from integration config
        cfg_root = hass.data.get(DOMAIN, {}) or {}
        cfg_conf = cfg_root.get("config", {}) or {}
        self._diaspora = cfg_conf.get("diaspora", True)

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

        # Recalculate exactly at each new minute (00 seconds)
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ---------------- Early-start helpers ----------------

    def _get_early_maps(self) -> tuple[dict, dict]:
        """Fetch effective early-start maps from the Early start-time sensor."""
        early_state = self.hass.states.get("sensor.yidcal_early_shabbos_yt_start_time")
        if not early_state:
            return {}, {}

        attrs = early_state.attributes or {}

        def pick(*names):
            for n in names:
                if n in attrs and isinstance(attrs[n], dict):
                    return attrs[n]
            return {}

        eff_shabbos = pick(
            "Effective shabbos start by date",
            "Effective_Shabbos_Start_By_Date",
            "effective_shabbos_start_by_date",
        )
        eff_yomtov = pick(
            "Effective yomtov start by date",
            "Effective_Yomtov_Start_By_Date",
            "effective_yomtov_start_by_date",
        )

        return eff_shabbos or {}, eff_yomtov or {}

    def _parse_early_dt(self, val):
        """Parse an iso/datetime attribute into local tz datetime."""
        if not val:
            return None
        try:
            if isinstance(val, datetime):
                dt_local = val
            else:
                dt_local = datetime.fromisoformat(str(val))
            if dt_local.tzinfo is None:
                dt_local = dt_local.replace(tzinfo=self._tz)
            return dt_local.astimezone(self._tz)
        except Exception:
            return None

    def _apply_early_start(
        self,
        erev_date,
        start_dt: datetime,
        is_yomtov_cluster: bool,
        eff_shabbos_map: dict,
        eff_yomtov_map: dict,
    ) -> datetime:
        """Return earlier of candle-start and effective early-start (if any)."""
        key = erev_date.isoformat()
        src_map = eff_yomtov_map if is_yomtov_cluster else eff_shabbos_map

        early_val = src_map.get(key)
        early_dt = self._parse_early_dt(early_val)
        if not early_dt:
            return start_dt

        # Round early time the same way as candle starts
        early_dt = round_half_up(early_dt)

        return min(start_dt, early_dt)

    # ---------------- Main update ----------------

    async def async_update(self, now: datetime | None = None) -> None:
        """Turn on from (possibly early) entry through havdalah of the last day,
        merging Yom Tov clusters that run directly into Shabbos."""
        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()
        if not self._geo:
            return

        eff_shabbos_map, eff_yomtov_map = self._get_early_maps()

        # Each candidate: (start_dt, end_dt, display_name, is_yomtov_cluster)
        candidates: list[tuple[datetime, datetime, str, bool]] = []

        # --- 1) Yom Tov clusters (possibly multi-day) ------------------------
        for delta in range(-1, 32):
            d = today + timedelta(days=delta)
            hd = HDateInfo(d, diaspora=self._diaspora)

            # Only first day of each contiguous YT block
            if not hd.is_yom_tov or HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                continue

            # Find last day of this YT block
            end_d = d
            while HDateInfo(end_d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                end_d += timedelta(days=1)

            # Candle-based start (Erev YT)
            start_dt = sunset_for_date(geo=self._geo, tz=self._tz, base_date=d - timedelta(days=1)) - timedelta(minutes=self._candle)

            # Apply early YT if present for that Erev
            erev_yt = d - timedelta(days=1)
            start_dt = self._apply_early_start(
                erev_yt,
                start_dt,
                is_yomtov_cluster=True,
                eff_shabbos_map=eff_shabbos_map,
                eff_yomtov_map=eff_yomtov_map,
            )

            # End = havdalah after last YT day
            end_dt = sunset_for_date(geo=self._geo, tz=self._tz, base_date=end_d) + timedelta(minutes=self._havdalah)

            # Ignore clusters whose *rounded* end is already past
            if round_ceil(end_dt) <= now:
                continue

            candidates.append((start_dt, end_dt, str(hd.holidays[0]), True))

        # --- 2) Shabbos windows (this week + next) --------------------------
        wd = today.weekday()
        friday = today - timedelta(days=(wd - 4) % 7)

        for week in range(2):
            f = friday + timedelta(days=7 * week)
            s = f + timedelta(days=1)

            start_dt = sunset_for_date(geo=self._geo, tz=self._tz, base_date=f) - timedelta(minutes=self._candle)

            # Apply early Shabbos if present for that Friday
            start_dt = self._apply_early_start(
                f,
                start_dt,
                is_yomtov_cluster=False,
                eff_shabbos_map=eff_shabbos_map,
                eff_yomtov_map=eff_yomtov_map,
            )

            end_dt = sunset_for_date(geo=self._geo, tz=self._tz, base_date=s) + timedelta(minutes=self._havdalah)

            if round_ceil(end_dt) <= now:
                continue

            candidates.append((start_dt, end_dt, "שבת", False))

        # Safety: if absolutely nothing, just turn off
        if not candidates:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "In_Window": "false",
            }
            return

        # --- 3) Pick the main cluster (current, else earliest upcoming) -----
        main: tuple[datetime, datetime, str, bool] | None = None

        for start_dt, end_dt, name, is_yt in candidates:
            start_cut = round_half_up(start_dt)
            end_cut   = round_ceil(end_dt)
            if start_cut <= now < end_cut:
                if main is None or start_dt < main[0]:
                    main = (start_dt, end_dt, name, is_yt)

        if main is None:
            best_start_cut: datetime | None = None
            for start_dt, end_dt, name, is_yt in candidates:
                start_cut = round_half_up(start_dt)
                if start_cut >= now and (best_start_cut is None or start_cut < best_start_cut):
                    best_start_cut = start_cut
                    main = (start_dt, end_dt, name, is_yt)

        if main is None:
            for start_dt, end_dt, name, is_yt in candidates:
                if start_dt >= now:
                    if main is None or start_dt < main[0]:
                        main = (start_dt, end_dt, name, is_yt)

        start, end, festival_name, is_yt_cluster = main  # type: ignore[misc]

        # --- 4) Merge overlapping clusters (3-day YT into Shabbos, etc.) ----
        union_start, union_end = start, end
        for start_dt, end_dt, *_ in candidates:
            if start_dt <= union_end and end_dt >= union_start:
                union_start = min(union_start, start_dt)
                union_end = max(union_end, end_dt)

        # --- 5) Round + final state / attributes ---------------------------
        window_start = round_half_up(union_start)
        window_end = round_ceil(union_end)
        in_window = (window_start <= now < window_end)

        self._attr_is_on = in_window

        active_festival = festival_name if in_window else None
        upcoming_festival = None if in_window else festival_name

        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Active_Festival_Name": active_festival,
            "Upcoming_Festival_Name": upcoming_festival,
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
            "In_Window": str(bool(in_window)).lower(),
            "Is_Yom_Tov": str(bool(in_window and is_yt_cluster)).lower(),
            "Is_Shabbos": str(bool(in_window and (festival_name == "שבת"))).lower(),
            "Early_Shabbos_Map_Keys": list(eff_shabbos_map.keys()),
            "Early_YomTov_Map_Keys": list(eff_yomtov_map.keys()),
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
    # Determine diaspora/EY once for filtering
    cfg_root = hass.data.get(DOMAIN, {}) or {}
    cfg_conf = cfg_root.get("config", {}) or {}
    diaspora = cfg_conf.get("diaspora", True)
    
    helper = YidCalHelper(hass.config)
    helper._candle_offset   = candle
    helper._havdalah_offset = havdalah
    
    entities: list[BinarySensorEntity] = [
        ShabbosMevorchimSensor(hass, helper, candle, havdalah),
        UpcomingShabbosMevorchimSensor(hass, helper, candle, havdalah),
        NoMeluchaSensor(hass, candle, havdalah),
        NoMeluchaShabbosSensor(hass, candle, havdalah),
        NoMeluchaYomTovSensor(hass, candle, havdalah),
        BishulAllowedSensor(hass, candle, havdalah),
        ErevHolidaySensor(hass, candle),
        SlichosSensor(hass, candle, havdalah),
        NoMusicSensor(hass, candle, havdalah),
        UpcomingYomTovSensor(hass, candle, havdalah),
        NineDaysSensor(hass, candle, havdalah),
        MotziSensor(hass, candle, havdalah),
        LongerShachrisSensor(hass, candle, havdalah),
        EruvTavshilinSensor(hass, candle, havdalah),
        DSTSensor(hass),
        ErevAfterChatzosSensor(hass, candle),
        ErevTishaBavAfterChatzosSensor(hass),
        TishaBavNightSensor(hass),
        LongerShabbosSensor(hass, candle, havdalah),
        ThreeDayYomTovSensor(hass, candle, havdalah),
        BeinHazmonimSensor(hass, candle, havdalah),
        YomTovTomorrowSensor(hass),
        LeapYearSensor(hass, havdalah),
        LeapYearNextYearSensor(hass, havdalah),
        ShmitaSensor(hass, havdalah),
        KiddushLevanaSensor(hass),
        PruzbolSensor(hass, candle),
    ]
    if include_attrs:
        # Filter the list so we don’t create sensors that will never be used
        if diaspora:
            allowed = [
                n for n in SLUG_OVERRIDES
                if n not in HolidaySensor.EY_ONLY_ATTRS
            ]
        else:
            allowed = [
                n for n in SLUG_OVERRIDES
                if n not in HolidaySensor.DIASPORA_ONLY_ATTRS
            ]
            # In EY, we *only* want the combined שמיני עצרת/שמחת תורה,
            # so skip the separate ones.
            allowed = [n for n in allowed if n not in {"שמיני עצרת", "שמחת תורה"}]

        for name in allowed:
            entities.append(HolidayAttributeBinarySensor(hass, name))

    async_add_entities(entities, update_before_add=False)
