"""
custom_components/yidcal/special_prayer_sensor.py

Defines a single YidCal sensor that aggregates multiple prayer insertions with continuous windows:
- 'מוריד הגשם' or 'מוריד הטל' from dawn of 22 Tishrei through dawn of 15 Nisan
- 'ותן טל ומטר לברכה' or 'ותן ברכה' from havdala of 5 Kislev through havdala of 15 Nisan
- 'יעלה ויבוא' on Rosh Chodesh (after dawn)
- 'אתה יצרת' on Shabbat that is Rosh Chodesh (dawn→sunset)
- 'על הניסים' on Chanukah or Purim
- 'ענינו' on any fast day (excluding YK) from dawn until sunset+havdala
- 'נחם' on Tish'a B'Av from chatzos (halachic midday) until sunset+havdala
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

        # Hebrew date, adjusting for after sunset
        hd = PHebrewDate.from_pydate(today)
        if now >= sunset:
            hd += 1
        day, m = hd.day, hd.month_name(hebrew=True)

        insertions: list[str] = []

        # 1) Rain blessing continuous window
        rain_start = (
            (m == "תשרי" and (day > 22 or (day == 22 and now >= dawn)))
            or m in ["חשון","כסלו","טבת","שבט","אדר","אדר א","אדר ב"]
            or (m == "ניסן" and (day < 15 or (day == 15 and now < dawn)))
        )
        insertions.append("מוריד הגשם" if rain_start else "מוריד הטל")

        # 2) Tal U'Matar continuous window
        tal_start = (
            (m == "כסלו" and (day > 5 or (day == 5 and now >= havdala)))
            or m in ["טבת","שבט","אדר","אדר א","אדר ב"]
            or (m == "ניסן" and (day < 15 or (day == 15 and now <= havdala)))
        )
        insertions.append("ותן טל ומטר לברכה" if tal_start else "ותן ברכה")

        # 3) Holiday insertions
        state = self.hass.states.get(HOLIDAY_SENSOR)
        attrs = state.attributes if state else {}

        # Rosh Chodesh
        # יעלה ויבוא is active from previous day's havdalah until today's havdalah
        rosh_chodesh = attrs.get("ראש חודש")
        active = False
        
        # Main case: Holiday sensor indicates Rosh Chodesh and we haven't passed havdalah yet
        if rosh_chodesh and now <= havdala:
            active = True
        
        # Edge case: After sunset but before havdalah on Rosh Chodesh day
        # The holiday sensor might have already switched to tomorrow's state
        elif now > sunset and now <= havdala:
            # Check if today's Hebrew date indicates Rosh Chodesh (1st or 30th)
            if hd.day == 1 or (hd.day == 30 and hd.month_length() == 30):
                active = True
        
        # Edge case: Before dawn - need to check if we're still in yesterday's Rosh Chodesh window
        elif now < dawn:
            # Calculate yesterday's havdalah time
            yesterday = today - timedelta(days=1)
            yesterday_sun = sun(loc.observer, date=yesterday, tzinfo=tz)
            yesterday_havdala = yesterday_sun["sunset"] + timedelta(minutes=self._havdalah)
            
            # If we're after yesterday's havdalah and today is Rosh Chodesh day 1 or 30
            if now >= yesterday_havdala and (hd.day == 1 or (hd.day == 30 and hd.month_length() == 30)):
                active = True
        
        if active:
            insertions.append("יעלה ויבוא")
            # אתה יצרת only on Shabbat during daytime on actual Rosh Chodesh
            if now.weekday() == 5 and now < sunset and rosh_chodesh:
                insertions.append("אתה יצרת")

        # Chanukah or Purim
        if attrs.get("חנוכה") or attrs.get("פורים"):
            insertions.append("על הניסים")

        # 4) Fast / Tisha B'Av windows
        is_tisha = (hd.month == 5 and hd.day == 9)
        is_fast = any(
            v and not "כיפור" in k and (k.startswith("צום") or k.startswith("תענית"))
            for k, v in attrs.items()
        )
        # Tisha B'Av: נחם from chatzos → havdala
        if is_tisha:
            if now >= hal_mid and now <= havdala:
                insertions.append("נחם")
        # Other fasts: ענינו from dawn → havdala
        elif is_fast:
            if now >= dawn and now <= havdala:
                insertions.append("ענינו")

        return " - ".join(insertions)
