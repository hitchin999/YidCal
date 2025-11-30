"""
custom_components/yidcal/special_prayer_sensor.py

Dynamic *tefillah* insertions with accurate halachic timing, plus Hoshanos.

What this sensor does
---------------------
• Exposes a **main state**: a human-readable string of active insertions joined with " - ".
• Adds **rich attributes** for Hoshanos, Aseres Yemei Teshuvah (עשי"ת), Hallel toggles, and more.
• Listens to `sensor.yidcal_holiday` and (optionally) `binary_sensor.yidcal_no_melucha` for context.

Main state (joined with " - ")
------------------------------
• מוריד הגשם / מוריד הטל  
  – Active window: dawn of 22 תשרי → dawn of 15 ניסן (גשם), otherwise טל.

• ותן טל ומטר לברכה / ותן ברכה  
  – Motzaei 5 כסלו (at havdalah offset) → Motzaei 15 ניסן (טל ומטר), otherwise ותן ברכה.

• יעלה ויבוא  
  – On: כל ראש חודש (חוץ מראש השנה), יום טוב וחול המועד.  
  – חלון זמן: מן צאת הכוכבים (אמש) עד צאת הכוכבים (הלילה) — כלומר לילה-עד-לילה (tzeis→tzeis).  
  – לא נאמר בחנוכה.

• אתה יצרת  
  – When שבת coincides with ראש חודש (from dawn → sunset).

• על הניסים  
  – On חנוכה or פורים.

• עננו  
  – On all minor fasts (dawn → מוצאי), and on תשעה באב (dawn → מוצאי).

• נחם  
  – תשעה באב only, from חצות היום → מוצאי.

• עשי"ת  
  – During עשרת ימי תשובה (see “Aseres Yemei Teshuvah” below), appears in **state** as the tag "עשי\"ת".

• אתה חוננתנו  
  – After melacha becomes permitted following any שבת/יום טוב until (civil) midnight.
    Uses `binary_sensor.yidcal_no_melucha` if available to detect when prohibition lifts;
    otherwise falls back to local havdalah offset.

Extra attributes
----------------
• "הושענות היום" – Which *Hoshana* is recited today (15–20 תשרי), empty otherwise.  
• Per-day Hoshanos labels – A fixed mapping for the six days (labels like "הושענות ליום א׳ …").  
• "הושענא רבה" – True on 21 תשרי (שביעי הושענא).  
• "עשי\"ת" – String of items during עשרת ימי תשובה:
    Motzaei R\"H (at havdalah offset) → Erev YK (until candle-lighting offset).
    On Shabbos within that window, omit "אבינו מלכנו".
    Example value: "ממעמקים - זכרינו - המלך - אבינו מלכנו"
• "עשרת ימי תשובה" – Boolean toggle indicating the AYT window is active.
• הלל / הלל השלם – חישוב מדויק:
    – הלל השלם: כל ימי חנוכה; סוכות וחוה״מ סוכות (כולל שבת חוה״מ); שמיני עצרת/שמחת תורה; שבועות (יום אחד בא״י, שני ימים בחו״ל); יום/יומי הראשון של פסח (א׳ בא״י; א׳-ב׳ בחו״ל).
    – חצי הלל: ראש חודש; כל שאר ימי פסח (כולל חוה״מ ושביעי/אחרון).
    – חנוכה גובר על ראש חודש (בר״ח טבת אומרים הלל שלם).

Timing & rollovers
------------------
• Solar times computed per HA location (72-minute dawn, configurable candle/havdalah offsets).  
• Hebrew date logic generally **rolls at havdalah** (צאת + ההבדלה).  
• יעלה ויבוא: נבחן בחלון **לילה-עד-לילה** (tzeis→tzeis) עבור ר״ח/יום טוב/חול המועד.  
• Hoshanos sequence **rolls at civil midnight**.  
• Fast-day windows and שבת-specific rules (e.g., omit "אבינו מלכנו" on Shabbos during עשי״ת) are honored.  
• "אתה חוננתנו": לאחר שמלאכה מותרת ועד חצות לילה אזרחית; משתמש ב־`binary_sensor.yidcal_no_melucha` אם קיים, אחרת נופל להבדלה מקומית.

Dependencies
------------
• `sensor.yidcal_holiday` (required) for holiday/fast context.  
• `binary_sensor.yidcal_no_melucha` (optional) to refine "אתה חוננתנו".

Output summary
--------------
State example (joined with " - "):  
"מוריד הגשם - טל ומטר - יעלה ויבוא - על הניסים - עננו - עשי\"ת - אתה חוננתנו"

Attributes include: "הושענות היום", per-day Hoshanos, "הושענא רבה", "עשי\"ת", "עשרת ימי תשובה",
"הלל", "הלל השלם", and boolean flags for each insertion as applicable.
"""

from __future__ import annotations

from datetime import timedelta, time, date, datetime
import calendar
from zoneinfo import ZoneInfo
import re

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from pyluach.dates import GregorianDate
from pyluach import parshios
from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .device import YidCalDisplayDevice
from .const import DOMAIN
from .zman_sensors import get_geo


HOLIDAY_SENSOR = "sensor.yidcal_holiday"
NO_MELOCHA_SENSOR = "binary_sensor.yidcal_no_melucha"


# ---------- Rounding helpers (same semantics as other YidCal sensors) ----------

def _round_half_up(dt: datetime) -> datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    """Always bump to the *next* minute (Motzi-style)."""
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


# Hoshanos sequences depend on the weekday of 15 Tishrei (first day of Sukkos, chutz la'aretz).
# Keys are Python weekday() where Monday=0 ... Sunday=6.
HOSHANOS_TABLE = {
    0: ["למען אמתך", "אבן שתיה", "אערוך שועי", "אום אני חומה", "אל למושעות", "אום נצורה"],
    1: ["למען אמתך", "אבן שתיה", "אערוך שועי", "אל למושעות", "אום נצורה", "אדון המושיע"],
    3: ["למען אמתך", "אבן שתיה", "אום נצורה", "אערוך שועי", "אל למושעות", "אדון המושיע"],
    5: ["אום נצורה", "למען אמתך", "אערוך שועי", "אבן שתיה", "אל למושעות", "אדון המושיע"],
}

HOSH_DAY_LABELS = [
    "הושענות ליום א׳ דיום טוב",
    "הושענות ליום ב׳ דיום טוב",
    "הושענות ליום א׳ דחול המועד סוכות",
    "הושענות ליום ב׳ דחול המועד סוכות",
    "הושענות ליום ג׳ דחול המועד סוכות",
    "הושענות ליום ד׳ דחול המועד סוכות",
]


def _as_true(v) -> bool:
    """Return True only for the boolean True, or the string 'true' (case-insensitive)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return False


def _diaspora_sets(diaspora: bool):
    """
    Return (YOMTOV_ATTR_KEYS, FULL_HALLEL_ATTR_KEYS, HALF_HALLEL_ATTR_KEYS, HOSH_DAY_LABELS)
    per locale.
    """
    if diaspora:
        yomtov_keys = [
            "סוכות", "סוכות א׳", "סוכות ב׳", "סוכות א׳ וב׳",
            "שמיני עצרת", "שמחת תורה", "שמיני עצרת/שמחת תורה",
            "פסח", "פסח א׳", "פסח ב׳", "פסח א׳ וב׳",
            "שביעי של פסח", "אחרון של פסח", "שביעי/אחרון של פסח",
            "שבועות א׳", "שבועות ב׳", "שבועות א׳ וב׳",
        ]
        full_hallel = [
            "חנוכה", "זאת חנוכה",
            "סוכות", "סוכות א׳", "סוכות ב׳", "סוכות א׳ וב׳",
            "שבת חול המועד סוכות", "חול המועד סוכות",
            "שמיני עצרת", "שמחת תורה", "שמיני עצרת/שמחת תורה",
            "שבועות א׳", "שבועות ב׳", "שבועות א׳ וב׳",
            "פסח א׳", "פסח ב׳", "פסח א׳ וב׳",
        ]
        half_hallel = [
            "ראש חודש",
            "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח",
            "ג׳ דחול המועד פסח", "ד׳ דחול המועד פסח",
            "שבת חול המועד פסח", "חול המועד פסח",
            "שביעי של פסח", "אחרון של פסח", "שביעי/אחרון של פסח",
        ]
        hosh_labels = [
            "הושענות ליום א׳ דיום טוב",
            "הושענות ליום ב׳ דיום טוב",
            "הושענות ליום א׳ דחול המועד סוכות",
            "הושענות ליום ב׳ דחול המועד סוכות",
            "הושענות ליום ג׳ דחול המועד סוכות",
            "הושענות ליום ד׳ דחול המועד סוכות",
        ]
    else:
        # EY: 1 YT day (15 Tishrei), 5 CH"M; one-day Pesach/Shavuos; Simchas Torah = with SA
        yomtov_keys = [
            "סוכות",
            "שמיני עצרת", "שמיני עצרת/שמחת תורה",
            "פסח", "שביעי של פסח",
            "שבועות",
        ]
        full_hallel = [
            "חנוכה", "זאת חנוכה",
            "סוכות", "שבת חול המועד סוכות", "חול המועד סוכות",
            "שמיני עצרת", "שמיני עצרת/שמחת תורה",
            "שבועות",
            "פסח",
        ]
        half_hallel = [
            "ראש חודש",
            "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח",
            "ג׳ דחול המועד פסח", "ד׳ דחול המועד פסח",
            "שבת חול המועד פסח", "חול המועד פסח",
            "שביעי של פסח",
        ]
        hosh_labels = [
            "הושענות ליום דיום טוב",
            "הושענות ליום א׳ דחול המועד סוכות",
            "הושענות ליום ב׳ דחול המועד סוכות",
            "הושענות ליום ג׳ דחול המועד סוכות",
            "הושענות ליום ד׳ דחול המועד סוכות",
            "הושענות ליום ה׳ דחול המועד סוכות",
        ]
    return yomtov_keys, full_hallel, half_hallel, hosh_labels


def _year_hoshanos_sequence(hebrew_year: int) -> list[str]:
    """Return the Hoshanos sequence for 15–20 Tishrei of the given Hebrew year."""
    first_py = PHebrewDate(hebrew_year, 7, 15).to_pydate()
    return HOSHANOS_TABLE.get(first_py.weekday(), [])


def _format_hebrew_year(year: int) -> str:
    """
    Format a Hebrew year like 5787 -> 'תשפ״ז'.
    """
    GERESH = "׳"      # U+05F3
    GERSHAYIM = "״"   # U+05F4

    n = year % 1000
    if n == 0:
        return "ת״"

    parts: list[str] = []

    # Hundreds
    while n >= 400:
        parts.append("ת")
        n -= 400
    if n >= 300:
        parts.append("ש")
        n -= 300
    if n >= 200:
        parts.append("ר")
        n -= 200
    if n >= 100:
        parts.append("ק")
        n -= 100

    # Tens + Ones with 15/16 exceptions
    tens = (n // 10) * 10
    ones = n % 10

    tens_map = {90: "צ", 80: "פ", 70: "ע", 60: "ס", 50: "נ", 40: "מ", 30: "ל", 20: "כ", 10: "י"}
    ones_map = {9: "ט", 8: "ח", 7: "ז", 6: "ו", 5: "ה", 4: "ד", 3: "ג", 2: "ב", 1: "א"}

    if tens + ones in (15, 16):
        parts.append("ט")
        parts.append("ו" if ones == 6 else "ז")
    else:
        if tens:
            parts.append(tens_map[tens])
        if ones:
            parts.append(ones_map[ones])

    if len(parts) == 1:
        return parts[0] + GERESH
    else:
        return "".join(parts[:-1]) + GERSHAYIM + parts[-1]


class SpecialPrayerSensor(YidCalDisplayDevice, SensorEntity):
    _attr_name = "Special Prayer"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int):
        super().__init__()
        slug = "special_prayer"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._candle = candle_offset
        self._havdalah = havdalah_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._diaspora = cfg.get("diaspora", True)
        self._geo: GeoLocation | None = None

        self._attr_extra_state_attributes: dict[str, object] = {}
        self._state = ""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Ensure geo is cached for Zmanim
        self._geo = await get_geo(self.hass)

        @callback
        def _refresh(_):
            # Just push HA to read native_value again
            self.async_write_ha_state()

        # Holiday/no-melucha updates
        unsub = async_track_state_change_event(
            self.hass, [HOLIDAY_SENSOR, NO_MELOCHA_SENSOR], _refresh
        )
        self._register_listener(unsub)

        # Top-of-minute tick (handles dawn/tzeis/havdalah boundaries)
        unsub_min = async_track_time_change(self.hass, _refresh, second=0)
        self._register_listener(unsub_min)

        _refresh(None)

    @property
    def native_value(self) -> str:
        try:
            # If geo isn't ready yet, don't crash – just keep last state
            if self._geo is None:
                return self._state

            tz = self._tz

            # Snap "now" to the minute so all comparisons line up with rounded Zmanim
            now = dt_util.now().astimezone(tz)
            now = now.replace(second=0, microsecond=0)
            today = now.date()

            yomtov_keys, full_hallel_keys, half_hallel_keys, hosh_labels = _diaspora_sets(
                self._diaspora
            )

            # ---------- Zmanim for today / yesterday / tomorrow ----------
            cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
            cal_yesterday = ZmanimCalendar(geo_location=self._geo, date=today - timedelta(days=1))
            cal_tomorrow = ZmanimCalendar(geo_location=self._geo, date=today + timedelta(days=1))

            sunrise = cal_today.sunrise().astimezone(tz)
            sunset = cal_today.sunset().astimezone(tz)

            # Dawn, candle-lighting, havdalah – with same rounding semantics as other sensors
            dawn = _round_half_up(sunrise - timedelta(minutes=72))
            candle_time = _round_half_up(sunset - timedelta(minutes=self._candle))
            havdala_raw = sunset + timedelta(minutes=self._havdalah)
            havdala = _round_ceil(havdala_raw)

            # Chatzos (no need for rounding; we never hit it exactly)
            hal_mid = sunrise + (sunset - sunrise) / 2

            # Nightfall (tzeis) window: prev_tzeis .. next_tzeis, round Motzi-style
            yest_tzeis = _round_ceil(
                cal_yesterday.sunset().astimezone(tz) + timedelta(minutes=self._havdalah)
            )
            tod_tzeis = havdala  # already rounded
            tom_tzeis = _round_ceil(
                cal_tomorrow.sunset().astimezone(tz) + timedelta(minutes=self._havdalah)
            )

            if now < tod_tzeis:
                prev_tzeis, next_tzeis = yest_tzeis, tod_tzeis
            else:
                prev_tzeis, next_tzeis = tod_tzeis, tom_tzeis

            night_inclusive_window = prev_tzeis <= now < next_tzeis

            # ---------- Hebrew dates (two flavors) ----------
            # Halachic date: flip at havdalah (rounded)
            hd = PHebrewDate.from_pydate(today)
            if now >= havdala:
                hd = hd + 1
            day = hd.day
            m_he = hd.month_name(hebrew=True)

            # Hebrew date by sunset-only (used for AYT boundaries)
            hd_sun = PHebrewDate.from_pydate(today)
            if now >= sunset:
                hd_sun = hd_sun + 1
            m_num_sun = hd_sun.month
            d_num_sun = hd_sun.day
            
            # ---------- פרשת המן (ג׳ בשלח) ----------
            # True only on Tuesday of Parshas Beshalach, from Alos (dawn) until Tzeis (havdala)
            # Use Israel vs Diaspora parsha schedule appropriately.
            parsha_today = parshios.getparsha_string(
                GregorianDate(today.year, today.month, today.day),
                israel=(not self._diaspora),
                hebrew=True,
            )
            is_parshas_haman = (
                now.weekday() == 1  # Tuesday (Mon=0)
                and parsha_today == "בשלח"
                and dawn <= now < havdala
            )

            # ---------- מוריד הגשם / מוריד הטל ----------
            is_morid_geshem = (
                (m_he == "תשרי" and (day > 22 or (day == 22 and now >= dawn)))
                or m_he in ["חשון", "כסלו", "טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
            )
            is_morid_tal = not is_morid_geshem

            # ---------- ותן טל ומטר / ותן ברכה ----------
            # Halachic date (flip at rounded havdalah)
            halachic_date = today + (timedelta(days=1) if now >= havdala else timedelta(days=0))
            hd_hal = PHebrewDate.from_pydate(halachic_date)

            # After first night of Pesach we always say "ותן ברכה"
            if hd_hal.month == 1 and hd_hal.day >= 15:
                is_tal_umatar = False
            else:
                if self._diaspora:
                    # Chutz LaAretz: Dec 4 (Dec 5 in Gregorian leap years), at Ma'ariv
                    dec_year = now.year - 1 if now.month <= 4 else now.year
                    start_day = 5 if calendar.isleap(dec_year) else 4
                    start_gdate = date(dec_year, 12, start_day)
                    cal_start = ZmanimCalendar(
                        geo_location=self._geo,
                        date=start_gdate,
                    )
                    start_sunset = cal_start.sunset().astimezone(tz)
                    start_dt = _round_ceil(
                        start_sunset + timedelta(minutes=self._havdalah)
                    )
                    is_tal_umatar = now >= start_dt
                else:
                    # Eretz Yisrael: from 7 Cheshvan (Ma'ariv) until Pesach
                    is_tal_umatar = (
                        (hd_hal.month == 8 and hd_hal.day >= 7)  # from 7 Cheshvan
                        or (9 <= hd_hal.month <= 13)            # Kislev–Adar/A II
                        or (hd_hal.month == 1 and hd_hal.day < 15)  # Nisan < 15
                    )

            is_ten_beracha = not is_tal_umatar

            # ---------- Holiday context ----------
            st_hol = self.hass.states.get(HOLIDAY_SENSOR)
            hol = st_hol.attributes if st_hol and getattr(st_hol, "attributes", None) else {}

            # ---------- יעלה ויבוא ----------
            is_yomtov = any(_as_true(hol.get(k)) for k in yomtov_keys)
            is_hallel_shalem = any(_as_true(hol.get(k)) for k in full_hallel_keys)
            is_hallel_half = (not is_hallel_shalem) and any(
                _as_true(hol.get(k)) for k in half_hallel_keys
            )
            is_hallel = is_hallel_shalem or is_hallel_half

            # Chol HaMoed — require True values and match key names
            has_chm = any(
                re.search(r"חול.?המועד", k) and _as_true(v)
                for k, v in hol.items()
            )

            # Chanukah does NOT trigger YVY
            is_chanukah = _as_true(hol.get("חנוכה"))

            # Rosh Chodesh (exclude R"H)
            is_rh = (hd.month == 7 and hd.day in (1, 2))
            is_rc = (day in (1, 30)) and not is_rh

            yaaleh_day = (is_rc or is_yomtov or has_chm) and night_inclusive_window
            is_yaaleh_veyavo = bool(yaaleh_day) and not is_chanukah

            # ---------- אתה יצרת ----------
            is_atah_yatzarta = (
                is_rc
                and now.weekday() == 5
                and dawn <= now < sunset
            )

            # ---------- על הניסים ----------
            is_purim = _as_true(hol.get("פורים"))
            is_chanukah_holiday = _as_true(hol.get("חנוכה"))
            is_al_hanissim = is_purim or is_chanukah_holiday

            # ---------- Fast days ----------
            is_tisha_bav = hd.month == 5 and hd.day == 9
            is_minor_fast = any(
                _as_true(v)
                and ("כיפור" not in k)
                and (k.startswith("צום") or k.startswith("תענית"))
                for k, v in hol.items()
            )
            is_anenu = False
            is_nachem = False
            if is_tisha_bav:
                if dawn <= now <= havdala:
                    is_anenu = True
                if hal_mid <= now <= havdala:
                    is_nachem = True
            elif is_minor_fast and dawn <= now <= havdala:
                is_anenu = True

            # ---------- עשרת ימי תשובה ----------
            is_tishrei_sun = (m_num_sun == 7)
            is_ayt_toggle = False
            ayt_str = ""
            if is_tishrei_sun and 3 <= d_num_sun <= 9:
                if not (d_num_sun == 3 and now < havdala):
                    is_ayt_toggle = True

            if is_ayt_toggle:
                shabbos_window = (
                    (now.weekday() == 4 and now >= candle_time)
                    or (now.weekday() == 5 and now <= havdala)
                )
                ayt_list = (
                    ["ממעמקים", "זכרינו", "המלך"]
                    if shabbos_window
                    else ["ממעמקים", "זכרינו", "המלך", "אבינו מלכנו"]
                )
                ayt_str = " - ".join(ayt_list)

            # ---------- אתה חוננתנו ----------
            # Purely local logic: after the rounded havdalah of Shabbos until civil 23:59
            motzash_tog = False
            if now.weekday() == 5 and now.time() < time(23, 59):
                if now >= havdala:
                    motzash_tog = True

            # ---------- Hoshanos ----------
            hd_ref = PHebrewDate.from_pydate(today)
            ref_year = hd_ref.year
            boundary = 23 if self._diaspora else 22
            if (
                hd_ref.month > 7
                or (hd_ref.month == 7 and hd_ref.day > boundary)
                or (hd_ref.month == 7 and hd_ref.day == boundary and now >= havdala)
            ):
                ref_year = hd_ref.year + 1

            seq = _year_hoshanos_sequence(ref_year)

            # Use HALACHIC day so the Day-1 Hoshana appears right after tzeis
            hd_hosh = hd
            if seq and hd_hosh.month == 7 and 15 <= hd_hosh.day <= 20:
                hosh_today = seq[hd_hosh.day - 15]
                is_hoshana_rabba_today = False
            else:
                hosh_today = ""
                is_hoshana_rabba_today = (hd_hosh.month == 7 and hd_hosh.day == 21)

            per_day = {
                label: seq[i - 1]
                for i, label in enumerate(hosh_labels, start=1)
                if seq and i <= len(seq)
            }

            # ---------- attributes ----------
            attrs: dict[str, object] = {}
            attrs["הושענות פאר יאר"] = _format_hebrew_year(ref_year)
            attrs["הושענות היום"] = hosh_today
            attrs.update(per_day)
            attrs["הושענא רבה"] = is_hoshana_rabba_today
            attrs["עשי\"ת"] = ayt_str
            attrs["עשרת ימי תשובה"] = is_ayt_toggle
            attrs["מוריד הגשם"] = is_morid_geshem
            attrs["מוריד הטל"] = is_morid_tal
            attrs["טל ומטר"] = is_tal_umatar
            attrs["ותן ברכה"] = is_ten_beracha
            attrs["יעלה ויבוא"] = is_yaaleh_veyavo
            attrs["אתה יצרת"] = is_atah_yatzarta
            attrs["על הניסים"] = is_al_hanissim
            attrs["על הניסים - בימי מרדכי"] = is_purim
            attrs["על הניסים - בימי מתתיהו"] = is_chanukah_holiday
            attrs["עננו"] = is_anenu
            attrs["נחם"] = is_nachem
            attrs["הלל"] = is_hallel
            attrs["הלל השלם"] = is_hallel_shalem
            attrs["אתה חוננתנו"] = motzash_tog
            attrs["פרשת המן"] = is_parshas_haman

            self._attr_extra_state_attributes = attrs

            # ---------- state ----------
            parts: list[str] = []
            parts.append("מוריד הגשם" if is_morid_geshem else "מוריד הטל")
            parts.append("טל ומטר" if is_tal_umatar else "ותן ברכה")
            if is_yaaleh_veyavo:
                parts.append("יעלה ויבוא")
            if is_atah_yatzarta:
                parts.append("אתה יצרת")
            if is_chanukah_holiday:
                parts.append("על הניסים - בימי מתתיהו")
            elif is_purim:
                parts.append("על הניסים - בימי מרדכי")
            if is_anenu:
                parts.append("עננו")
            if is_nachem:
                parts.append("נחם")
            if is_ayt_toggle:
                parts.append("עשי\"ת")
            if motzash_tog:
                parts.append("אתה חוננתנו")
            if is_parshas_haman:
                parts.append("פרשת המן")

            self._state = " - ".join(parts)
            return self._state

        except Exception as exc:
            # In case of any bug, expose it as an attribute instead of killing the entity
            self._attr_extra_state_attributes = {"error": repr(exc)}
            self._state = ""
            return ""
