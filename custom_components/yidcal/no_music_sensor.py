# no_music_sensor.py
from __future__ import annotations
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from pyluach.hebrewcal import HebrewDate

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import get_geo
from zmanim.zmanim_calendar import ZmanimCalendar


class NoMusicSensor(YidCalSpecialDevice, BinarySensorEntity):
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

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo = None  # for ZmanimCalendar (used for Chatzos like ChatzosHayomSensor)

        self._in_sefirah: bool = False
        self._in_three_weeks: bool = False
        self._this_window_ends: datetime | None = None
        self._next_window_start: datetime | None = None
        self._next_window_end: datetime | None = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ── Helpers ─────────────────────────────────────────────────────────
    def _tz(self) -> ZoneInfo:
        return self._tz

    def _loc(self) -> LocationInfo:
        # astral helper for tzeis (sunset + havdalah offset)
        return LocationInfo(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            timezone=self._tz.key,
        )

    def _dt_at_start_of_day(self, d) -> datetime:
        return datetime.combine(d, time(0, 0, 0), tzinfo=self._tz)

    def _omer_day(self, hd: HebrewDate) -> int:
        if hd.month == 1 and hd.day >= 16:  # Nisan 16-30
            return hd.day - 15
        if hd.month == 2:                   # Iyar
            return 15 + hd.day
        if hd.month == 3 and hd.day <= 4:   # Sivan 1-4
            return 45 + hd.day
        return 0

    def _is_omer_prohibited(self, day: int) -> bool:
        # Prohibited: 1–32 and 34–46; Allowed: 33 and 47–49
        return (1 <= day <= 32) or (34 <= day <= 46)

    def _tzeis_on(self, greg_date) -> datetime:
        tz = self._tz
        s = sun(self._loc().observer, date=greg_date, tzinfo=tz)
        return s["sunset"] + timedelta(minutes=self._havdalah)

    def _compute_chatzos_for_date(self, base_date) -> datetime:
        """Match ChatzosHayomSensor exactly: MGA day (dawn=sr-72, nightfall=ss+72) with rounding."""
        assert self._geo is not None
        cal = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunrise = cal.sunrise().astimezone(self._tz)
        sunset = cal.sunset().astimezone(self._tz)

        dawn = sunrise - timedelta(minutes=72)
        nightfall = sunset + timedelta(minutes=72)

        target = dawn + (nightfall - dawn) / 12 * 6  # 6 sha'os zmanios
        if target.second >= 30:  # round-half-up to the minute
            target += timedelta(minutes=1)
        return target.replace(second=0, microsecond=0)

    def _build_sefirah_windows(self, hyear: int) -> list[tuple[datetime, datetime, str]]:
        """
        Build Sefirah prohibition windows (two segments typically).
        start = tzeis (sunset+havdalah) of the *evening that begins* the first prohibited Hebrew day
              = tzeis(greg of first_prohibited_day - 1)
        end   = tzeis of the *last prohibited Hebrew day*
        This naturally carves out Lag BaOmer (day 33) as fully allowed: tzeis(32) -> tzeis(33).
        """
        windows: list[tuple[datetime, datetime, str]] = []

        start_hd = HebrewDate(hyear, 1, 16)
        end_hd = HebrewDate(hyear, 3, 4)
        cur = start_hd
        in_block = False
        block_start: HebrewDate | None = None

        while cur <= end_hd:
            od = self._omer_day(cur)
            prohibited = self._is_omer_prohibited(od)
            if prohibited and not in_block:
                in_block = True
                block_start = HebrewDate(cur.year, cur.month, cur.day)
            if not prohibited and in_block:
                # Block ends previous Hebrew day -> end at tzeis of that last prohibited day
                last = cur - 1
                start_dt = self._tzeis_on(block_start.to_pydate() - timedelta(days=1))
                end_dt = self._tzeis_on(last.to_pydate())
                windows.append((start_dt, end_dt, "sefirah"))
                in_block = False
                block_start = None
            cur = cur + 1

        if in_block and block_start:
            # Runs through Sivan 4 -> end at tzeis of Sivan 4
            last = end_hd
            start_dt = self._tzeis_on(block_start.to_pydate() - timedelta(days=1))
            end_dt = self._tzeis_on(last.to_pydate())
            windows.append((start_dt, end_dt, "sefirah"))

        return windows

    def _three_weeks_window(self, hyear: int) -> tuple[datetime, datetime, str]:
        """
        Start:
          • Normal year: tzeis that BEGINS 17 Tammuz (tzeis on the civil day before 17 Tammuz)
          • If 17 Tammuz is Shabbos (nidche): start at tzeis after Shabbos that begins 18 Tammuz
        End:
          • Normal year: 10 Av at Chatzos Hayom (MGA 72/72, rounded like Chatzos sensor)
          • Nidche year (9 Av is Shabbos; fast on 10 Av): tzeis on 10 Av
        """
        # start
        tammuz17 = HebrewDate(hyear, 4, 17).to_pydate()
        if tammuz17.weekday() == 5:
            start_dt = self._tzeis_on(HebrewDate(hyear, 4, 18).to_pydate() - timedelta(days=1))
        else:
            start_dt = self._tzeis_on(tammuz17 - timedelta(days=1))
    
        # end (depends on nidche)
        av9  = HebrewDate(hyear, 5, 9).to_pydate()
        av10 = HebrewDate(hyear, 5, 10).to_pydate()
        if av9.weekday() == 5:
            end_dt = self._tzeis_on(av10)
        else:
            end_dt = self._compute_chatzos_for_date(av10)
    
        return (start_dt, end_dt, "three_weeks")

    def _build_windows(self, now: datetime) -> list[tuple[datetime, datetime, str]]:
        """Current Hebrew year + next, sorted by start."""
        hy = HebrewDate.from_pydate(now.date()).year
        w = []
        w.extend(self._build_sefirah_windows(hy))
        w.append(self._three_weeks_window(hy))
        w.extend(self._build_sefirah_windows(hy + 1))
        w.append(self._three_weeks_window(hy + 1))
        w.sort(key=lambda x: x[0])
        return w

    # ── Update ──────────────────────────────────────────────────────────
    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return  # wait until geo is loaded so Chatzos calc matches ChatzosHayomSensor

        tz = self._tz
        now = now or datetime.now(tz)

        # Build windows and locate where "now" sits
        windows = self._build_windows(now)

        in_window = False
        in_sefirah = False
        in_three_weeks = False
        current_end = None
        next_start = None
        next_end = None

        for (ws, we, kind) in windows:
            if ws <= now < we:
                in_window = True
                current_end = we
                in_sefirah = (kind == "sefirah")
                in_three_weeks = (kind == "three_weeks")
                break

        pivot = current_end or now
        for (ws, we, kind) in windows:
            if ws > pivot:
                next_start, next_end = ws, we
                break

        self._attr_is_on = in_window
        self._in_sefirah = in_sefirah
        self._in_three_weeks = in_three_weeks
        self._this_window_ends = current_end
        self._next_window_start = next_start
        self._next_window_end = next_end

        if self._added:
            self.async_write_ha_state()

    # ── Attributes ─────────────────────────────────────────────────────
    def _activation_logic_text(self) -> str:
        return (
            "ON during two periods: "
            "• Sefirah — Omer days 1–32 and 34–46, from tzeis that begins the first prohibited day "
            "through tzeis at the end of the last prohibited day (Lag BaOmer 33 and days 47–49 are allowed); "
            "• Three Weeks — from tzeis that begins 17 Tammuz (if 17 Tammuz is Shabbos, from tzeis after Shabbos that begins 18 Tammuz) "
            "until 10 Av at Chatzos Hayom; if 9 Av is Shabbos (nidche), ends at tzeis on 10 Av. "
            "OFF outside these windows."
        )

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        attrs: dict[str, object] = {}
        attrs["Three Weeks"] = self._in_three_weeks
        attrs["Sefirah"] = self._in_sefirah
        attrs["Next Window Start"] = (
            self._next_window_start.isoformat() if self._next_window_start else "N/A"
        )
        attrs["Next Window End"] = (
            self._next_window_end.isoformat() if self._next_window_end else "N/A"
        )
        attrs["This Window End"] = (
            self._this_window_ends.isoformat() if self._this_window_ends else "N/A"
        )
        attrs["Activation_Logic"] = self._activation_logic_text()
        return attrs
