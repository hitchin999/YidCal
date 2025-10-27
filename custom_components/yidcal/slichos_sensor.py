from __future__ import annotations

"""YidCal – Slichos binary‑sensor

Turns **on** from Alef‑Selichos (Motzaei Shabbos before Rosh HaShanah) until
candle‑lighting Erev Yom Kippur, excluding Shabbos and both days of R"H.

Attributes expose scheduling metadata **plus** a Hebrew label such as::

    סליחות ליום א׳
    סליחות לערב ר"ה
    סליחות לצום גדליה
    סליחות ליום חמישי מעשי"ת
    סליחות לערב יוה"כ

The ✧ fifth Aseres‑Yemei‑Teshuvah day (חמישי מעשי"ת) always follows the
"שלוש‑עשרה מדות" custom, even in the rare year when it falls on 6 Tishrei.
"""

import datetime
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from zmanim.zmanim_calendar import ZmanimCalendar
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .config_flow import CONF_SLICHOS_LABEL_ROLLOVER
from .config_flow import DEFAULT_SLICHOS_LABEL_ROLLOVER
from .zman_sensors import get_geo
from .yidcal_lib.helper import int_to_hebrew  # existing util in YidCal

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Hebrew words for 1‑6 (used in Aseres‑Yemei‑Teshuvah labels)
HEBREW_DAY_WORDS: dict[int, str] = {
    1: "ראשון",
    2: "שני",
    3: "שלישי",
    4: "רביעי",
    5: "חמישי",
    6: "ששי",  # never used here, but kept for completeness
}


def _is_xiiimiddos(hd: PHebrewDate, weekday: int) -> bool:
    """Detect the special י"ג מידות day (Polin/Satmar custom).

    • 8 Tishrei when it falls Mon/Tue/Thu
    • 6 Tishrei when it falls Thu (swap‑year pattern)
    """
    return (
        hd.month == 7
        and (
            (hd.day == 8 and weekday in (0, 1, 3))  # Mon/Tue/Thu
            or (hd.day == 6 and weekday == 3)  # Thu (swap year)
        )
    )


async def async_setup_entry(hass, entry, async_add_entities):
    candle_offset = entry.options.get("candle_offset", 15)
    havdalah_offset = entry.options.get("havdalah_offset", 72)
    async_add_entities(
        [SlichosSensor(hass, candle_offset, havdalah_offset)], update_before_add=True
    )


class SlichosSensor(YidCalSpecialDevice, RestoreEntity, BinarySensorEntity):
    """Binary sensor for the continuous Selichos period."""

    _attr_name = "Slichos"
    _attr_icon = "mdi:book-open-variant"
    _attr_should_poll = True

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int):
        super().__init__()
        slug = "slichos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._rollover = self.hass.data[DOMAIN]["config"].get(
            CONF_SLICHOS_LABEL_ROLLOVER, DEFAULT_SLICHOS_LABEL_ROLLOVER
        )
        self._attr_is_on: bool = False
        self._attr_extra_state_attributes: dict[str, str | bool | int] = {}

    # ------------------------------------------------------------------ helpers
    def _schedule_update(self, *_args) -> None:
        """Thread‑safe wrapper to schedule *async_update* immediately."""

        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self.async_update())
        )

    # ------------------------------------------------ lifecycle / listeners ----
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state or "").lower() == "on"
            self._attr_extra_state_attributes = dict(last.attributes or {})

        # 1) Regular minute interval
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

        # 2) Top‑of‑minute cron (handles manual time jumps)
        unsub_cron = async_track_time_change(
            self.hass, self._schedule_update, second=0
        )
        self._register_listener(unsub_cron)

        await self.async_update()

    # ----------------------------------------------------------------- main ---
    async def async_update(self, now: datetime.datetime | None = None) -> None:  # noqa: C901
        if self.hass is None:
            return

        tz = ZoneInfo(self.hass.config.time_zone)
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        actual_date = now.date()

        geo = await get_geo(self.hass)

        # -------------------------------------------------------------------
        # Determine festival date (after Havdalah roll‑over)
        cal_today = ZmanimCalendar(geo_location=geo, date=actual_date)
        sunset_today = cal_today.sunset().astimezone(tz)
        havdalah_cut_today = sunset_today + timedelta(minutes=self._havdalah_offset)
        festival_date = (
            actual_date + timedelta(days=1) if now >= havdalah_cut_today else actual_date
        )
        hd_fest = PHebrewDate.from_pydate(festival_date)

        # ------------------------------------------------ select High‑Holiday cycle
        target_year = hd_fest.year if hd_fest.month >= 7 else hd_fest.year + 1

        # ------------------------------------------------ Alef‑Selichos calculation
        tishrei1_greg = PHebrewDate(target_year, 7, 1).to_pydate()
        rh_wd = tishrei1_greg.weekday()  # Mon=0 … Sun=6

        pre_rh = tishrei1_greg - timedelta(days=1)
        alef_shabbos = pre_rh - timedelta(days=((pre_rh.weekday() - 5) % 7))
        if rh_wd in (0, 1):  # Monday or Tuesday R"H → start a week earlier
            alef_shabbos -= timedelta(days=7)

        alef_start = (
            ZmanimCalendar(geo_location=geo, date=alef_shabbos)
            .sunset()
            .astimezone(tz)
            + timedelta(minutes=self._havdalah_offset)
        )

        # ------------------------------------------------ Erev YK candle‑lighting
        erev_yk_greg = PHebrewDate(target_year, 7, 9).to_pydate()
        erev_yk_candle = (
            ZmanimCalendar(geo_location=geo, date=erev_yk_greg)
            .sunset()
            .astimezone(tz)
            - timedelta(minutes=self._candle_offset)
        )

        in_global_window = alef_start <= now < erev_yk_candle

        # ------------------------------------------------ exclusions: Shabbos, R"H
        wd_today = actual_date.weekday()
        friday = actual_date - timedelta(days=(wd_today - 4) % 7)
        saturday = friday + timedelta(days=1)

        shabbos_start = (
            ZmanimCalendar(geo_location=geo, date=friday)
            .sunset()
            .astimezone(tz)
            - timedelta(minutes=self._candle_offset)
        )
        shabbos_end = (
            ZmanimCalendar(geo_location=geo, date=saturday)
            .sunset()
            .astimezone(tz)
            + timedelta(minutes=self._havdalah_offset)
        )
        excluded_shabbos = shabbos_start <= now < shabbos_end

        tishrei2_greg = tishrei1_greg + timedelta(days=1)
        rh_start = (
            ZmanimCalendar(geo_location=geo, date=tishrei1_greg - timedelta(days=1))
            .sunset()
            .astimezone(tz)
            - timedelta(minutes=self._candle_offset)
        )
        rh_end = (
            ZmanimCalendar(geo_location=geo, date=tishrei2_greg)
            .sunset()
            .astimezone(tz)
            + timedelta(minutes=self._havdalah_offset)
        )
        excluded_rosh_hashanah = rh_start <= now < rh_end

        # ------------------------------------------------ final ON/OFF state
        is_on = in_global_window and not (excluded_shabbos or excluded_rosh_hashanah)
        self._attr_is_on = is_on

        # ====================================================================
        # Label calculation (Hebrew wording)
        label = None
        if self._rollover == "havdalah":
            today = festival_date           # rolls after sunset + havdalah_offset
            hd_today = hd_fest
        else:  # "midnight"
            today = actual_date             # civil date rolls 00:00
            hd_today = PHebrewDate.from_pydate(today)
        weekday = today.weekday()

        # Anchor dates
        erev_rh_greg = tishrei1_greg - timedelta(days=1)
        tzom_gedaliah_greg = (
            tishrei1_greg + timedelta(days=2)
            if tishrei1_greg.weekday() != 5  # 3 Tishrei not Shabbos
            else tishrei1_greg + timedelta(days=3)  # postponed fast
        )

        # ---- 13 Middos overrides everything else
        if _is_xiiimiddos(hd_today, weekday):
            label = "סליחות ליום חמישי מעשי\"ת"

        # ---- Fixed captions
        elif today == erev_rh_greg:
            label = "סליחות לערב ר\"ה"
        elif today == tzom_gedaliah_greg:
            label = "סליחות לצום גדליה"
        elif hd_today.month == 7 and hd_today.day == 9:  # 9 Tishrei – Erev YK
            label = "סליחות לערב יוה\"כ"

        # ---- Aseres-Yemei-Teshuvah numbering (after the fast, before Erev YK)
        elif hd_today.month == 7 and tzom_gedaliah_greg < today < erev_yk_greg:
            # Day-1 = Tzom Gedaliah itself (3 Tishrei)
            cnt = 1
            d = tzom_gedaliah_greg + timedelta(days=1)     # start with 4 Tishrei
            while d <= today:
                if d.weekday() != 5:                       # skip Shabbos Shuvah
                    cnt += 1
                d += timedelta(days=1)

            # cnt now 1…6 → ראשון…חמישי
            label = f"סליחות ליום {HEBREW_DAY_WORDS[cnt]} מעשי\"ת"

        # ---- Elul period ordinal
        elif is_on:  # still active but none of the above captions applied
            first_morning = alef_shabbos + timedelta(days=1)
            ordinal = 0
            d = first_morning
            while d <= today:
                if d.weekday() != 5:  # skip Shabbos mornings
                    ordinal += 1
                d += timedelta(days=1)
            if ordinal:
                label = f"סליחות ליום {int_to_hebrew(ordinal)}"

        # ====================================================================
        # Expose attributes
        attrs: dict[str, str | bool | int] = {
            "Now": now.isoformat(),
            "Global_Start_Alef_Slichos_Motzi": alef_start.isoformat(),
            "Global_End_Erev_YK_Candle": erev_yk_candle.isoformat(),
            "Excluded_Rosh_Hashanah": excluded_rosh_hashanah,
            "Excluded_Shabbos": excluded_shabbos,
            "In_Global_Window": in_global_window,
            "Selichos_Label": label or "",
        }

        self._attr_extra_state_attributes = attrs
