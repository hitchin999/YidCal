"""
custom_components/yidcal/special_prayer_sensor.py

Defines a single YidCal sensor that aggregates multiple prayer insertions with proper timing:
- 'מוריד הגשם' or 'מוריד הטל' after alos
- 'ותן ברכה' or 'ותן טל ומטר לברכה', switching only on transition days at havdalah
- 'יעלה ויבוא' on Rosh Chodesh after alos
- 'אתה יצרת' on Rosh Chodesh falling on Shabbat (between alos and candlelighting)
- 'על הניסים' on Chanukah or Purim
- 'ענינו' on any fast day (excluding Yom Kippur) during Mincha (half-day+30m to havdala)
Phrases are joined with hyphens.
"""
from __future__ import annotations
from datetime import timedelta
# Note: candle_offset is accepted for consistency but not used; use alos for dawn period
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
    """Aggregates special prayer insertions into a single sensor value."""
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
        self._havadalah = havdalah_offset

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
        now_dt = dt_now()
        today = now_dt.date()

        # Compute sun times using Home Assistant location
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(
            name="home",
            region="",
            timezone=self.hass.config.time_zone,
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
        )
        sun_times = sun(loc.observer, date=today, tzinfo=tz)
        alos = sun_times["sunrise"] - timedelta(minutes=72)
        sunset = sun_times["sunset"]
        # candle_time = sunset - timedelta(minutes=self._candle)  # removed; use sunset directly for sunset bound
        havdala = sunset + timedelta(minutes=self._havadalah)
        hal_mid = sun_times["sunrise"] + (sunset - sun_times["sunrise"]) / 2
        mincha_start = hal_mid + timedelta(minutes=30)

        phrases: list[str] = []

        # 1) Hebrew date via pyluach
        today_hd = PHebrewDate.from_pydate(today)
        day_num = today_hd.day
        month_name = today_hd.month_name(hebrew=True)

        # Rain blessing (after alos)
        if now_dt >= alos:
            rainy = (
                (month_name == "תשרי" and day_num >= 22)
                or month_name in ["חשון","כסלו","טבת","שבט","אדר","אדר א","אדר ב"]
                or (month_name == "ניסן" and day_num < 15)
            )
            phrases.append("מוריד הגשם" if rainy else "מוריד הטל")

            # Tal U’Matar (with transition at havdala)
            in_window = (
                (month_name == "כסלו" and day_num >= 5)
                or month_name in ["טבת","שבט","אדר","אדר א","אדר ב"]
                or (month_name == "ניסן" and day_num < 15)
            )
            start_switch = (month_name == "כסלו" and day_num == 5)
            end_switch   = (month_name == "ניסן" and day_num == 15)
            if start_switch or end_switch:
                # flip wording at havdala on transition days
                if now_dt >= havdala:
                    phrases.append("ותן טל ומטר לברכה" if in_window else "ותן ברכה")
                else:
                    phrases.append("ותן ברכה" if in_window else "ותן טל ומטר לברכה")
            else:
                phrases.append("ותן טל ומטר לברכה" if in_window else "ותן ברכה")

        # 2) Holiday-based insertions
        state = self.hass.states.get(HOLIDAY_SENSOR)
        attrs = state.attributes if state else {}

        # Rosh Chodesh
        if attrs.get("ראש חודש") and now_dt >= alos:
            phrases.append("יעלה ויבוא")
            if now_dt.weekday() == 5 and alos <= now_dt < sunset:
                phrases.append("אתה יצרת")

        # Chanukah or Purim
        if attrs.get("חנוכה") or attrs.get("פורים"):
            phrases.append("על הניסים")

        # Fast days during Mincha window (excluding YK)
        if mincha_start <= now_dt <= havdala:
            for key, val in attrs.items():
                if not val or "כיפור" in key:
                    continue
                if key.startswith("צום") or key.startswith("תענית") or key in ["תשעה באב","תשעה באב נדחה"]:
                    phrases.append("ענינו")
                    break

        return " - ".join(phrases)
