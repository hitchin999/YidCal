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
  – On: כל ראש חודש (חוץ מראש השנה) מן העמוד השחר;  
  – Also on: יום טוב, חנוכה, חול המועד (any variant).

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
• הלל / הלל השלם – Toggles derived from holiday context (e.g., חנוכה, סוכות/חוה\"מ, שבועות, פסח day-specific).

Timing & rollovers
------------------
• Solar times computed per HA location (72-minute dawn, configurable candle/havdalah offsets).  
• Hebrew date logic generally **rolls at havdalah**; Hoshanos sequence **rolls at civil midnight**.  
• Fast-day windows and שבת-specific rules (e.g., omit "אבינו מלכנו") are honored.

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


def _year_hoshanos_sequence(hd_civil_today: PHebrewDate) -> list[str]:
    year = hd_civil_today.year
    first_py = PHebrewDate(year, 7, 15).to_pydate()
    return HOSHANOS_TABLE.get(first_py.weekday(), [])


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

            hd = PHebrewDate.from_pydate(today)
            if now >= havdala:
                hd = hd + 1
            day = hd.day
            m_he = hd.month_name(hebrew=True)

            hd_sun = PHebrewDate.from_pydate(today)
            if now >= sunset:
                hd_sun = hd_sun + 1
            m_num_sun = hd_sun.month
            d_num_sun = hd_sun.day

            # ---------- rain / tal ----------
            is_morid_geshem = (
                (m_he == "תשרי" and (day > 22 or (day == 22 and now >= dawn)))
                or m_he in ["חשון", "כסלו", "טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
            )
            is_morid_tal = not is_morid_geshem

            is_tal_umatar = (
                (m_he == "כסלו" and (day > 5 or (day == 5 and now >= havdala)))
                or m_he in ["טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now <= havdala)))
            )
            is_ten_beracha = not is_tal_umatar

            st_hol = self.hass.states.get(HOLIDAY_SENSOR)
            hol = st_hol.attributes if st_hol else {}

            # ---------- יעלה ויבוא ----------
            text_attrs = " ".join(hol.keys())
            has_chm = bool(re.search("חול.?המועד", text_attrs))
            is_yomtov = bool(hol.get("יום טוב"))
            is_chanukah = bool(hol.get("חנוכה"))
            is_rc = ((day == 1) or (day == 30)) and (m_he != "תשרי")
            is_yaaleh_veyavo = is_rc or has_chm or is_chanukah or is_yomtov

            # ---------- אתה יצרת ----------
            is_atah_yatzarta = (
                is_rc and now.weekday() == 5 and sunrise - timedelta(minutes=72) <= now < sunset
            )

            # ---------- על הניסים ----------
            is_al_hanissim = bool(hol.get("חנוכה") or hol.get("פורים"))

            # ---------- fast days ----------
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
            is_hallel = bool(
                hol.get("חנוכה")
                or hol.get("חול המועד")
                or hol.get("שבועות")
                or hol.get("סוכות")
                or hol.get("פסח")
            )
            is_hallel_shalem = bool(
                hol.get("חנוכה")
                or hol.get("סוכות")
                or hol.get("חול המועד סוכות")
                or hol.get("שבועות")
                or (hol.get("פסח") and day in [15, 16])
            )

            # ---------- אתה חוננתנו ----------
            no_melucha = self.hass.states.get(NO_MELOCHA_SENSOR)
            motzash_tog = False
            
            # Only ever show until (civil) midnight tonight
            if now.time() < time(23, 59):
                if no_melucha:
                    # Turn on only if the prohibition lifted tonight (on -> off after havdala)
                    try:
                        nm_is_off = (no_melucha.state == "off")
                        # HA stores last_changed in UTC; compare in local tz
                        lc_local = no_melucha.last_changed.astimezone(tz) if no_melucha.last_changed else None
                        nm_lifted_tonight = (
                            nm_is_off
                            and lc_local is not None
                            and lc_local >= havdala                 # flipped after tonight's havdala
                            and lc_local.date() == now.date()       # and on this civil date
                        )
                        motzash_tog = nm_lifted_tonight
                    except Exception:
                        motzash_tog = False
                else:
                    # Fallback when no-melucha sensor isn't available:
                    # only Motzaei Shabbos or Motzaei Yom Tov after havdala
                    was_shabbos = (now.weekday() == 5)  # Saturday night
                    was_yomtov_today = bool(st_hol and st_hol.attributes.get("יום טוב"))
                    motzash_tog = (now >= havdala) and (was_shabbos or was_yomtov_today)

            # ---------- Hoshanos ----------
            hd_civil = PHebrewDate.from_pydate(today)
            seq = _year_hoshanos_sequence(hd_civil)
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
            if is_al_hanissim:
                parts.append("על הניסים")
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
