# nine_days_sensor.py
"""
Binary sensor for "תשעת הימים" (The Nine Days):
- Activates at sunset + havdalah offset on the eve of 1 Av
- Deactivates by halachic midday on 10 Av, except:
    • if 9 Av falls on Shabbat (fast deferred to 10 Av),
      deactivates at sunset + havdalah on 10 Av

Attributes:
  window_start: ISO timestamp when the window opens
  window_end:   ISO timestamp when the window closes
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from .device import YidCalDevice


class NineDaysSensor(YidCalDevice, BinarySensorEntity):
    _attr_name = "Nine Days"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__()
        slug = "nine_days"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        # match your other sensors
        self._candle = candle
        self._havdalah = havdalah

        # placeholders for attributes
        self._window_start: datetime | None = None
        self._window_end:   datetime | None = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()
        await self.async_update()  # twice like your no_music

        # minute-by-minute updates so we hit on/off exactly
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )

    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.now(tz)

        # Today's Hebrew date & year
        hd_today = HebrewDate.from_pydate(now.date())
        year = hd_today.year

        # 1 Av & 10 Av in Gregorian
        rc1_py  = HebrewDate(year, 5, 1).to_pydate()
        rc10_py = HebrewDate(year, 5, 10).to_pydate()

        # Location for sun() calculations
        loc = LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.hass.config.time_zone,
        )

        # ON at sunset + havdalah on eve of 1 Av
        eve_rc1 = rc1_py - timedelta(days=1)
        s_eve   = sun(loc.observer, date=eve_rc1, tzinfo=tz)
        on_time = s_eve["sunset"] + timedelta(minutes=self._havdalah)

        # Is 9 Av deferred because it fell on Shabbat?
        date9    = rc1_py + timedelta(days=8)
        is_nidche = (date9.weekday() == 5)

        # OFF: either halachic noon on 10 Av, or sunset+havdalah on 10 Av if deferred
        s_rc10 = sun(loc.observer, date=rc10_py, tzinfo=tz)
        if is_nidche:
            off_time = s_rc10["sunset"] + timedelta(minutes=self._havdalah)
        else:
            off_time = s_rc10["noon"]

        # update our state and attribute cache
        self._attr_is_on = (now >= on_time) and (now < off_time)
        self._window_start = on_time
        self._window_end   = off_time

        if self._added:
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the window start/end as ISO-format timestamps."""
        attrs: dict[str, str] = {}
        if self._window_start and self._window_end:
            attrs["Window_Start"] = self._window_start.isoformat()
            attrs["Window_End"]   = self._window_end.isoformat()
        return attrs
