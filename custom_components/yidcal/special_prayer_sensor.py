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
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import re

from astral import LocationInfo
from astral.sun import sun
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.dt import now as dt_now
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalDevice

HOLIDAY_SENSOR = "sensor.yidcal_holiday"
NO_MELOCHA_SENSOR = "binary_sensor.yidcal_no_melucha"

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

# Days that require FULL Hallel (keys from sensor.yidcal_holiday attributes)
FULL_HALLEL_ATTR_KEYS = [
    # Chanukah
    "חנוכה", "זאת חנוכה",
    # Sukkos & end days
    "סוכות", "סוכות א׳", "סוכות ב׳", "סוכות א׳ וב׳",
    "שבת חול המועד סוכות", "חול המועד סוכות",
    "שמיני עצרת", "שמחת תורה", "שמיני עצרת/שמחת תורה",
    # Shavuos
    "שבועות א׳", "שבועות ב׳", "שבועות א׳ וב׳",
    # Pesach first day(s)
    "פסח", "פסח א׳", "פסח ב׳", "פסח א׳ וב׳",
]

# Days that require HALF Hallel (never when Full already applies)
HALF_HALLEL_ATTR_KEYS = [
    "ראש חודש",
    # Pesach (all non-first-day(s) incl. Shabbos CH”M and last day(s))
    "א׳ דחול המועד פסח", "ב׳ דחול המועד פסח",
    "ג׳ דחול המועד פסח", "ד׳ דחול המועד פסח",
    "שבת חול המועד פסח",
    "חול המועד פסח",  # in case you also expose a generic CH”M key
    "שביעי של פסח", "אחרון של פסח", "שביעי/אחרון של פסח",
    # (Do NOT include "פסח שני" here — no Hallel)
]

# Attribute keys on sensor.yidcal_holiday that represent actual Yom Tov days (Diaspora)
YOMTOV_ATTR_KEYS = [
    # Sukkos & end days
    "סוכות", "סוכות א׳", "סוכות ב׳", "סוכות א׳ וב׳",
    "שמיני עצרת", "שמחת תורה", "שמיני עצרת/שמחת תורה",
    # Pesach & end days
    "פסח", "פסח א׳", "פסח ב׳", "פסח א׳ וב׳",
    "שביעי של פסח", "אחרון של פסח", "שביעי/אחרון של פסח",
    # Shavuos
    "שבועות א׳", "שבועות ב׳", "שבועות א׳ וב׳",
    # (Intentionally excluding Rosh Hashanah from YVY)
]

def _year_hoshanos_sequence(hebrew_year: int) -> list[str]:
    """Return the Hoshanos sequence for 15–20 Tishrei of the given Hebrew year."""
    first_py = PHebrewDate(hebrew_year, 7, 15).to_pydate()
    return HOSHANOS_TABLE.get(first_py.weekday(), [])


def _format_hebrew_year(year: int) -> str:
    """
    Format a Hebrew year like 5787 -> 'תשפ״ז'.
    Rules:
    • Drop the thousands (i.e., use year % 1000).
    • Use special forms for 15 (ט״ו) and 16 (ט״ז).
    • Add gershayim (״ U+05F4) before the last letter if 2+ letters,
      or geresh (׳ U+05F3) after the single letter.
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
        parts.append("ש"); n -= 300
    if n >= 200:
        parts.append("ר"); n -= 200
    if n >= 100:
        parts.append("ק"); n -= 100

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


class SpecialPrayerSensor(YidCalDevice, SensorEntity):
    _attr_name = "Special Prayer"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int):
        super().__init__()
        slug = "special_prayer"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._attr_extra_state_attributes: dict[str, object] = {}
        self._state = ""

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _refresh(_):
            self.async_write_ha_state()

        unsub = async_track_state_change_event(
            self.hass, [HOLIDAY_SENSOR, NO_MELOCHA_SENSOR], _refresh
        )
        self._register_listener(unsub)
        _refresh(None)

    @property
    def native_value(self) -> str:
        try:
            tzname = self.hass.config.time_zone
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude
            if not tzname or lat is None or lon is None:
                return ""

            now = dt_now()
            today = now.date()
            tz = ZoneInfo(tzname)
            loc = LocationInfo("home", "", tzname, lat, lon)
            st = sun(loc.observer, date=today, tzinfo=tz)
            sunrise = st["sunrise"]
            sunset = st["sunset"]
            dawn = sunrise - timedelta(minutes=72)
            candle_time = sunset - timedelta(minutes=self._candle)
            havdala = sunset + timedelta(minutes=self._havdalah)
            hal_mid = sunrise + (sunset - sunrise) / 2
            # Nightfall (tzeis) window boundaries for the *current halachic day*
            # last_tzeis .. this_tzeis is the full halachic "night→night" span
            st_yesterday = sun(loc.observer, date=today - timedelta(days=1), tzinfo=tz)
            last_tzeis = st_yesterday["sunset"] + timedelta(minutes=self._havdalah)
            this_tzeis = havdala  # today’s sunset + havdalah offset
            
            # Windows we’ll use:
            day_window = (now >= dawn) and (now < this_tzeis)
            night_inclusive_window = (now >= last_tzeis) and (now < this_tzeis)

            # Hebrew date by halachic rollover (havdalah)
            hd = PHebrewDate.from_pydate(today)
            if now >= havdala:
                hd = hd + 1
            day = hd.day
            m_he = hd.month_name(hebrew=True)

            # Hebrew date by sunset rollover (used for AYT window boundaries)
            hd_sun = PHebrewDate.from_pydate(today)
            if now >= sunset:
                hd_sun = hd_sun + 1
            m_num_sun = hd_sun.month
            d_num_sun = hd_sun.day

            # ---------- מוריד הגשם / מוריד הטל ----------
            is_morid_geshem = (
                (m_he == "תשרי" and (day > 22 or (day == 22 and now >= dawn)))
                or m_he in ["חשון", "כסלו", "טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
            )
            is_morid_tal = not is_morid_geshem

            # ---------- ותן טל ומטר / ותן ברכה ----------
            is_tal_umatar = (
                (m_he == "כסלו" and (day > 5 or (day == 5 and now >= havdala)))
                or m_he in ["טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now <= havdala)))
            )
            is_ten_beracha = not is_tal_umatar

            # Holiday context
            st_hol = self.hass.states.get(HOLIDAY_SENSOR)
            hol = st_hol.attributes if st_hol else {}
            
            # ---------- יעלה ויבוא ----------
            # Strict booleans from holiday attributes (ignore strings like "אסרו חג סוכות")
            is_yomtov = any(_as_true(hol.get(k)) for k in YOMTOV_ATTR_KEYS)
            
            # Chol HaMoed — require True values and match key names
            has_chm = any(
                re.search(r"חול.?המועד", k) and _as_true(v)
                for k, v in hol.items()
            )
            
            # Chanukah does NOT trigger YVY (kept for Al HaNissim elsewhere)
            is_chanukah = _as_true(hol.get("חנוכה"))
            
            # Rosh Chodesh (exclude Tishrei to avoid R"H via RC branch; R"H is covered by Yom Tov anyway)
            is_rc = ((day == 1) or (day == 30)) and (m_he != "תשרי")
            
            # Final rule: ALL of these use a full halachic night→night window
            yaaleh_day = (is_rc or is_yomtov or has_chm) and night_inclusive_window
            is_yaaleh_veyavo = bool(yaaleh_day)

            # ---------- אתה יצרת ----------
            is_atah_yatzarta = (
                is_rc and now.weekday() == 5 and sunrise - timedelta(minutes=72) <= now < sunset
            )

            # ---------- על הניסים ----------
            is_purim = _as_true(hol.get("פורים"))
            is_chanukah_holiday = _as_true(hol.get("חנוכה"))
            is_al_hanissim = is_purim or is_chanukah_holiday

            # ---------- Fast days ----------
            is_tisha_bav = hd.month == 5 and hd.day == 9
            is_minor_fast = any(
                bool(v)
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

            # ---------- הלל ----------
            # Full Hallel if ANY full key is true
            is_hallel_shalem = any(_as_true(hol.get(k)) for k in FULL_HALLEL_ATTR_KEYS)
            
            # Half Hallel if ANY half key is true, but never when Full already applies
            is_hallel_half = (not is_hallel_shalem) and any(
                _as_true(hol.get(k)) for k in HALF_HALLEL_ATTR_KEYS
            )
            
            # Overall Hallel toggle (either full or half)
            is_hallel = is_hallel_shalem or is_hallel_half

            # ---------- אתה חוננתנו ----------
            no_melucha = self.hass.states.get(NO_MELOCHA_SENSOR)
            was_no_melucha = bool(no_melucha and no_melucha.state == "on")
            is_after_havdala = now >= havdala
            motzash_tog = False
            if not was_no_melucha and is_after_havdala and now.time() < time(23, 59):
                motzash_tog = True

            # ---------- Hoshanos (always populate future mapping) ----------
            # Civil-date Hebrew (no halachic rollover) for choosing reference year
            hd_civil = PHebrewDate.from_pydate(today)

            # Reference year rule (Chutz La'Aretz):
            # After Simchas Torah (23 Tishrei) at havdalah → next year.
            # If month > Tishrei OR day > 23 in Tishrei, we’re past Sukkos → next year.
            # If exactly 23 Tishrei, roll after havdalah.
            ref_year = hd_civil.year
            if (
                hd_civil.month > 7  # Cheshvan..Elul
                or (hd_civil.month == 7 and hd_civil.day > 23)  # 24+ Tishrei
                or (hd_civil.month == 7 and hd_civil.day == 23 and now >= havdala)  # motzaei 23
            ):
                ref_year = hd_civil.year + 1

            seq = _year_hoshanos_sequence(ref_year)

            # Today's specific Hoshana only during 15–20 Tishrei (civil-day window)
            hosh_today = (
                seq[hd_civil.day - 15]
                if (hd_civil.month == 7 and 15 <= hd_civil.day <= 20 and seq)
                else ""
            )
            is_hoshana_rabba_today = bool(hd_civil.month == 7 and hd_civil.day == 21)

            per_day = {
                label: seq[i - 1]
                for i, label in enumerate(HOSH_DAY_LABELS, start=1)
                if seq and i <= len(seq)
            }

            # ---------- attributes ----------
            attrs: dict[str, object] = {}
            # Requested rename: show the reference year under "הושענות פאר יאר"
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
            # More detailed Al HaNissim flags with correct spelling
            attrs["על הניסים"] = is_al_hanissim
            attrs["על הניסים - בימי מרדכי"] = is_purim
            attrs["על הניסים - בימי מתתיהו"] = is_chanukah_holiday
            attrs["עננו"] = is_anenu
            attrs["נחם"] = is_nachem
            attrs["הלל"] = is_hallel
            attrs["הלל השלם"] = is_hallel_shalem
            attrs["אתה חוננתנו"] = motzash_tog



            self._attr_extra_state_attributes = attrs

            # ---------- state ----------
            parts = []
            parts.append("מוריד הגשם" if is_morid_geshem else "מוריד הטל")
            parts.append("טל ומטר" if is_tal_umatar else "ותן ברכה")
            if is_yaaleh_veyavo:
                parts.append("יעלה ויבוא")
            if is_atah_yatzarta:
                parts.append("אתה יצרת")
            # Show detailed Al HaNissim text with corrected spelling
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

            self._state = " - ".join(parts)
            return self._state

        except Exception as exc:
            self._attr_extra_state_attributes = {"error": repr(exc)}
            return ""
