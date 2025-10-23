# nine_days_sensor.py
"""
Binary sensor for "תשעת הימים" (The Nine Days):
- Activates at sunset + havdalah offset on the eve of 1 Av (tzeis that begins 1 Av)
- Deactivates by halachic midday (Chatzos Hayom, MGA 72/72) on 10 Av, except:
    • if 9 Av falls on Shabbat (fast deferred to 10 Av),
      deactivates at sunset + havdalah (tzeis) on 10 Av

Attributes:
  Now:                 ISO current local time
  Next_Window_Start:   ISO when the current/upcoming Nine Days window starts
  Next_Window_End:     ISO when the current/upcoming Nine Days window ends
  Nidche_Year:         True if this year's 9 Av is Shabbos
  Activation_Logic:    concise ON/OFF rules
"""

from __future__ import annotations
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant

from .device import YidCalDevice
from .zman_sensors import get_geo
from zmanim.zmanim_calendar import ZmanimCalendar


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0) if dt.second or dt.microsecond else dt


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

        self._candle = candle
        self._havdalah = havdalah

        self._geo = None  # for MGA Chatzos calc
        self._tz = ZoneInfo(hass.config.time_zone)

        # caches (Bishul-style)
        self._now_local: datetime | None = None
        self._next_window_start: datetime | None = None
        self._next_window_end: datetime | None = None
        self._nidche_year: bool = False

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)  # match Chatzos sensor inputs
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ---- helpers ----
    def _loc(self) -> LocationInfo:
        return LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self.hass.config.time_zone,
        )

    def _tzeis_on(self, greg_date) -> datetime:
        s = sun(self._loc().observer, date=greg_date, tzinfo=self._tz)
        return s["sunset"] + timedelta(minutes=self._havdalah)

    def _compute_chatzos_for_date(self, base_date) -> datetime:
        """Match ChatzosHayomSensor exactly: MGA day (sr-72/ss+72) with round-half-up."""
        assert self._geo is not None
        cal = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunrise = cal.sunrise().astimezone(self._tz)
        sunset  = cal.sunset().astimezone(self._tz)
        dawn      = sunrise - timedelta(minutes=72)
        nightfall = sunset  + timedelta(minutes=72)
        target = dawn + (nightfall - dawn) / 12 * 6
        return _round_half_up(target)

    def _activation_logic_text(self) -> str:
        return (
            "ON from tzeis that begins 1 Av, until 10 Av at Chatzos Hayom; "
            "if 9 Av is Shabbos (nidche), remains ON until"
            "tzeis on 10 Av. OFF outside this window."
        )

    # ---- update ----
    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now = (now or datetime.now(self._tz)).astimezone(self._tz)
        self._now_local = now

        # Today's Hebrew year
        year = HebrewDate.from_pydate(now.date()).year

        # Current year's window
        av1   = HebrewDate(year, 5, 1).to_pydate()
        av9   = HebrewDate(year, 5, 9).to_pydate()
        av10  = HebrewDate(year, 5, 10).to_pydate()

        on_time  = _round_half_up(self._tzeis_on(av1 - timedelta(days=1)))
        is_nidche = (av9.weekday() == 5)
        self._nidche_year = is_nidche

        if is_nidche:
            off_time = _round_ceil(self._tzeis_on(av10))
        else:
            off_time = _round_ceil(self._compute_chatzos_for_date(av10))

        # State
        in_window = (on_time <= now < off_time)
        self._attr_is_on = in_window
        
        # Pick which window Next_* should represent and compute Nidche accordingly
        if now < on_time or in_window:
            # this year's window (current/upcoming)
            next_year = year
            next_on, next_off = on_time, off_time
            nidche_for_next = is_nidche  # current year's 9 Av on Shabbos?
        else:
            # next year's window
            next_year = year + 1
            av1n  = HebrewDate(next_year, 5, 1).to_pydate()
            av9n  = HebrewDate(next_year, 5, 9).to_pydate()
            av10n = HebrewDate(next_year, 5, 10).to_pydate()
        
            nidche_for_next = (av9n.weekday() == 5)
            next_on = _round_half_up(self._tzeis_on(av1n - timedelta(days=1)))
            if nidche_for_next:
                next_off = _round_ceil(self._tzeis_on(av10n))
            else:
                next_off = _round_ceil(self._compute_chatzos_for_date(av10n))
        
        self._next_window_start = next_on
        self._next_window_end   = next_off
        self._nidche_year       = nidche_for_next  # <-- now guaranteed to match Next_Window_*

        if self._added:
            self.async_write_ha_state()

    # ---- attributes ----
    @property
    def extra_state_attributes(self) -> dict[str, str | bool]:
        attrs: dict[str, str | bool] = {}
        if self._now_local:
            attrs["Now"] = self._now_local.isoformat()
        if self._next_window_start:
            attrs["Next_Window_Start"] = self._next_window_start.isoformat()
        if self._next_window_end:
            attrs["Next_Window_End"] = self._next_window_end.isoformat()
        attrs["Nidche_Year"] = self._nidche_year
        attrs["Activation_Logic"] = self._activation_logic_text()
        return attrs
