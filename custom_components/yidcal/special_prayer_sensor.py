"""
custom_components/yidcal/special_prayer_sensor.py

Aggregates all dynamic *tefillah* insertions **plus** a ʻHoshanaʼ label for
each day of Sukkot.

Prayers handled (joined with " - "):
  • מוריד הגשם / מוריד הטל – dawn 22 Tishrei → dawn 15 Nisan
  • ותן טל ומטר לברכה / ותן ברכה – Motzaei 5 Kislev → Motzaei 15 Nisan
  • יעלה ויבוא – every Rosh‑Chodesh (not R"H) from dawn onward
  • אתה יצרת – when Shabbat is Rosh‑Chodesh (dawn → sunset)
  • על הניסים – Chanukah or Purim
  • עננו – any minor fast (dawn → Motzaei); also on 9 Av (dawn → Motzaei)
  • נחם – 9 Av only, from chatzot → Motzaei

Extra attribute:
  • "הושענות" – which *Hoshana* is recited on days 1‑6 of Sukkot, empty otherwise.
"""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.dt import now as dt_now
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .device import YidCalDevice

# -----------------------------------------------------------------------------
HOLIDAY_SENSOR = "sensor.yidcal_holiday"

# Mapping of the 6 Hoshanos (15‑20 Tishrei) by WEEKDAY of 15 Tishrei
# weekday(): Mon=0 … Sun=6 – only the four possible weekdays for the first day.
HOSHANOS_TABLE = {
    0: [  # Monday
        "למען אמתך",
        "אבן שתיה",
        "אערוך שועי",
        "אום אני חומה",
        "אל למושעות",
        "אום נצורה",
    ],
    1: [  # Tuesday
        "למען אמתך",
        "אבן שתיה",
        "אערוך שועי",
        "אל למושעות",
        "אום נצורה",
        "אדון המושיע",
    ],
    3: [  # Thursday
        "למען אמתך",
        "אבן שתיה",
        "אום נצורה",
        "אערוך שועי",
        "אל למושעות",
        "אדון המושיע",
    ],
    5: [  # Shabbat
        "אום נצורה",
        "למען אמתך",
        "אערוך שועי",
        "אבן שתיה",
        "אל למושעות",
        "אדון המושיע",
    ],
}


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
        self._attr_extra_state_attributes: dict[str, str] = {}

    # ------------------------------------------------------------------ HA hook
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _refresh(_):
            self.async_write_ha_state()

        unsub = async_track_state_change_event(
            self.hass,
            [HOLIDAY_SENSOR],
            _refresh,
        )
        self._register_listener(unsub)
        _refresh(None)

    # ---------------------------------------------------------------- property
    @property
    def native_value(self) -> str:
        try:
            # -------- HA location guard (can be None after reload)
            tzname = self.hass.config.time_zone
            lat = self.hass.config.latitude
            lon = self.hass.config.longitude
            if not tzname or lat is None or lon is None:
                return ""

            now = dt_now()
            today = now.date()

            # -------- Solar times
            tz = ZoneInfo(tzname)
            loc = LocationInfo("home", "", tzname, lat, lon)
            sun_times = sun(loc.observer, date=today, tzinfo=tz)
            dawn = sun_times["sunrise"] - timedelta(minutes=72)
            sunset = sun_times["sunset"]
            havdala = sunset + timedelta(minutes=self._havdalah)
            hal_mid = sun_times["sunrise"] + (sunset - sun_times["sunrise"]) / 2

            # -------- Hebrew date (roll after Havdalah)
            hd = PHebrewDate.from_pydate(today)
            if now >= havdala:
                hd = hd + 1
            day = hd.day
            m_he = hd.month_name(hebrew=True)

            # -------- Build insertion list
            insertions: list[str] = []

            # 1) Rain blessing window
            rain_start = (
                (m_he == "תשרי" and (day > 22 or (day == 22 and now >= dawn)))
                or m_he in [
                    "חשון",
                    "כסלו",
                    "טבת",
                    "שבט",
                    "אדר",
                    "אדר א",
                    "אדר ב",
                ]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
            )
            insertions.append("מוריד הגשם" if rain_start else "מוריד הטל")

            # 2) ותן טל ומטר window
            tal_start = (
                (m_he == "כסלו" and (day > 5 or (day == 5 and now >= havdala)))
                or m_he in ["טבת", "שבט", "אדר", "אדר א", "אדר ב"]
                or (m_he == "ניסן" and (day < 15 or (day == 15 and now <= havdala)))
            )
            insertions.append("ותן טל ומטר לברכה" if tal_start else "ותן ברכה")

            # 3) Holiday sensor attrs (Chanukah, Purim, fasts)
            state = self.hass.states.get(HOLIDAY_SENSOR)
            attrs = state.attributes if state else {}

            # Rosh‑Chodesh
            is_rc = ((day == 1) or (day == 30)) and (m_he != "תשרי")
            if is_rc:
                insertions.append("יעלה ויבוא")
                if now.weekday() == 5 and dawn <= now < sunset:
                    insertions.append("אתה יצרת")

            # Chanukah / Purim
            if attrs.get("חנוכה") or attrs.get("פורים"):
                insertions.append("על הניסים")

            # 4) Fast‑day insertions
            is_tisha = hd.month == 5 and hd.day == 9
            is_fast = any(
                bool(v)
                and ("כיפור" not in k)
                and (k.startswith("צום") or k.startswith("תענית"))
                for k, v in attrs.items()
            )

            if is_tisha:
                if dawn <= now <= havdala:
                    insertions.append("עננו")
                if hal_mid <= now <= havdala:
                    insertions.append("נחם")
            elif is_fast and dawn <= now <= havdala:
                insertions.append("עננו")

            # ------------------------------------------------------------------ Hoshana  (rolls at civil midnight)
            hoshana = ""
            hd_civil = PHebrewDate.from_pydate(today)          # ← never advanced after Havdalah
            if hd_civil.month == 7 and 15 <= hd_civil.day <= 20:
                first_day_greg = PHebrewDate(hd_civil.year, 7, 15).to_pydate()
                seq = HOSHANOS_TABLE.get(first_day_greg.weekday())
                if seq:
                    hoshana = seq[hd_civil.day - 15]

            # ---------------------------------------------------------------- expose attributes
            self._attr_extra_state_attributes = {"הושענות": hoshana}

            return " - ".join(insertions)

        except Exception as exc:
            self._attr_extra_state_attributes = {"error": repr(exc)}
            return ""
