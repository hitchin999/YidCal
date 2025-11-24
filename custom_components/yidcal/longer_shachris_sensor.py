from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class LongerShachrisSensor(YidCalSpecialDevice, RestoreEntity, BinarySensorEntity):
    """
    ON 04:00–14:00 local on:
      • Rosh Chodesh (exclude 1 Tishrei)
      • Chanukah
      • Tisha B'Av (incl. nidcheh)
      • Chol Hamoed (Pesach/Sukkos)
      • Purim (14 Adar; Adar II in leap year)

    Always OFF on Shabbos or Yom Tov even if those occur.

    Attributes (same as No Melucha – Yom Tov):
      Now, Window_Start, Window_End, Activation_Logic

    If currently OFF, attributes show the next upcoming qualifying window.
    """

    _attr_name = "Longer Shachris"
    _attr_icon = "mdi:alarm"
    _attr_unique_id = "yidcal_longer_shachris"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self.entity_id = "binary_sensor.yidcal_longer_shachris"

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)

        # store both for consistency; only havdalah is used for halachic-day roll
        self._candle = int(candle_offset)
        self._havdalah = int(havdalah_offset)

        self._geo = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ---------- helpers ----------

    def _festival_date(self, now: datetime) -> datetime.date:
        """Halachic date that rolls at havdalah (sunset + havdalah_offset)."""
        cal_today = ZmanimCalendar(geo_location=self._geo, date=now.date())
        sunset = cal_today.sunset().astimezone(self._tz)
        havdalah_cut = sunset + timedelta(minutes=self._havdalah)
        return (now.date() + timedelta(days=1)) if now >= havdalah_cut else now.date()

    @staticmethod
    def _is_leap(hebrew_year: int) -> bool:
        return ((hebrew_year * 7 + 1) % 19) < 7

    def _is_shabbos(self, d: datetime.date) -> bool:
        return d.weekday() == 5

    def _is_yomtov(self, d: datetime.date) -> bool:
        return HDateInfo(d, diaspora=self._diaspora).is_yom_tov

    def _qualifies(self, d: datetime.date) -> bool:
        """Whether the HALACHIC day d qualifies (before Shabbos/YT exclusions)."""
        hd = PHebrewDate.from_pydate(d)
        m, day, y = hd.month, hd.day, hd.year
        is_leap = self._is_leap(y)

        # Rosh Chodesh (excluding 1 Tishrei)
        is_rc = (day in (1, 30)) and not (m == 7 and day == 1)

        # Chanukah: 25–30 Kislev + 1–2 Tevet
        is_chanukah = (m == 9 and 25 <= day <= 30) or (m == 10 and day in (1, 2))

        # Chol Hamoed (diaspora/EY differ, and we include הושענא רבה)
        if self._diaspora:
            # Pesach: 17–20  | Sukkos: 17–21 (includes הושענא רבה)
            is_chm_pesach = (m == 1 and 17 <= day <= 20)
            is_chm_sukkos = (m == 7 and 17 <= day <= 21)
        else:
            # Pesach: 16–20  | Sukkos: 16–21 (includes הושענא רבה)
            is_chm_pesach = (m == 1 and 16 <= day <= 20)
            is_chm_sukkos = (m == 7 and 16 <= day <= 21)
        is_chm = is_chm_pesach or is_chm_sukkos

        # Purim (Adar II in leap years)
        is_purim = (m == (13 if is_leap else 12) and day == 14)

        # Tisha B'Av incl. nidcheh (10 Av when 9 Av is Shabbos)
        av9_wd = PHebrewDate(y, 5, 9).to_pydate().weekday()
        is_tbav = (m == 5 and day == 9) or (m == 5 and day == 10 and av9_wd == 5)

        return any((is_rc, is_chanukah, is_chm, is_purim, is_tbav))

    def _window_for(self, halachic_date: datetime.date) -> tuple[datetime, datetime]:
        """Civil window 04:00–14:00 for the morning of the halachic day."""
        start = datetime.combine(halachic_date, time(4, 0, 0, tzinfo=self._tz))
        end = datetime.combine(halachic_date, time(14, 0, 0, tzinfo=self._tz))
        return _round_half_up(start), _round_ceil(end)

    def _next_qualifying_hdate(self, ref: datetime.date) -> datetime.date | None:
        """Find the next halachic day ≥ ref that qualifies and isn’t Shabbos/YT."""
        for i in range(0, 370):
            d = ref + timedelta(days=i)
            if not self._qualifies(d):
                continue
            if self._is_shabbos(d) or self._is_yomtov(d):
                continue
            return d
        return None

    # ---------- main ----------

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        hdate = self._festival_date(now)
        included = self._qualifies(hdate) and not (self._is_shabbos(hdate) or self._is_yomtov(hdate))

        if included:
            window_start, window_end = self._window_for(hdate)
            self._attr_is_on = window_start <= now < window_end

            # If we're outside today's window, show the next upcoming qualifying window
            if not self._attr_is_on:
                nxt = self._next_qualifying_hdate(hdate + timedelta(days=1))
                if nxt:
                    window_start, window_end = self._window_for(nxt)
        else:
            self._attr_is_on = False
            nxt = self._next_qualifying_hdate(hdate)
            if nxt:
                window_start, window_end = self._window_for(nxt)
            else:
                window_start = window_end = None

        if window_start and window_end:
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": window_start.isoformat(),
                "Window_End": window_end.isoformat(),
                "Activation_Logic": (
                    "ON 4:00–14:00 local on: Rosh Chodesh (except 1 Tishrei), Chanukah, "
                    "Tisha B'Av (incl. nidcheh), Chol Hamoed (Pesach/Sukkos; includes הושענא רבה; "
                    "ranges honor your Diaspora/Israel setting), and Purim. "
                    "Always OFF on Shabbos or Yom Tov."
                ),
            }
        else:
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": "",
                "Window_End": "",
                "Activation_Logic": (
                    "ON 4:00–14:00 local on: Rosh Chodesh (except 1 Tishrei), Chanukah, "
                    "Tisha B'Av (incl. nidcheh), Chol Hamoed (Pesach/Sukkos; includes הושענא רבה; "
                    "ranges honor your Diaspora/Israel setting), and Purim. "
                    "Always OFF on Shabbos or Yom Tov."
                ),
            }
