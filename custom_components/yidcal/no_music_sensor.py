# no_music_sensor.py
"""
Binary sensor for "נישט הערן מוזיק":
- Prohibits music during the Omer (days 1‑49) except on Lag BaOmer (33) and the
  final three days (47‑49).
- Prohibits music during the Three Weeks period (17 Tammuz – 9 Av).
- Activates/deactivates on a simple date basis (no candle/havdalah offsets).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDevice

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from pyluach.hebrewcal import HebrewDate


class NoMusicSensor(YidCalDevice, BinarySensorEntity):
    _attr_name = "No Music"
    _attr_icon = "mdi:music-off"

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__()
        slug = "no_music"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False
        self._candle = candle
        self._havdalah = havdalah

        # Hourly updates keep the state fresh without spamming
        async_track_time_interval(hass, self.async_update, timedelta(hours=1))

    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()

    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.now(tz)
        today = now.date()
        hd = HebrewDate.from_pydate(today)

        # ── Omer count (Nisan 16 – Sivan 4) ─────────────────────────────
        omer = 0
        if hd.month == 1 and hd.day >= 16:          # 16‑30 Nisan
            omer = hd.day - 15
        elif hd.month == 2:                         # Iyar
            omer = 15 + hd.day
        elif hd.month == 3 and hd.day <= 4:         # 1‑4 Sivan
            omer = 45 + hd.day

        # True for days 1‑49 **except** 33, 47, 48, 49
        in_omer = 1 <= omer <= 49 and omer not in (33, 47, 48, 49)

        # ── Three Weeks (17 Tammuz – 9 Av) ──────────────────────────────
        in_three_weeks = (
            (hd.month == 4 and hd.day >= 17) or  # 17‑29 Tammuz
            (hd.month == 5 and hd.day <= 9)      # 1‑9 Av
        )

        self._attr_is_on = in_omer or in_three_weeks

        if self._added:
            self.async_write_ha_state()
            
