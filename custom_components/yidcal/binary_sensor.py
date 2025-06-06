# /config/custom_components/yidcal/binary_sensor.py
from __future__ import annotations
import logging
from datetime import datetime, timedelta

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
from zoneinfo import ZoneInfo
from .device import YidCalDevice

from astral import LocationInfo
from astral.sun import sun
from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate
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

_LOGGER = logging.getLogger(__name__)




# ─── Your override map ────────────────────────────────────────────────────────
SLUG_OVERRIDES: dict[str, str] = {
    "א׳ סליחות":             "alef_selichos",
    "ערב ראש השנה":          "erev_rosh_hashana",
    "ראש השנה א׳":           "rosh_hashana_1",
    "ראש השנה ב׳":           "rosh_hashana_2",
    "ראש השנה א׳ וב׳":       "rosh_hashana_1_2",
    "צום גדליה":             "tzom_gedalia",
    "שלוש עשרה מדות":        "shlosh_asrei_midos",
    "ערב יום כיפור":          "erev_yom_kippur",
    "יום הכיפורים":          "yom_kippur",
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
    "חול המועד פסח":        "chol_hamoed_pesach",
    "שביעי של פסח":         "shviei_shel_pesach",
    "אחרון של פסח":         "achron_shel_pesach",
    "ל\"ג בעומר":            "lag_baomer",
    "ערב שבועות":           "erev_shavuos",
    "שבועות א׳":            "shavuos_1",
    "שבועות ב׳":            "shavuos_2",
    "שבועות א׳ וב׳":        "shavuos_1_2",
    "צום שבעה עשר בתמוז":   "shiva_usor_btammuz",
    "תשעה באב":             "tisha_bav",
    "תשעה באב נדחה":        "tisha_bav_nidche",
    "ראש חודש":             "rosh_chodesh",
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
    """True on specific Erev‐days from alos ha-shachar until candle-lighting, with restore-on-reboot."""

    _attr_name = "Erev"
    _attr_icon = "mdi:weather-sunset-up"

    # (Hebrew month, day) of Erev‐Yom‐Tov dates
    _EREV_DATES = {
        (6, 29),  # ערב ראש השנה
        (7, 9),   # ערב יום כיפור
        (7, 14),  # ערב סוכות
        (7, 21),  # הושענא רבה
        (9, 24),  # ערב חנוכה
        (1, 14),  # ערב פסח
        (3, 5),   # ערב שבועות
    }

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__()
        slug = "erev"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )
        self._attr_extra_state_attributes: dict[str, str] = {}

    def _schedule_update(self, *_args) -> None:
        """Thread-safe scheduling of async_update on the event loop."""
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    async def async_added_to_hass(self) -> None:
        """Restore previous state, do an initial update, and register a once-per-minute poll."""
        await super().async_added_to_hass()

        # 1) Restore last known state
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == STATE_ON)

        # 2) Immediate first update
        await self.async_update()

        # 3) Poll every minute (use base-class wrapper so unsubscribe is saved)
        self._register_interval(
            self.hass,
            self._schedule_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now: datetime | None = None) -> None:
        """Compute whether we are currently in an Erev window."""
        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        today = now.date()

        s = sun(self._loc.observer, date=today, tzinfo=self._tz)
        sunrise = s["sunrise"]
        alos = sunrise - timedelta(minutes=72)  # alos ha-shachar at 72 min before sunrise
        sunset = s["sunset"]

        # holiday vs Shabbos
        hd = PHebrewDate.from_pydate(today)
        is_erev_holiday = (hd.month, hd.day) in self._EREV_DATES
        candle_time = sunset - timedelta(minutes=self._candle)
        is_erev_shabbos = (today.weekday() == 4) and (now < candle_time)
        is_erev = is_erev_holiday or is_erev_shabbos

        self._attr_is_on = is_erev and (alos <= now < candle_time)

        self._attr_extra_state_attributes = {
            "now": now.isoformat(),
            "is_erev_holiday": is_erev_holiday,
            "is_erev_shabbos": is_erev_shabbos,
            "alos": alos.isoformat(),
            "candle_time": candle_time.isoformat(),
            "window_start": alos.isoformat(),
            "window_end": candle_time.isoformat(),
        }

        
class MeluchaProhibitionSensor(YidCalDevice, BinarySensorEntity):
    """True from candle-lighting until havdalah on Shabbos & multi-day Yom Tov."""

    _attr_name = "Melucha Prohibition"
    _attr_icon = "mdi:briefcase-variant-off"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        slug = "melucha"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass
        self._diaspora = True
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._tz = ZoneInfo(hass.config.time_zone)
        self._loc = LocationInfo(
            latitude=hass.config.latitude,
            longitude=hass.config.longitude,
            timezone=hass.config.time_zone,
        )
        self._attr_extra_state_attributes: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        """Register immediate update and once-per-minute polling."""
        await super().async_added_to_hass()

        # Immediate update
        await self.async_update()

        # Poll every minute (use base-class wrapper to store unsubscribe)
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now: datetime | None = None) -> None:
        """Compute whether melucha is prohibited (Shabbos/Yom Tov window)."""
        # 1) get correct current time in local tz
        now = dt_util.now().astimezone(self._tz)
        today = now.date()

        # 2) compute sunset + candle-lighting threshold for today
        s_today = sun(self._loc.observer, date=today, tzinfo=self._tz)
        sunset_today = s_today["sunset"]
        candle_time = sunset_today - timedelta(minutes=self._candle)

        # 3) decide which Gregorian date to check for festival
        check_date = today + timedelta(days=1) if now >= candle_time else today
        hd = HDateInfo(check_date, diaspora=self._diaspora)
        is_yomtov = hd.is_yom_tov

        # 4) find festival span (start_date…end_date)
        if is_yomtov:
            # multi-day Yom Tov: expand around check_date
            start_date = check_date
            while HDateInfo(start_date - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start_date -= timedelta(days=1)
            end_date = check_date
            while HDateInfo(end_date + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                end_date += timedelta(days=1)
            festival_name = HDateInfo(start_date, diaspora=self._diaspora).holidays[0].name
        else:
            # Shabbos as two-day festival (Fri→Sat)
            wd = today.weekday()  # Mon=0…Fri=4,Sat=5
            if wd == 5 and now < (sunset_today + timedelta(minutes=self._havdalah)):
                # still Sat before havdalah: started Fri
                start_date = today - timedelta(days=1)
            else:
                # upcoming Fri
                days_to_friday = (4 - wd) % 7
                start_date = today + timedelta(days=days_to_friday)
            end_date = start_date + timedelta(days=1)
            festival_name = "Shabbos"

        # 5) compute the candle window:
        #    - for multi-day Yom Tov, use the eve *before* the first day
        #    - for Shabbos, use that Friday itself
        if is_yomtov:
            eve_date = start_date - timedelta(days=1)
        else:
            eve_date = start_date

        # sunrise/sunset for eve and final day
        s_eve = sun(self._loc.observer, date=eve_date, tzinfo=self._tz)["sunset"]
        s_final = sun(self._loc.observer, date=end_date, tzinfo=self._tz)["sunset"]

        window_start = s_eve - timedelta(minutes=self._candle)
        window_end = s_final + timedelta(minutes=self._havdalah)
        in_window = window_start <= now < window_end

        # only show “Shabbos” when we’re actually in that Fri→Sat window
        if festival_name == "Shabbos" and not in_window:
            festival_name = None

        # 6) set state & attributes
        self._attr_is_on = in_window
        self._attr_extra_state_attributes = {
            "now": now.isoformat(),
            "today": str(today),
            "check_date": str(check_date),
            "festival_name": festival_name,
            "is_yomtov": is_yomtov,
            "is_shabbos": (festival_name == "Shabbos" and in_window),
            "candle_eve": eve_date.isoformat(),
            "sunset_eve": s_eve.isoformat(),
            "sunset_final": s_final.isoformat(),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "in_window": in_window,
        }



async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    opts = hass.data[DOMAIN][entry.entry_id]
    candle = opts["candlelighting_offset"]
    havdalah = opts["havdalah_offset"]

    entities: list[BinarySensorEntity] = [
        MeluchaProhibitionSensor(hass, candle, havdalah),
        ErevHolidaySensor(hass, candle),
    ]
    for name in SLUG_OVERRIDES:
        entities.append(HolidayAttributeBinarySensor(hass, name))
    entities.extend([
        MotzeiYomKippurSensor(hass, candle, havdalah),
        MotzeiPesachSensor(hass, candle, havdalah),
        MotzeiSukkosSensor(hass, candle, havdalah),
        MotzeiShavuosSensor(hass, candle, havdalah),
        MotzeiRoshHashanaSensor(hass, candle, havdalah),
        MotzeiShivaUsorBTammuzSensor(hass, candle, havdalah),
        MotzeiTishaBavSensor(hass, candle, havdalah),
    ])

    async_add_entities(entities, update_before_add=True)
