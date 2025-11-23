from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo
import logging

from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate
from hdate import HDateInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity

from .device import YidCalDevice
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Days to exclude when detecting festivals
FAST_DAYS = {
    "צום גדליה",
    "תענית אסתר",
    "צום עשרה בטבת",
    "צום שבעה עשר בתמוז",
    "תשעה באב",
    "תשעה באב נדחה",
    "ט׳ באב",
    "ט׳ באב נדחה",
    "י׳ בטבת",
    "י׳ בטבת נדחה",
    "י״ז בתמוז",
    "י״ז בתמוז נדחה",
}

# All possible states
POSSIBLE_STATES = [
    "Any Other Day",
    "Erev",
    "Motzi",
    "Shabbos",
    "Yom Tov",
    "Shabbos & Yom Tov",
    "Fast Day",
    "Chol Hamoed",
    "Shabbos & Chol Hamoed",
]

def _round_half_up(dt: datetime.datetime) -> datetime.datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime.datetime) -> datetime.datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

def _is_yomtov(pydate: datetime.date) -> bool:
    """Festival detection: try pyluach, exclude FAST_DAYS and Chol Hamoed."""
    try:
        name = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=False
        )
        if name:
            return True
        return False
    except Exception:
        return False


def _is_chol_hamoed(pydate: datetime.date) -> bool:
    """Check if the given date is Chol Hamoed."""
    try:
        name_with = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=True
        )
        name_no = PHebrewDate.from_pydate(pydate).festival(
            hebrew=True, include_working_days=False
        )
        return bool(name_with and not name_no and name_with in ["פסח", "סוכות"])
    except Exception:
        return False


def _is_fast_day(pydate: datetime.date) -> bool:
    """Check if the given date is a fast day."""
    try:
        name = PHebrewDate.from_pydate(pydate).fast_day(hebrew=True)
        return bool(name)
    except Exception:
        return False

def _attrs_for_state(state: str) -> dict:
    # Flags first…
    flags = {name: (name == state) for name in POSSIBLE_STATES}
    # …then "Possible states" last so it shows after the booleans
    return {
        **flags,
        "Possible states": POSSIBLE_STATES,
    }

class DayTypeSensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_name = "Day Type"
    _attr_unique_id = "yidcal_day_type"
    entity_id = "sensor.yidcal_day_type"
    _attr_icon = "mdi:calendar-check"
    _attr_device_class = "enum"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.hass = hass
        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._attr_native_value = "Any Other Day"
        self._attr_extra_state_attributes = _attrs_for_state(self._attr_native_value)
        cfg = hass.data[DOMAIN]["config"]
        self._diaspora = cfg.get("diaspora", True)


    @property
    def options(self) -> list[str]:
        """Return list of possible values for Home Assistant automation UI."""
        return POSSIBLE_STATES

    @property
    def native_value(self) -> str:
        """Return the current state value."""
        return self._attr_native_value

    def _set_state(self, state: str) -> None:
        """Atomic state+attributes setter to avoid dropping keys."""
        self._attr_native_value = state
        self._attr_extra_state_attributes = _attrs_for_state(state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in POSSIBLE_STATES:
            self._set_state(last.state)
        else:
            self._set_state("Any Other Day")
        await self.async_update()

        # Evaluate exactly once per minute at HH:MM:00 so it syncs with Zman Erev/Motzi
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    def _check_is_fast(self, hd: PHebrewDate) -> bool:
        # Tzom Gedaliah
        if hd.month == 7 and hd.day == 3 and hd.weekday() != 7:
            return True
        if hd.month == 7 and hd.day == 4 and hd.weekday() == 1 and PHebrewDate(hd.year, 7, 3).weekday() == 7:
            return True
        # Asara B'Tevet
        if hd.month == 10 and hd.day == 10 and hd.weekday() != 7:
            return True
        if hd.month == 10 and hd.day == 11 and hd.weekday() == 1 and PHebrewDate(hd.year, 10, 10).weekday() == 7:
            return True
        # Shiv'a Asar B'Tammuz
        if hd.month == 4 and hd.day == 17 and hd.weekday() != 7:
            return True
        if hd.month == 4 and hd.day == 18 and hd.weekday() == 1 and PHebrewDate(hd.year, 4, 17).weekday() == 7:
            return True
        # Ta'anit Esther (advanced to Thursday if on Shabbat)
        adar_month = 13 if ((hd.year * 7 + 1) % 19) < 7 else 12
        if hd.month == adar_month and hd.day == 13 and hd.weekday() != 7:
            return True
        if hd.month == adar_month and hd.day == 11 and hd.weekday() == 5 and PHebrewDate(hd.year, adar_month, 13).weekday() == 7:
            return True
        # Tisha B'Av
        if hd.month == 5 and hd.day == 9 and hd.weekday() != 7:
            return True
        if hd.month == 5 and hd.day == 10 and hd.weekday() == 1 and PHebrewDate(hd.year, 5, 9).weekday() == 7:
            return True
        return False

    def _is_minor_fast(self, hd: PHebrewDate) -> bool:
        return self._check_is_fast(hd) and hd.month != 5  # Excludes Tisha B'Av (Av/5)

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        cfg = self.hass.data[DOMAIN]["config"]
        tz = ZoneInfo(cfg["tzname"])
        now = now or datetime.datetime.now(tz)
        today = now.date()

        # Civil sun times for today, then ROUND them
        loc = LocationInfo(
            name="home",
            region="",
            timezone=cfg["tzname"],
            latitude=cfg["latitude"],
            longitude=cfg["longitude"],
        )
        solar = sun(loc.observer, date=today, tzinfo=tz)

        raw_dawn = solar["sunrise"] - timedelta(minutes=72)
        raw_sunset_today = solar["sunset"]
        raw_candle_cut = raw_sunset_today - timedelta(minutes=self._candle_offset)
        raw_havdalah_today = raw_sunset_today + timedelta(minutes=self._havdalah_offset)

        dawn = _round_half_up(raw_dawn)
        candle_cut = _round_half_up(raw_candle_cut)
        havdalah_today = _round_ceil(raw_havdalah_today)

        # EY/Chutz toggle via HDateInfo
        diaspora = cfg.get("diaspora", True)

        def is_yomtov(pydate: datetime.date) -> bool:
            """Reliable YT detector (excludes Shabbos and CH”M)."""
            try:
                return HDateInfo(pydate, diaspora=diaspora).is_yom_tov
            except Exception:
                return False

        # Effective pydate for current Hebrew day (rounded havdalah)
        effective_pydate = today if now < havdalah_today else today + timedelta(days=1)

        # --- Shabbos window (nearest current Shabbos), with rounding ---
        shabbos_eve = today
        while shabbos_eve.weekday() != 4:  # Friday
            shabbos_eve -= timedelta(days=1)
        shabbos_day = shabbos_eve + timedelta(days=1)

        shabbos_eve_solar = sun(loc.observer, date=shabbos_eve, tzinfo=tz)
        shabbos_day_solar = sun(loc.observer, date=shabbos_day, tzinfo=tz)

        raw_shabbos_start = (
            shabbos_eve_solar["sunset"] - timedelta(minutes=self._candle_offset)
        )
        raw_shabbos_end = (
            shabbos_day_solar["sunset"] + timedelta(minutes=self._havdalah_offset)
        )

        shabbos_start = _round_half_up(raw_shabbos_start)
        shabbos_end = _round_ceil(raw_shabbos_end)

        # --- Festival window detection (rounded) ---
        dates_to_check = [today - timedelta(days=1), today, today + timedelta(days=1)]
        fest_dates = sorted(d for d in dates_to_check if is_yomtov(d))
        if fest_dates:
            start_date = fest_dates[0]
            end_date = fest_dates[-1]

            # festival start: eve of first festival day
            eve = start_date - timedelta(days=1)
            eve_solar = sun(loc.observer, date=eve, tzinfo=tz)

            if eve.weekday() == 5:  # Shabbos → YT starts at Motzi Shabbos (havdalah)
                raw_fest_start = eve_solar["sunset"] + timedelta(
                    minutes=self._havdalah_offset
                )
                fest_start = _round_ceil(raw_fest_start)
            else:  # regular Erev-YT at candles
                raw_fest_start = eve_solar["sunset"] - timedelta(
                    minutes=self._candle_offset
                )
                fest_start = _round_half_up(raw_fest_start)

            # festival end: sunset+havdalah of last festival day (rounded)
            raw_fest_end = sun(
                loc.observer, date=end_date, tzinfo=tz
            )["sunset"] + timedelta(minutes=self._havdalah_offset)
            fest_end = _round_ceil(raw_fest_end)

            # if within festival window
            if fest_start <= now < fest_end:
                if shabbos_start <= now < shabbos_end:
                    if _is_chol_hamoed(shabbos_day):
                        state = "Shabbos & Chol Hamoed"
                    elif is_yomtov(shabbos_day):   # real overlap: Shabbos day is YT
                        state = "Shabbos & Yom Tov"
                    else:                          # overhang (e.g., RH day 2 → Shabbos)
                        state = "Shabbos"
                else:
                    state = "Yom Tov"
                self._set_state(state)
                return

            # holiday motzi: immediately after fest_end → 2 AM next day
            motzi_start = fest_end
            motzi_end = datetime.datetime.combine(
                end_date + timedelta(days=1), time(2, 0), tz
            )
            if motzi_start <= now < motzi_end:
                if _is_chol_hamoed(effective_pydate):
                    # Keep Shabbos visible during motzi-YT into Shabbos
                    state = "Chol Hamoed"
                    if shabbos_start <= now < shabbos_end:
                        state = "Shabbos & Chol Hamoed"
                else:
                    # 🔑 Shabbos beats Motzi after YT ends
                    if shabbos_start <= now < shabbos_end:
                        state = "Shabbos"
                    else:
                        state = "Motzi"
                self._set_state(state)
                return

        # --- Standard Motzi: only if yesterday was Yom Tov (rounded start) ---
        prev_date = today - timedelta(days=1)
        raw_motzi = is_yomtov(prev_date)
        prev_solar = sun(loc.observer, date=prev_date, tzinfo=tz)
        raw_motzi_start = prev_solar["sunset"] + timedelta(
            minutes=self._havdalah_offset
        )
        motzi_start = _round_ceil(raw_motzi_start)
        motzi_end = datetime.datetime.combine(today, time(2, 0), tz)
        if raw_motzi and motzi_start <= now < motzi_end:
            if _is_chol_hamoed(effective_pydate):
                state = "Chol Hamoed"
                if shabbos_start <= now < shabbos_end:
                    state = "Shabbos & Chol Hamoed"
            else:
                # 🔑 Shabbos beats Motzi
                if shabbos_start <= now < shabbos_end:
                    state = "Shabbos"
                else:
                    state = "Motzi"
            self._set_state(state)
            return

        # --- Erev: dawn → candlelighting for Friday or YT-eve ---
        is_yom_tom = is_yomtov(today + timedelta(days=1))
        is_fast_tomorrow = _is_fast_day(today + timedelta(days=1))
        is_shabbos_today = (today.weekday() == 5)

        if (
            dawn <= now < candle_cut
            and not is_fast_tomorrow
            and not is_shabbos_today
            and ((today.weekday() == 4 and not is_yomtov(today)) or is_yom_tom)
        ):
            self._set_state("Erev")
            return

        # --- Shabbos on Friday evening or Saturday day ---
        if shabbos_start <= now < shabbos_end:
            if _is_chol_hamoed(shabbos_day):
                state = "Shabbos & Chol Hamoed"
            elif is_yomtov(shabbos_day):       # real overlap
                state = "Shabbos & Yom Tov"
            else:
                state = "Shabbos"
            self._set_state(state)
            return

        # --- Chol Hamoed ---
        if _is_chol_hamoed(effective_pydate):
            self._set_state("Chol Hamoed")
            return

        # --- Fast days ---
        # Use halachic-day anchor (same rule used above)
        effective_pydate = today if now < havdalah_today else today + timedelta(days=1)
        effective_hd = PHebrewDate.from_pydate(effective_pydate)
        is_fast = self._check_is_fast(effective_hd)

        if is_fast:
            # Fast ends at sunset + havdalah of the FAST DAY (rounded)
            end_solar = sun(loc.observer, date=effective_pydate, tzinfo=tz)
            raw_end_time = end_solar["sunset"] + timedelta(
                minutes=self._havdalah_offset
            )
            end_time = _round_ceil(raw_end_time)

            # Start threshold:
            # - Minor fasts: SHOW "Fast Day" only from 02:00 local on the fast day.
            # - Tisha B'Av (and any non-minor fast): begins at shkiah (sunset) of the previous day.
            if self._is_minor_fast(effective_hd):
                start_time = datetime.datetime.combine(
                    effective_pydate, time(2, 0), tz
                )
            else:
                prev_solar = sun(
                    loc.observer, date=effective_pydate - timedelta(days=1), tzinfo=tz
                )
                raw_start_time = prev_solar["sunset"]
                start_time = _round_half_up(raw_start_time)

            # Decide state within [start_time, end_time)
            if start_time <= now < end_time:
                # Never mask Shabbos visuals while inside Shabbos window
                if shabbos_start <= now < shabbos_end:
                    if _is_chol_hamoed(shabbos_day):
                        state = "Shabbos & Chol Hamoed"
                    elif is_yomtov(shabbos_day):
                        state = "Shabbos & Yom Tov"
                    else:
                        state = "Shabbos"
                    self._set_state(state)
                else:
                    self._set_state("Fast Day")
                return

        # --- Motzi on Saturday evening (plain Shabbos) ---
        motzi_end_shabbos = datetime.datetime.combine(
            shabbos_day + timedelta(days=1), time(2, 0), tz
        )
        if shabbos_end <= now < motzi_end_shabbos:
            self._set_state("Motzi")
            return

        # --- Default: Any Other Day ---
        self._set_state("Any Other Day")
