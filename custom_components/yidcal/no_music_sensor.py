# no_music_sensor.py
from __future__ import annotations
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.core import HomeAssistant
from pyluach.hebrewcal import HebrewDate

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import (
    chatzos_hayom_for_date,
    round_ceil as _round_ceil,
    round_half_up as _round_half_up,
    sunset_for_date,
)
from .zman_sensors import get_geo

class NoMusicSensor(YidCalSpecialDevice, RestoreEntity, BinarySensorEntity):
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
        self._diaspora = cfg.get("diaspora", True)
        self._geo = None  # for shared zmanim helpers (used for Chatzos like ChatzosHayomSensor)

        self._in_sefirah: bool = False
        self._in_three_weeks: bool = False
        self._this_window_starts: datetime | None = None
        self._this_window_ends: datetime | None = None
        self._next_window_start: datetime | None = None
        self._next_window_end: datetime | None = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        # restore last state if available
        last = await self.async_get_last_state()
        if last:
            self._attr_is_on = (last.state == "on")
        await self.async_update()
        self._register_interval(self.hass, self.async_update, timedelta(minutes=1))

    # ── Helpers ─────────────────────────────────────────────────────────

    def _dt_at_start_of_day(self, d) -> datetime:
        return datetime.combine(d, time(0, 0, 0), tzinfo=self._tz)

    def _omer_day(self, hd: HebrewDate) -> int:
        """Canonical omer count (halacha_events.omer_day_for).

        BUG #16 FIX: the old local arithmetic used ``45 + day`` for Sivan
        (should be ``44 + day``), so true omer 46 (2 Sivan) was treated as
        47 → the sefirah no-music window ended one day before שלושת ימי
        הגבלה. sfirah_helper (the public counter) always had the correct
        ``44 + day``; no_music now matches it.
        """
        return he.omer_day_for(hd.to_pydate()) or 0

    def _is_omer_prohibited(self, day: int) -> bool:
        # Prohibited: 1–32 and 34–46; Allowed: 33 and 47–49
        return (1 <= day <= 32) or (34 <= day <= 46)

    def _tzeis_on(self, greg_date) -> datetime:
        # Shared cached zmanim sunset (Grossman) + havdalah offset — was
        # previously computed with a different astronomy library, so this
        # sensor now agrees with the rest of YidCal (its sibling nine_days
        # sensor already used the zmanim sunset).
        sunset = sunset_for_date(geo=self._geo, tz=self._tz, base_date=greg_date)
        return sunset + timedelta(minutes=self._havdalah)

    def _compute_chatzos_for_date(self, base_date) -> datetime:
        """Match ChatzosHayomSensor exactly: Grossman true solar transit, rounded half-up."""
        assert self._geo is not None
        # Grossman transit from the shared helper — matches the dedicated
        # chatzos sensor; replaces the old MGA midpoint (tiny value change,
        # intentional).
        return _round_half_up(
            chatzos_hayom_for_date(geo=self._geo, tz=self._tz, base_date=base_date)
        )

    def _build_sefirah_windows(self, hyear: int) -> list[tuple[datetime, datetime, str]]:
        """
        Build Sefirah prohibition windows (two segments typically).
        start = tzeis (sunset+havdalah) of the *evening that begins* the first prohibited Hebrew day
              = tzeis(greg of first_prohibited_day - 1)
        end   = tzeis of the *last prohibited Hebrew day*
        This naturally carves out Lag BaOmer (day 33) as fully allowed: tzeis(32) -> tzeis(33).
        """
        windows: list[tuple[datetime, datetime, str]] = []

        start_hd = HebrewDate(hyear, 1, 23 if self._diaspora else 22)
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
                start_dt = _round_half_up(self._tzeis_on(block_start.to_pydate() - timedelta(days=1)))
                end_dt = _round_ceil(self._tzeis_on(last.to_pydate()))
                windows.append((start_dt, end_dt, "sefirah"))
                in_block = False
                block_start = None
            cur = cur + 1

        if in_block and block_start:
            # Runs through Sivan 4 -> end at tzeis of Sivan 4
            last = end_hd
            start_dt = _round_half_up(self._tzeis_on(block_start.to_pydate() - timedelta(days=1)))
            end_dt = _round_ceil(self._tzeis_on(last.to_pydate()))
            windows.append((start_dt, end_dt, "sefirah"))

        return windows

    def _three_weeks_window(self, hyear: int) -> tuple[datetime, datetime, str]:
        """
        Start:
          • Normal year: tzeis that BEGINS 17 Tammuz (tzeis on the civil day before 17 Tammuz)
          • If 17 Tammuz is Shabbos (nidche): start at tzeis after Shabbos that begins 18 Tammuz
        End:
          • Normal year: 10 Av at Chatzos Hayom (Grossman transit, rounded like Chatzos sensor)
          • Nidche year (9 Av is Shabbos; fast on 10 Av): tzeis on 10 Av
        """
        # start — canonical observed 17 Tammuz (18th when 17 is Shabbos);
        # tzeis of the evening BEFORE the observed fast day, same as before.
        start_obs = he.shiva_asar_btamuz_observed(hyear)
        start_dt = _round_half_up(self._tzeis_on(start_obs - timedelta(days=1)))

        # end (depends on nidche — canonical rule)
        av10 = HebrewDate(hyear, 5, 10).to_pydate()
        if he.is_tisha_bav_nidche(hyear):
            end_dt = _round_ceil(self._tzeis_on(av10))
        else:
            # _compute_chatzos_for_date already returns an exact minute
            # (half-up). The old conditional ceil was a no-op on it; the
            # shared always-bump round_ceil would wrongly add a minute.
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
        current_start = None
        current_end = None
        next_start = None
        next_end = None

        for (ws, we, kind) in windows:
            if ws <= now < we:
                in_window = True
                current_start = ws
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
        self._this_window_starts = current_start
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
        attrs["This Window Start"] = (
            self._this_window_starts.isoformat() if self._this_window_starts else "N/A"
        )
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
