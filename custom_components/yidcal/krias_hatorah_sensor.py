# krias_hatorah_sensor.py
"""Krias HaTorah Sensor for YidCal Integration."""

from __future__ import annotations
import datetime
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo
from typing import Any

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from pyluach.parshios import getparsha_string

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from .device import YidCalDisplayDevice
from .const import DOMAIN
from .zman_sensors import get_geo
from .data.krias_hatorah_data import (
    PARSHIYOT, ENGLISH_TO_HEBREW_PARSHA, CHANUKAH_READINGS, ROSH_CHODESH_READING,
    FAST_DAY_READING, YOM_KIPPUR_READINGS, TISHA_BAV_READINGS, ROSH_HASHANAH_READINGS,
    SUKKOS_READINGS, SHMINI_ATZERES_READINGS, PESACH_READINGS, SHAVUOS_READINGS,
    PURIM_READING, SCROLL_ANCHORS, NESIIM_READINGS,
    KORBANOS_READING, MISHNE_TORAH_READING,
    MONDAY, THURSDAY, SATURDAY, HEBREW_DAYS,
)

_LOGGER = logging.getLogger(__name__)

# Hebrew numerals for sefer count
HEBREW_NUMERALS = {1: "א'", 2: "ב'", 3: "ג'"}


class KriasHaTorahSensor(YidCalDisplayDevice, RestoreEntity, SensorEntity):
    _attr_name = "Krias HaTorah"
    _attr_icon = "mdi:book-open-page-variant"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self._attr_unique_id = "yidcal_krias_hatorah"
        self.entity_id = "sensor.yidcal_krias_hatorah"
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_native_value: str | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}
        cfg = hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora = cfg.get("diaspora", True)
        # Optional minhag-based readings
        self._read_korbanos = cfg.get("korbanos_yud_gimmel_midos", False)
        self._read_mishne_torah = cfg.get("mishne_torah_hoshana_rabba", False)
        self._tz: ZoneInfo | None = ZoneInfo(hass.config.time_zone)
        self._geo: GeoLocation | None = None
        self._last_completed_anchor: str | None = None
        self._last_completed_time: datetime.datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._attr_native_value = last.state if last.state not in ("unknown", "unavailable") else None
            self._attr_extra_state_attributes = dict(last.attributes or {})
            self._last_completed_anchor = self._attr_extra_state_attributes.get("_last_completed_anchor")
            lct = self._attr_extra_state_attributes.get("_last_completed_time")
            if lct:
                try:
                    self._last_completed_time = datetime.datetime.fromisoformat(lct)
                except:
                    pass
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    @property
    def native_value(self) -> str | None:
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attr_extra_state_attributes

    def _build_summary(self, sifrei: list[dict]) -> str:
        """Build summary string for the reading."""
        if not sifrei:
            return ""
        
        if len(sifrei) == 1:
            # Single sefer: just show parsha (reason)
            s = sifrei[0]
            parsha = s.get("parsha_source", "")
            reason = s.get("reason", "")
            if reason and reason != parsha:
                return f"{parsha} ({reason})"
            return parsha
        
        # Multiple sifrei: ג' ס"ת • ספר א' - מקץ (reason) • ספר ב' - ...
        count = len(sifrei)
        count_heb = HEBREW_NUMERALS.get(count, str(count))
        parts = [f"{count_heb} ס\"ת"]
        
        for s in sifrei:
            sefer_num = s.get("sefer_number", 1)
            sefer_heb = HEBREW_NUMERALS.get(sefer_num, str(sefer_num))
            parsha = s.get("parsha_source", "")
            reason = s.get("reason", "")
            if reason and reason != parsha:
                parts.append(f"ספר {sefer_heb} - {parsha} ({reason})")
            else:
                parts.append(f"ספר {sefer_heb} - {parsha}")
        
        return " • ".join(parts)

    def _english_to_hebrew_parsha(self, english: str | None) -> str | None:
        if not english:
            return None
        hebrew = ENGLISH_TO_HEBREW_PARSHA.get(english)
        if hebrew:
            return hebrew
        if english in PARSHIYOT:
            return english
        return None

    def _get_weekly_parsha(self, hd: PHebrewDate) -> str | None:
        try:
            english = getparsha_string(hd, israel=not self._diaspora)
            return self._english_to_hebrew_parsha(english)
        except:
            return None

    def _get_next_weekly_parsha(self, hd: PHebrewDate) -> str | None:
        try:
            english = getparsha_string(hd + 7, israel=not self._diaspora)
            return self._english_to_hebrew_parsha(english)
        except:
            return None

    def _is_rosh_chodesh(self, hd: PHebrewDate) -> bool:
        if hd.month == 7 and hd.day == 1:
            return False
        return hd.day in (1, 30)

    def _get_chanukah_day(self, hd: PHebrewDate) -> int | None:
        try:
            chan_first = PHebrewDate(hd.year, 9, 25).to_pydate()
            days = (hd.to_pydate() - chan_first).days
            return days + 1 if 0 <= days <= 7 else None
        except:
            return None

    def _is_purim(self, hd: PHebrewDate) -> bool:
        is_leap = ((hd.year * 7 + 1) % 19) < 7
        adar = 13 if is_leap else 12
        return hd.month == adar and hd.day == 14

    def _get_fast_info(self, hd: PHebrewDate, wd: int) -> tuple[bool, str | None, bool]:
        year = hd.year
        is_leap = ((year * 7 + 1) % 19) < 7
        adar = 13 if is_leap else 12
        gedaliah_day = 4 if PHebrewDate(year, 7, 3).to_pydate().weekday() == 5 else 3
        if hd.month == 7 and hd.day == gedaliah_day:
            return True, "צום גדליה", False
        if hd.month == 10 and hd.day == 10:
            return True, "צום עשרה בטבת", False
        if hd.month == adar and hd.day == 13 and wd != 5:
            return True, "תענית אסתר", False
        if hd.month == adar and hd.day == 11 and wd == 3:
            try:
                if PHebrewDate(year, adar, 13).to_pydate().weekday() == 5:
                    return True, "תענית אסתר", False
            except:
                pass
        if hd.month == 4 and hd.day == 17 and wd != 5:
            return True, "צום שבעה עשר בתמוז", False
        if hd.month == 4 and hd.day == 18 and wd == 6:
            try:
                if PHebrewDate(year, 4, 17).to_pydate().weekday() == 5:
                    return True, "צום שבעה עשר בתמוז", False
            except:
                pass
        if hd.month == 5 and hd.day == 9 and wd != 5:
            return True, "תשעה באב", True
        if hd.month == 5 and hd.day == 10 and wd == 6:
            try:
                if PHebrewDate(year, 5, 9).to_pydate().weekday() == 5:
                    return True, "תשעה באב נדחה", True
            except:
                pass
        return False, None, False

    def _is_yom_kippur(self, hd: PHebrewDate) -> bool:
        return hd.month == 7 and hd.day == 10

    def _is_shlosh_esrei_middos(self, date: datetime.date) -> bool:
        """Return True on the 'שלוש עשרה מדות' day used for Korbanos at Mincha."""
        hd_py = PHebrewDate.from_pydate(date)
        wd_py = date.weekday()  # Python weekday: Mon=0 ... Sun=6
        if hd_py.month != 7:
            return False
        return (
            (hd_py.day == 8 and wd_py in (MONDAY, TUESDAY, THURSDAY))
            or (hd_py.day == 6 and wd_py == THURSDAY)
        )
        
    def _is_shabbos_chol_hamoed(self, hd: PHebrewDate, wd: int) -> tuple[bool, str | None]:
        if wd != SATURDAY:
            return False, None
        m, d = hd.month, hd.day
        chm_start_sukkos = 17 if self._diaspora else 16
        if m == 7 and chm_start_sukkos <= d <= 20:
            return True, "sukkos"
        chm_start_pesach = 17 if self._diaspora else 16
        if m == 1 and chm_start_pesach <= d <= 20:
            return True, "pesach"
        return False, None

    def _get_yom_tov_reading(self, hd: PHebrewDate, wd: int) -> dict | None:
        m, d = hd.month, hd.day
        is_shabbos_chm, chag = self._is_shabbos_chol_hamoed(hd, wd)
        if is_shabbos_chm:
            if chag == "sukkos":
                return {"key": "shabbos_chol_hamoed_sukkos", "data": SUKKOS_READINGS["shabbos_chol_hamoed"]}
            elif chag == "pesach":
                return {"key": "shabbos_chol_hamoed_pesach", "data": PESACH_READINGS["shabbos_chol_hamoed"]}
        if m == 7 and d == 1: return {"key": "rosh_hashanah_1", "data": ROSH_HASHANAH_READINGS["day_1"]}
        if m == 7 and d == 2: return {"key": "rosh_hashanah_2", "data": ROSH_HASHANAH_READINGS["day_2"]}
        if m == 7 and d == 15: return {"key": "sukkos_1", "data": SUKKOS_READINGS["day_1"]}
        if m == 7 and d == 16 and self._diaspora: return {"key": "sukkos_2", "data": SUKKOS_READINGS["day_2_diaspora"]}
        if m == 7 and wd != SATURDAY:
            chm_start = 17 if self._diaspora else 16
            chm_map = {1: "chol_hamoed_1", 2: "chol_hamoed_2", 3: "chol_hamoed_3", 4: "chol_hamoed_4"}
            if not self._diaspora: chm_map[5] = "chol_hamoed_5_israel"
            if chm_start <= d <= 20:
                chm_day = d - chm_start + 1
                if chm_day in chm_map and chm_map[chm_day] in SUKKOS_READINGS:
                    return {"key": chm_map[chm_day], "data": SUKKOS_READINGS[chm_map[chm_day]]}
        if m == 7 and d == 21: return {"key": "hoshana_rabbah", "data": SUKKOS_READINGS["hoshana_rabbah"]}
        if m == 7 and d == 22:
            if self._diaspora: return {"key": "shemini_atzeres", "data": SHMINI_ATZERES_READINGS["shemini_atzeres_diaspora"]}
            return {"key": "shemini_atzeres_israel", "data": SHMINI_ATZERES_READINGS["shemini_atzeres_israel"]}
        if m == 7 and d == 23 and self._diaspora: return {"key": "simchas_torah", "data": SHMINI_ATZERES_READINGS["simchas_torah_diaspora"]}
        if m == 1 and d == 15: return {"key": "pesach_1", "data": PESACH_READINGS["day_1"]}
        if m == 1 and d == 16 and self._diaspora: return {"key": "pesach_2", "data": PESACH_READINGS["day_2_diaspora"]}
        if m == 1 and wd != SATURDAY:
            chm_start = 17 if self._diaspora else 16
            chm_map = {1: "chol_hamoed_1", 2: "chol_hamoed_2", 3: "chol_hamoed_3", 4: "chol_hamoed_4"}
            if chm_start <= d <= 20:
                chm_day = d - chm_start + 1
                if chm_day in chm_map and chm_map[chm_day] in PESACH_READINGS:
                    return {"key": chm_map[chm_day], "data": PESACH_READINGS[chm_map[chm_day]]}
        if m == 1 and d == 21: return {"key": "shvii_pesach", "data": PESACH_READINGS["day_7"]}
        if m == 1 and d == 22 and self._diaspora: return {"key": "acharon_pesach", "data": PESACH_READINGS["day_8_diaspora"]}
        if m == 3 and d == 6: return {"key": "shavuos_1", "data": SHAVUOS_READINGS["day_1"]}
        if m == 3 and d == 7 and self._diaspora: return {"key": "shavuos_2", "data": SHAVUOS_READINGS["day_2_diaspora"]}
        return None

    def _get_scroll_anchor(self, reading: dict) -> str:
        """
        A 'scroll anchor' represents which actual sefer(s) are on the bimah.
        For prep logic we only care if the *set* of parsha_sources changes
        between the last kriah and the next one.

        Examples:
        - Single sefer → 'מקץ'
        - Multiple → 'MULTI|מקץ|נשא|פינחס' (sorted + deduped)
        """
        sifrei = reading.get("sifrei_torah", [])
        if not sifrei:
            return ""

        anchors = sorted(
            {s.get("parsha_source", "") for s in sifrei if s.get("parsha_source")}
        )
        if not anchors:
            return ""

        if len(anchors) == 1:
            return anchors[0]

        return "MULTI|" + "|".join(anchors)

    def _build_reading(self, tefilah: str, display_title: str, reason: str, aliyah_count: int,
                       has_maftir: bool, sifrei_torah: list, window_start: str | None = None,
                       window_end: str | None = None) -> dict:
        reading = {
            "tefilah": tefilah, "display_title": display_title, "reason": reason,
            "aliyah_count": aliyah_count, "has_maftir": has_maftir,
            "sefer_torah_count": len(sifrei_torah), "sifrei_torah": sifrei_torah,
            "window_start": window_start, "window_end": window_end,
        }
        reading["_scroll_anchor"] = self._get_scroll_anchor(reading)
        return reading

    def _get_kriah_days_this_week(self, today: datetime.date, hd: PHebrewDate) -> list[str]:
        """Return list of Hebrew day names that have kriah this week."""
        kriah_days = []
        days_since_sunday = (today.weekday() + 1) % 7
        week_start = today - timedelta(days=days_since_sunday)
        for i in range(7):
            day = week_start + timedelta(days=i)
            wd = day.weekday()
            try:
                hd_day = PHebrewDate.from_pydate(day)
            except:
                continue
            has_kriah = False
            if wd in (MONDAY, THURSDAY):
                has_kriah = True
            if wd == SATURDAY:
                has_kriah = True
            if self._is_rosh_chodesh(hd_day):
                has_kriah = True
            if self._get_chanukah_day(hd_day) is not None:
                has_kriah = True
            if self._is_purim(hd_day):
                has_kriah = True
            if self._get_fast_info(hd_day, wd)[0]:
                has_kriah = True
            if self._get_yom_tov_reading(hd_day, wd) is not None:
                has_kriah = True
            if self._is_yom_kippur(hd_day):
                has_kriah = True
            if has_kriah:
                kriah_days.append(HEBREW_DAYS.get(wd, ""))
        return kriah_days

    def _get_readings_for_date(self, target_date: datetime.date, tz: ZoneInfo) -> tuple[list[dict], bool, int, int, str, dict | None]:
        """
        Compute readings for a specific date.
        Returns: (readings_list, has_kriah, sefer_count_max, aliyah_count_max, reason, nasi_reading)
        """
        wd = target_date.weekday()
        cal = ZmanimCalendar(geo_location=self._geo, date=target_date)
        alos = cal.sunrise().astimezone(tz) - timedelta(minutes=72)
        chatzos = cal.chatzos().astimezone(tz)
        mincha_gedola = chatzos + timedelta(minutes=30)
        sunset = cal.sunset().astimezone(tz)
        tzeis = sunset + timedelta(minutes=self._havdalah_offset)

        hd = PHebrewDate.from_pydate(target_date)
        weekly_parsha = self._get_weekly_parsha(hd)
        next_parsha = self._get_next_weekly_parsha(hd)
        parsha_info = PARSHIYOT.get(weekly_parsha, {}) if weekly_parsha else {}

        shacharis_start, shacharis_end = alos.isoformat(), chatzos.isoformat()
        mincha_start, mincha_end = mincha_gedola.isoformat(), tzeis.isoformat()

        is_shabbos = wd == SATURDAY
        is_mon_thu = wd in (MONDAY, THURSDAY)
        is_rc = self._is_rosh_chodesh(hd)
        chan_day = self._get_chanukah_day(hd)
        is_purim = self._is_purim(hd)
        is_yom_kippur = self._is_yom_kippur(hd)
        is_fast, fast_name, is_tisha_bav = self._get_fast_info(hd, wd)
        yom_tov = self._get_yom_tov_reading(hd, wd)
        shlosh_esrei_middos = self._is_shlosh_esrei_middos(target_date)

        readings: list[dict] = []
        has_kriah = False
        sefer_torah_count_max = 0
        aliyah_count_max = 0

        # SHABBOS
        if is_shabbos and not yom_tov:
            has_kriah = True
            sifrei = [{"sefer_number": 1, "opening_words": parsha_info.get("opening_words", ""),
                       "sefer": parsha_info.get("sefer", ""), "parsha_source": weekly_parsha or "",
                       "reason": "פרשת השבוע", "aliyos": "7 עליות"}]
            if is_rc and chan_day:
                sefer_torah_count_max = 3
                sifrei.append({"sefer_number": 2, "opening_words": ROSH_CHODESH_READING["shabbos_maftir"]["opening_words"],
                               "sefer": "במדבר", "parsha_source": "פינחס", "reason": "ראש חודש", "aliyos": "קריאת ר״ח"})
                cr = CHANUKAH_READINGS[chan_day]
                sifrei.append({"sefer_number": 3, "opening_words": cr["opening_words"], "sefer": "במדבר",
                               "parsha_source": "נשא", "reason": f"מפטיר {cr['reason']}", "aliyos": "מפטיר"})
            elif is_rc:
                sefer_torah_count_max = 2
                sifrei.append({"sefer_number": 2, "opening_words": ROSH_CHODESH_READING["shabbos_maftir"]["opening_words"],
                               "sefer": "במדבר", "parsha_source": "פינחס", "reason": "מפטיר ראש חודש", "aliyos": "מפטיר"})
            elif chan_day:
                sefer_torah_count_max = 2
                cr = CHANUKAH_READINGS[chan_day]
                sifrei.append({"sefer_number": 2, "opening_words": cr["opening_words"], "sefer": "במדבר",
                               "parsha_source": "נשא", "reason": f"מפטיר {cr['reason']}", "aliyos": "מפטיר"})
            else:
                sefer_torah_count_max = 1
            subtitle = "שבת ראש חודש וחנוכה" if is_rc and chan_day else "שבת ראש חודש" if is_rc else "שבת חנוכה" if chan_day else "שבת"
            shacharis = self._build_reading("שחרית", f"פרשת {weekly_parsha}" if weekly_parsha else "שבת",
                                            subtitle, 7, True, sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)
            aliyah_count_max = 7
            if next_parsha:
                next_info = PARSHIYOT.get(next_parsha, {})
                mincha_sifrei = [{"sefer_number": 1, "opening_words": next_info.get("opening_words", ""),
                                  "sefer": next_info.get("sefer", ""), "parsha_source": next_parsha,
                                  "reason": "מנחה דשבת", "aliyos": "כהן, לוי, ישראל"}]
                mincha = self._build_reading("מנחה", f"פרשת {next_parsha}", "מנחה דשבת", 3, False,
                                            mincha_sifrei, mincha_start, mincha_end)
                readings.append(mincha)

        elif yom_tov:
            has_kriah = True
            yt_data = yom_tov["data"]
            sifrei = yt_data.get("sifrei_torah", [])
            sefer_torah_count_max = len(sifrei)
            aliyah_count_max = yt_data.get("aliyah_count", 5)
            
            # Check if this is Simchas Torah (has night hakafos reading)
            is_simchas_torah_diaspora = (hd.month == 7 and hd.day == 23 and self._diaspora)
            is_simchas_torah_israel = (hd.month == 7 and hd.day == 22 and not self._diaspora)
            
            if is_simchas_torah_diaspora or is_simchas_torah_israel:
                # Calculate maariv window (tzeis of previous day until alos)
                yesterday = target_date - timedelta(days=1)
                yesterday_cal = ZmanimCalendar(geo_location=self._geo, date=yesterday)
                yesterday_sunset = yesterday_cal.sunset().astimezone(tz)
                yesterday_tzeis = yesterday_sunset + timedelta(minutes=self._havdalah_offset)
                maariv_start, maariv_end = yesterday_tzeis.isoformat(), alos.isoformat()
                
                # Get night reading data
                if is_simchas_torah_diaspora:
                    night_data = SHMINI_ATZERES_READINGS["simchas_torah_night_diaspora"]
                else:
                    night_data = SHMINI_ATZERES_READINGS["shemini_atzeres_night_israel"]
                
                night_sifrei = night_data.get("sifrei_torah", [])
                maariv = self._build_reading("ערבית", night_data.get("display_title", ""), 
                                            night_data.get("reason", ""), night_data.get("aliyah_count", 3),
                                            night_data.get("has_maftir", False), night_sifrei,
                                            maariv_start, maariv_end)
                readings.append(maariv)
            
            # Check if this is Hoshana Rabba (optional Mishne Torah at night)
            is_hoshana_rabba = (hd.month == 7 and hd.day == 21)
            if is_hoshana_rabba and self._read_mishne_torah:
                # Calculate maariv window (tzeis of previous day until alos)
                yesterday = target_date - timedelta(days=1)
                yesterday_cal = ZmanimCalendar(geo_location=self._geo, date=yesterday)
                yesterday_sunset = yesterday_cal.sunset().astimezone(tz)
                yesterday_tzeis = yesterday_sunset + timedelta(minutes=self._havdalah_offset)
                maariv_start, maariv_end = yesterday_tzeis.isoformat(), alos.isoformat()
                
                mt_sifrei = MISHNE_TORAH_READING.get("sifrei_torah", [])
                maariv = self._build_reading("ערבית", MISHNE_TORAH_READING.get("display_title", ""),
                                            MISHNE_TORAH_READING.get("reason", ""), 0, False, mt_sifrei,
                                            maariv_start, maariv_end)
                readings.append(maariv)
            
            shacharis = self._build_reading("שחרית", yt_data.get("display_title", ""), yt_data.get("reason", ""),
                                            yt_data.get("aliyah_count", 5), yt_data.get("has_maftir", False),
                                            sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)

        elif is_yom_kippur:
            has_kriah = True
            yk_s = YOM_KIPPUR_READINGS["shacharis"]
            sefer_torah_count_max = len(yk_s["sifrei_torah"])
            aliyah_count_max = 6
            shacharis = self._build_reading("שחרית", yk_s["display_title"], yk_s["reason"], yk_s["aliyah_count"],
                                            yk_s["has_maftir"], yk_s["sifrei_torah"], shacharis_start, shacharis_end)
            readings.append(shacharis)
            yk_m = YOM_KIPPUR_READINGS["mincha"]
            mincha = self._build_reading("מנחה", yk_m["display_title"], yk_m["reason"], yk_m["aliyah_count"],
                                         yk_m["has_maftir"], yk_m["sifrei_torah"], mincha_start, mincha_end)
            readings.append(mincha)

        elif is_fast:
            has_kriah = True
            sefer_torah_count_max = 1
            aliyah_count_max = 3
            if is_tisha_bav:
                tb_s = TISHA_BAV_READINGS["shacharis"]
                shacharis = self._build_reading("שחרית", tb_s["display_title"], fast_name, tb_s["aliyah_count"],
                                                tb_s["has_maftir"], tb_s["sifrei_torah"], shacharis_start, shacharis_end)
                readings.append(shacharis)
                tb_m = TISHA_BAV_READINGS["mincha"]
                mincha = self._build_reading("מנחה", tb_m["display_title"], fast_name, tb_m["aliyah_count"],
                                             tb_m["has_maftir"], tb_m["sifrei_torah"], mincha_start, mincha_end)
                readings.append(mincha)
            else:
                sifrei = [{"sefer_number": 1, "opening_words": FAST_DAY_READING["opening_words"],
                           "sefer": FAST_DAY_READING["sefer"], "parsha_source": FAST_DAY_READING["parsha_source"],
                           "reason": "ויחל", "aliyos": "3 עליות"}]
                shacharis = self._build_reading("שחרית", f"{fast_name} שחרית", fast_name, 3, False, sifrei,
                                                shacharis_start, shacharis_end)
                readings.append(shacharis)
                mincha = self._build_reading("מנחה", f"{fast_name} מנחה", fast_name, 3, False, sifrei,
                                            mincha_start, mincha_end)
                readings.append(mincha)

        elif chan_day and not is_shabbos:
            has_kriah = True
            cr = CHANUKAH_READINGS[chan_day]
            if is_rc:
                sefer_torah_count_max = 2
                aliyah_count_max = 4
                sifrei = [{"sefer_number": 1, "opening_words": ROSH_CHODESH_READING["weekday"]["opening_words"],
                           "sefer": "במדבר", "parsha_source": "פינחס", "reason": "ראש חודש", "aliyos": "3 עליות"},
                          {"sefer_number": 2, "opening_words": cr["opening_words"], "sefer": "במדבר",
                           "parsha_source": "נשא", "reason": cr["reason"], "aliyos": "1 עליה"}]
                shacharis = self._build_reading("שחרית", f"ראש חודש + {cr['display_title']}",
                                                f"ראש חודש + {cr['reason']}", 4, False, sifrei,
                                                shacharis_start, shacharis_end)
            else:
                sefer_torah_count_max = 1
                aliyah_count_max = 3
                sifrei = [{"sefer_number": 1, "opening_words": cr["opening_words"], "sefer": cr["sefer"],
                           "parsha_source": cr["parsha_source"], "reason": cr["reason"], "aliyos": "3 עליות"}]
                shacharis = self._build_reading("שחרית", cr["display_title"], cr["reason"], cr["aliyah_count"],
                                                False, sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)

        elif is_rc and not is_shabbos:
            has_kriah = True
            sefer_torah_count_max = 1
            aliyah_count_max = 4
            rc = ROSH_CHODESH_READING["weekday"]
            sifrei = [{"sefer_number": 1, "opening_words": rc["opening_words"], "sefer": rc["sefer"],
                       "parsha_source": rc["parsha_source"], "reason": rc["reason"], "aliyos": "4 עליות"}]
            shacharis = self._build_reading("שחרית", rc["display_title"], rc["reason"], rc["aliyah_count"],
                                            False, sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)

        elif is_purim:
            has_kriah = True
            sefer_torah_count_max = 1
            aliyah_count_max = 3
            sifrei = PURIM_READING["sifrei_torah"]
            shacharis = self._build_reading("שחרית", PURIM_READING["display_title"], PURIM_READING["reason"],
                                            PURIM_READING["aliyah_count"], False, sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)

        elif is_mon_thu:
            has_kriah = True
            sefer_torah_count_max = 1
            aliyah_count_max = 3
            sifrei = [{"sefer_number": 1, "opening_words": parsha_info.get("opening_words", ""),
                       "sefer": parsha_info.get("sefer", ""), "parsha_source": weekly_parsha or "",
                       "reason": "שני וחמישי", "aliyos": "כהן, לוי, ישראל"}]
            shacharis = self._build_reading("שחרית", f"פרשת {weekly_parsha}" if weekly_parsha else "שני וחמישי",
                                            "שני וחמישי", 3, False, sifrei, shacharis_start, shacharis_end)
            readings.append(shacharis)

        # Optional: Korbanos at Mincha on Shlosh Esrei Middos
        if shlosh_esrei_middos and self._read_korbanos:
            sefer_korb = KORBANOS_READING.get("sifrei_torah", [])
            if sefer_korb:
                korbanos_reading = self._build_reading(
                    "מנחה",
                    KORBANOS_READING.get("display_title", ""),
                    KORBANOS_READING.get("reason", ""),
                    0,
                    False,
                    sefer_korb,
                    mincha_start,
                    mincha_end,
                )
                readings.append(korbanos_reading)
                has_kriah = True

        # Determine reason
        reason = ""
        if is_shabbos and is_rc and chan_day:
            reason = f"שבת ראש חודש חנוכה - פרשת {weekly_parsha}" if weekly_parsha else "שבת ראש חודש חנוכה"
        elif is_shabbos and is_rc:
            reason = f"שבת ראש חודש - פרשת {weekly_parsha}" if weekly_parsha else "שבת ראש חודש"
        elif is_shabbos and chan_day:
            reason = f"שבת חנוכה - פרשת {weekly_parsha}" if weekly_parsha else "שבת חנוכה"
        elif yom_tov:
            reason = yom_tov["data"].get("reason", "")
        elif is_yom_kippur:
            reason = "יום הכיפורים"
        elif is_fast:
            reason = fast_name or ""
        elif chan_day and is_rc:
            reason = f"ראש חודש + {CHANUKAH_READINGS[chan_day]['reason']}"
        elif chan_day:
            reason = CHANUKAH_READINGS[chan_day]["reason"]
        elif is_rc:
            reason = "ראש חודש"
        elif is_purim:
            reason = "פורים"
        elif is_shabbos:
            reason = f"שבת - פרשת {weekly_parsha}" if weekly_parsha else "שבת"
        elif is_mon_thu:
            reason = f"שני וחמישי - פרשת {weekly_parsha}" if weekly_parsha else "שני וחמישי"

        # Check for Nasi reading (1-13 Nissan)
        nasi_reading = None
        if hd.month == 1 and 1 <= hd.day <= 13:
            nasi_data = NESIIM_READINGS.get(hd.day)
            if nasi_data:
                nasi_reading = {
                    "day": hd.day,
                    "nasi": nasi_data["nasi"],
                    "opening_words": nasi_data["opening_words"],
                    "pesukim": nasi_data["pesukim"],
                }

        return readings, has_kriah, sefer_torah_count_max, aliyah_count_max, reason, nasi_reading

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if self.hass is None or not self._geo:
            if not self._geo:
                self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz or ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.datetime.now(tz)
        today = now.date()

        # Get readings for today and tomorrow (run blocking zmanim calls in executor)
        readings_today, has_kriah_today, sefer_max_today, aliyah_max_today, reason_today, nasi_today = \
            await self.hass.async_add_executor_job(self._get_readings_for_date, today, tz)
        
        tomorrow = today + timedelta(days=1)
        readings_tomorrow, has_kriah_tomorrow, sefer_max_tomorrow, aliyah_max_tomorrow, reason_tomorrow, nasi_tomorrow = \
            await self.hass.async_add_executor_job(self._get_readings_for_date, tomorrow, tz)

        # Determine which reading is "current" vs "next"
        # Find if we're in a reading window, before one, or past all today's readings
        current_reading: dict | None = None
        next_reading: dict | None = None
        showing_next = False  # True if we're showing upcoming (not current)
        
        # Check if we're currently in a reading window
        for reading in readings_today:
            ws, we = reading.get("window_start"), reading.get("window_end")
            if ws and we:
                try:
                    if datetime.datetime.fromisoformat(ws) <= now <= datetime.datetime.fromisoformat(we):
                        current_reading = reading
                        break
                except:
                    pass
        
        if current_reading:
            # We're in a reading window - show current
            showing_next = False
            display_reading = current_reading
            display_readings = [current_reading]
            display_reason = reason_today
            display_sefer_count = current_reading.get("sefer_torah_count", 1)
            display_aliyah_count = current_reading.get("aliyah_count", 3)
            display_nasi = nasi_today
        else:
            # Not in a reading window - find next upcoming
            showing_next = True
            
            # Check for upcoming reading today
            for reading in readings_today:
                ws = reading.get("window_start")
                if ws:
                    try:
                        if now < datetime.datetime.fromisoformat(ws):
                            next_reading = reading
                            break
                    except:
                        pass
            
            if next_reading:
                # There's still a reading later today (e.g., mincha)
                display_reading = next_reading
                display_readings = [next_reading]
                # For mincha, use the mincha-specific info
                display_reason = next_reading.get("reason", reason_today)
                display_sefer_count = next_reading.get("sefer_torah_count", 1)
                display_aliyah_count = next_reading.get("aliyah_count", 3)
                display_nasi = nasi_today
            elif has_kriah_tomorrow:
                # Show tomorrow's reading
                display_reading = readings_tomorrow[0] if readings_tomorrow else None
                display_readings = readings_tomorrow
                display_reason = reason_tomorrow
                display_sefer_count = sefer_max_tomorrow
                display_aliyah_count = aliyah_max_tomorrow
                display_nasi = nasi_tomorrow
            else:
                # No reading today or tomorrow - find next kriah day
                display_reading = None
                display_readings = []
                display_reason = ""
                display_sefer_count = 0
                display_aliyah_count = 0
                display_nasi = None
                # Look ahead up to 7 days
                for days_ahead in range(2, 8):
                    future_date = today + timedelta(days=days_ahead)
                    future_readings, future_has, future_sefer, future_aliyah, future_reason, future_nasi = \
                        await self.hass.async_add_executor_job(self._get_readings_for_date, future_date, tz)
                    if future_has:
                        display_reading = future_readings[0] if future_readings else None
                        display_readings = future_readings
                        display_reason = future_reason
                        display_sefer_count = future_sefer
                        display_aliyah_count = future_aliyah
                        display_nasi = future_nasi
                        break

        # Update last completed anchor for prep_now logic
        for reading in readings_today:
            we = reading.get("window_end")
            if we:
                try:
                    window_end_dt = datetime.datetime.fromisoformat(we)
                    if now > window_end_dt:
                        if self._last_completed_time is None or window_end_dt > self._last_completed_time:
                            self._last_completed_anchor = reading.get("_scroll_anchor", "")
                            self._last_completed_time = window_end_dt
                except:
                    pass

        # Prep now logic - true when showing_next and anchor differs from last completed
        prep_now = False
        if showing_next and display_reading:
            next_upcoming_anchor = display_reading.get("_scroll_anchor", "")
            # If we have a last completed anchor and it differs from next, prep is needed
            if self._last_completed_anchor and next_upcoming_anchor:
                if self._last_completed_anchor != next_upcoming_anchor:
                    prep_now = True

        hd = PHebrewDate.from_pydate(today)
        kriah_days = self._get_kriah_days_this_week(today, hd)

        # Build summary for state
        display_sifrei = display_reading.get("sifrei_torah", []) if display_reading else []
        summary = self._build_summary(display_sifrei)

        # Kriah_Now is true only when we're inside a reading window
        kriah_now = current_reading is not None

        # Build attributes with dynamic naming
        reason_key = "Reason_Next" if showing_next else "Reason_Today"
        
        attrs: dict[str, Any] = {
            reason_key: display_reason,
            "Sefer_Torah_Count": display_sefer_count,
            "Aliyah_Count": display_aliyah_count,
            "Kriah_Days": ", ".join(kriah_days),
            "Has_Kriah_Today": has_kriah_today,
            "Kriah_Now": kriah_now,
            "Prep_Now": prep_now,
        }

        # Flatten readings into header-style attributes
        tefilah_english = {"שחרית": "Shacharis", "מנחה": "Mincha", "ערבית": "Maariv"}
        for reading in display_readings:
            tefilah = reading.get("tefilah", "")
            tefilah_eng = tefilah_english.get(tefilah, tefilah)
            sifrei = reading.get("sifrei_torah", [])
            single_sefer = len(sifrei) == 1
            
            # Add tefilah header - mark as "הבא:" if showing next
            header = f"{tefilah} הבא:" if showing_next else tefilah
            attrs[header] = ""
            
            for sefer in sifrei:
                sefer_num = sefer.get("sefer_number", 1)
                opening = sefer.get("opening_words", "")
                source_sefer = sefer.get("sefer", "")
                parsha_source = sefer.get("parsha_source", "")
                reason = sefer.get("reason", "")
                
                # Build source string
                if reason and reason != parsha_source:
                    source_str = f"{source_sefer} - {parsha_source} ({reason})"
                else:
                    source_str = f"{source_sefer} - {parsha_source}"
                
                # Use simpler names when only 1 sefer (no "_Sefer_1")
                if single_sefer:
                    attrs[f"{tefilah_eng}_Opening"] = opening
                    attrs[f"{tefilah_eng}_Source"] = source_str
                else:
                    attrs[f"{tefilah_eng}_Sefer_{sefer_num}"] = opening
                    attrs[f"{tefilah_eng}_Sefer_{sefer_num}_Source"] = source_str

        # Add Nasi reading if applicable
        if display_nasi:
            nasi_header = "נשיא הבא:" if showing_next else "נשיא"
            attrs[nasi_header] = ""
            attrs["Nasi_Today"] = f"נשיא {display_nasi['nasi']}"
            attrs["Nasi_Opening"] = display_nasi["opening_words"]
            attrs["Nasi_Source"] = display_nasi["pesukim"]

        # State is the summary
        self._attr_native_value = summary if summary else None
        self._attr_extra_state_attributes = attrs
        
        self.async_write_ha_state()