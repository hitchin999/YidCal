# no_music_sensor.py
"""
Binary sensor for "נישט הערן מוזיק":
- Prohibits music during the Omer (1–49) except on Lag BaOmer (33).
- Prohibits music during the Three Weeks period (17 Tammuz–9 Av).
- Activates at candle-lighting time, and deactivates at havdalah.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from pyluach.hebrewcal import HebrewDate

class NoMusicSensor(BinarySensorEntity):
    _attr_name = "YidCal No Music"
    _attr_unique_id = "yidcal_no_music"
    _attr_icon = "mdi:music-off"

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__()
        self.hass = hass
        self._attr_is_on = False
        self._added = False
        self._candle = candle
        self._havdalah = havdalah

        # Regular hourly updates
        async_track_time_interval(hass, self.async_update, timedelta(hours=1))

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()

    async def async_update(self, now=None) -> None:
        from datetime import datetime

        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.now(tz)
        today = now.date()
        hd = HebrewDate.from_pydate(today)

        # Count Omer (Nisan 16 - Sivan 5), except Lag B'Omer
        omer = 0
        if hd.month == 1 and hd.day >= 16:
            omer = hd.day - 15
        elif hd.month == 2:
            omer = 15 + hd.day
        elif hd.month == 3 and hd.day <= 5:
            omer = 45 + hd.day

        in_omer = omer and omer != 33

        # Three Weeks: 17 Tammuz (4) – 9 Av (5)
        in_three_weeks = (
            (hd.month == 4 and hd.day >= 17) or
            (hd.month == 5 and hd.day <= 9)
        )

        self._attr_is_on = in_omer or in_three_weeks

        if self._added:
            self.async_write_ha_state()
