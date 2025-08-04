# no_music_sensor.py
"""
Binary sensor for "נישט הערן מוזיק":
- Prohibits music during the Omer (days 1‑49) except on Lag BaOmer (33) and the
  final three days (47‑49).
- Prohibits music during the Three Weeks period (17 Tammuz – 10 Av).
- Three Weeks ends at halachic midday on 10 Av, except if 9 Av falls on Shabbat
  (fast deferred), then ends at sunset + havdalah on 10 Av.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
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
        
        # Cache for Three Weeks end time
        self._three_weeks_end: datetime | None = None
        
    async def async_added_to_hass(self) -> None:
        self._added = True
        await self.async_update()
        self._added = True
        await self.async_update()
        
        # Update every minute for precise timing
        self._register_interval(
            self.hass,
            self.async_update,
            timedelta(minutes=1),
        )
        
    async def async_update(self, now=None) -> None:
        tz = ZoneInfo(self.hass.config.time_zone)
        now = now or datetime.now(tz)
        today = now.date()
        hd = HebrewDate.from_pydate(today)
        
        # ── Omer count (Nisan 16 – Sivan 4) ─────────────────────────────
        omer = 0
        if hd.month == 1 and hd.day >= 16: # 16‑30 Nisan
            omer = hd.day - 15
        elif hd.month == 2: # Iyar
            omer = 15 + hd.day
        elif hd.month == 3 and hd.day <= 4: # 1‑4 Sivan
            omer = 45 + hd.day
            
        # True for days 1‑49 **except** 33, 47, 48, 49
        in_omer = 1 <= omer <= 49 and omer not in (33, 47, 48, 49)
        
        # ── Three Weeks (17 Tammuz – 10 Av with time awareness) ─────────
        in_three_weeks = False
        
        # Check if we're in the date range for Three Weeks
        if ((hd.month == 4 and hd.day >= 17) or  # 17‑29 Tammuz
            (hd.month == 5 and hd.day <= 10)):   # 1‑10 Av
            
            # Calculate the end time for Three Weeks
            year = hd.year
            
            # Get 9 Av and 10 Av dates
            av9_py = HebrewDate(year, 5, 9).to_pydate()
            av10_py = HebrewDate(year, 5, 10).to_pydate()
            
            # Check if 9 Av falls on Shabbat (deferred fast)
            is_nidche = (av9_py.weekday() == 5)
            
            # Location for sun calculations
            loc = LocationInfo(
                latitude=self.hass.config.latitude,
                longitude=self.hass.config.longitude,
                timezone=self.hass.config.time_zone,
            )
            
            # Calculate end time
            s_av10 = sun(loc.observer, date=av10_py, tzinfo=tz)
            if is_nidche:
                # Deferred: ends at sunset + havdalah on 10 Av
                three_weeks_end = s_av10["sunset"] + timedelta(minutes=self._havdalah)
            else:
                # Regular: ends at halachic midday on 10 Av
                three_weeks_end = s_av10["noon"]
            
            self._three_weeks_end = three_weeks_end
            
            # We're in Three Weeks if:
            # - We're before 10 Av, OR
            # - We're on 10 Av but before the end time
            if hd.month == 4 or (hd.month == 5 and hd.day < 10):
                in_three_weeks = True
            elif hd.month == 5 and hd.day == 10:
                in_three_weeks = now < three_weeks_end
        
        self._attr_is_on = in_omer or in_three_weeks
        
        if self._added:
            self.async_write_ha_state()
            
    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the Three Weeks end time if we're in that period."""
        attrs: dict[str, str] = {}
        if self._three_weeks_end:
            attrs["three_weeks_end"] = self._three_weeks_end.isoformat()
        return attrs
