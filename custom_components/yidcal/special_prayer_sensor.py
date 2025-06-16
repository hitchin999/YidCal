"""
custom_components/yidcal/special_prayer_sensor.py

Defines a single YidCal sensor that aggregates multiple prayer insertions with continuous windows:
- ‘מוריד הגשם’ or ‘מוריד הטל’ from dawn of 22 Tishrei through dawn of 15 Nisan
- ‘ותן טל ומטר לברכה’ or ‘ותן ברכה’ from havdala of 5 Kislev through havdala of 15 Nisan
- ‘יעלה ויבוא’ on Rosh Chodesh (after dawn)
- ‘אתה יצרת’ on Shabbat that is Rosh Chodesh (dawn→sunset)
- ‘על הניסים’ on Chanukah or Purim
- ‘ענינו’ on any fast day (excluding YK) during Mincha window (half‐day+30m→havdala)
Phrases joined with hyphens.
"""
from __future__ import annotations
from datetime import timedelta
from zoneinfo import ZoneInfo
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util.dt import now as dt_now
from astral.sun import sun
from astral import LocationInfo
from pyluach.dates import HebrewDate as PHebrewDate
from .device import YidCalDevice

HOLIDAY_SENSOR = "sensor.yidcal_holiday"

class SpecialPrayerSensor(YidCalDevice, SensorEntity):
    _attr_name = "Special Prayer"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "special_prayer"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass
        self._candle = candle_offset
        self._havdalah = havdalah_offset

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _refresh(_) -> None:
            self.async_write_ha_state()

        unsub = async_track_state_change_event(
            self.hass,
            [HOLIDAY_SENSOR],
            _refresh,
        )
        self._register_listener(unsub)
        _refresh(None)

    @property
    def native_value(self) -> str:
        now = dt_now()
        today = now.date()

        # compute sun times & offsets
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home", region="", timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude, longitude=self.hass.config.longitude
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        dawn = sun_times["sunrise"] - timedelta(minutes=72)
        sunset = sun_times["sunset"]
        havdala = sunset + timedelta(minutes=self._havdalah)
        hal_mid = dawn + (sunset - dawn) / 2
        mincha = hal_mid + timedelta(minutes=30)

        # Hebrew date
        hd = PHebrewDate.from_pydate(today)
        day, m = hd.day, hd.month_name(hebrew=True)

        insertions: list[str] = []

        # 1) Rain blessing continuous window
        rain_start = (m == "תשרי" and (day > 22 or (day == 22 and now >= dawn))) or \
                     (m in ["חשון","כסלו","טבת","שבט","אדר","אדר א","אדר ב"]) or \
                     (m == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
        insertions.append("מוריד הגשם" if rain_start and now >= dawn else "מוריד הטל")

        # 2) Tal U’Matar continuous window
        tal_start = (m == "כסלו" and (day > 5 or (day == 5 and now >= havdala))) or \
                    (m in ["טבת","שבט","אדר","אדר א","אדר ב"]) or \
                    (m == "ניסן" and day < 15)
        insertions.append("ותן טל ומטר לברכה" if tal_start and now >= havdala else "ותן ברכה")

        # 3) Holiday insertions
        attrs = (self.hass.states.get(HOLIDAY_SENSOR) or {}).attributes

        # Rosh Chodesh
        if attrs.get("ראש חודש") and now >= dawn:
            insertions.append("יעלה ויבוא")
            if now.weekday() == 5 and now < sunset:
                insertions.append("אתה יצרת")

        # Chanukah or Purim
        if attrs.get("חנוכה") or attrs.get("פורים"):
            insertions.append("על הניסים")

        # Fast days during Mincha
        if mincha <= now <= havdala:
            for key, val in attrs.items():
                if val and not "כיפור" in key and (key.startswith("צום") or key.startswith("תענית") or key in ["תשעה באב","תשעה באב נדחה"]):
                    insertions.append("ענינו")
                    break

        return " - ".join(insertions)
