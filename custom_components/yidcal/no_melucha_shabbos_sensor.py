from __future__ import annotations

from hdate import HDateInfo
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.core import HomeAssistant

from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalDevice
from .zman_sensors import get_geo


def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class NoMeluchaShabbosSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """
    ON only on *regular* Shabbos:
      Friday sunset − candle_offset → Saturday sunset + havdalah_offset

    OFF if that Shabbos is also Yom Tov (e.g., RH on Shabbos, YT day on Shabbos).
    ON on Shabbos Chol HaMoed (since it isn't is_yom_tov).

    If Early Shabbos is enabled and applicable, the start time may be moved earlier
    based on sensor.yidcal_early_shabbos_yt_start_time.
    """
    _attr_name = "No Melucha – Regular Shabbos"
    _attr_icon = "mdi:briefcase-variant-off"
    _attr_unique_id = "yidcal_no_melucha_regular_shabbos"

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__()
        self.entity_id = "binary_sensor.yidcal_no_melucha_regular_shabbos"
        self._attr_unique_id = "yidcal_no_melucha_regular_shabbos"

        self.hass = hass
        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora = cfg.get("diaspora", True)
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._geo = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

        # Recalculate exactly on the minute (HH:MM:00)
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    # ─── helpers ────────────────────────────────────────────────────────────

    def _sunset(self, d) -> datetime:
        """Helper to get local sunset for a given civil date."""
        return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

    def _is_regular_shabbos(self, shabbos_date) -> bool:
        """Saturday and NOT Yom Tov."""
        if shabbos_date.weekday() != 5:
            return False
        return not HDateInfo(shabbos_date, diaspora=self._diaspora).is_yom_tov

    def _get_effective_early_start(self, friday_date):
        """
        Read effective early Shabbos start from:
          sensor.yidcal_early_shabbos_yt_start_time
        Attribute:
          effective_shabbos_start_by_date[YYYY-MM-DD] -> ISO datetime
        """
        try:
            st = self.hass.states.get("sensor.yidcal_early_shabbos_yt_start_time")
            if not st:
                return None

            eff = st.attributes.get("effective_shabbos_start_by_date") or {}
            iso = eff.get(friday_date.isoformat())
            if not iso:
                return None

            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self._tz)
            return dt.astimezone(self._tz)
        except Exception:
            return None

    def _shabbos_window(self, shabbos_date):
        """
        Raw window (unrounded) for a *regular* Shabbos:
          Friday sunset − candle_offset → Shabbos sunset + havdalah_offset

        If Early Shabbos is effective for that Friday, start earlier.
        """
        friday = shabbos_date - timedelta(days=1)

        # normal candles-based start
        start_dt = self._sunset(friday) - timedelta(minutes=self._candle)

        # early-entry override (only if earlier than candles start)
        early_dt = self._get_effective_early_start(friday)
        if early_dt and early_dt < start_dt:
            start_dt = early_dt

        end_dt = self._sunset(shabbos_date) + timedelta(minutes=self._havdalah)
        return start_dt, end_dt, early_dt  # return early_dt for attrs

    def _find_active_window(self, now_local: datetime):
        """Return active regular Shabbos raw window if now inside it."""
        base = now_local.date()
        for i in range(-7, 60):
            d = base + timedelta(days=i)
            if not self._is_regular_shabbos(d):
                continue
            start_dt, end_dt, _early = self._shabbos_window(d)
            if start_dt <= now_local < end_dt:
                return start_dt, end_dt, _early
        return None, None, None

    def _next_regular_shabbos_after(self, ref_date):
        """Next regular Shabbos on/after ref_date."""
        for i in range(0, 370):
            d = ref_date + timedelta(days=i)
            if self._is_regular_shabbos(d):
                return d
        return None

    # ─── main ──────────────────────────────────────────────────────────────

    async def async_update(self, _=None) -> None:
        if not self._geo:
            return

        now = dt_util.now().astimezone(self._tz)

        # 1) active window?
        s_raw, e_raw, early_raw = self._find_active_window(now)

        # 2) else next upcoming regular Shabbos
        if s_raw is None or e_raw is None:
            nxt_shabbos = self._next_regular_shabbos_after(now.date())
            if nxt_shabbos:
                s_raw, e_raw, early_raw = self._shabbos_window(nxt_shabbos)

        # 3) still nothing → OFF
        if s_raw is None or e_raw is None:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": "",
                "Window_End": "",
                "Early_Start_Used": False,
                "Early_Start_Time": "",
                "Activation_Logic": (
                    "ON for regular Shabbos only: Friday candle-lighting → Motzaei Shabbos (havdalah). "
                    "OFF if that Shabbos is Yom Tov. ON on Shabbos Chol HaMoed. "
                    "May start early if Early Shabbos is enabled and effective."
                ),
            }
            return

        # 4) rounding
        window_start = _round_half_up(s_raw)
        window_end = _round_ceil(e_raw)

        self._attr_is_on = window_start <= now < window_end

        early_used = bool(early_raw and early_raw < (self._sunset((window_start.date())) - timedelta(minutes=self._candle)))
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
            "Early_Start_Used": bool(early_raw and early_raw < s_raw + timedelta(seconds=1)),
            "Early_Start_Time": early_raw.isoformat() if early_raw else "",
            "Activation_Logic": (
                "ON for regular Shabbos only: Friday candle-lighting → Motzaei Shabbos (havdalah). "
                "OFF if that Shabbos is Yom Tov. ON on Shabbos Chol HaMoed. "
                "May start early if Early Shabbos is enabled and effective."
            ),
        }
