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

    Attributes always show the current active Shabbos window, or the next
    upcoming regular Shabbos window if none is active:
      Now, Window_Start, Window_End, Activation_Logic
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

        # Recalculate exactly on the minute (HH:MM:00), matching rounded Zman times
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
        """
        True iff:
          • shabbos_date is Saturday, and
          • that day is NOT Yom Tov (HDateInfo.is_yom_tov is False).

        This means:
          • OFF when Shabbos is also Yom Tov
          • ON on Shabbos Chol HaMoed
        """
        if shabbos_date.weekday() != 5:  # Saturday
            return False
        return not HDateInfo(shabbos_date, diaspora=self._diaspora).is_yom_tov

    def _shabbos_window(self, shabbos_date):
        """
        Raw window (unrounded) for a *regular* Shabbos:
          Friday sunset − candle_offset → Shabbos sunset + havdalah_offset
        """
        friday = shabbos_date - timedelta(days=1)
        start_dt = self._sunset(friday) - timedelta(minutes=self._candle)
        end_dt = self._sunset(shabbos_date) + timedelta(minutes=self._havdalah)
        return start_dt, end_dt

    def _find_active_window(self, now_local: datetime):
        """
        If we're currently inside a *regular* Shabbos window,
        return (start_dt, end_dt). Otherwise (None, None).

        Scan a generous window around 'now' to avoid edge issues.
        """
        base = now_local.date()
        for i in range(-7, 60):  # one week back, ~2 months forward
            d = base + timedelta(days=i)
            if not self._is_regular_shabbos(d):
                continue
            start_dt, end_dt = self._shabbos_window(d)
            if start_dt <= now_local < end_dt:
                return start_dt, end_dt
        return None, None

    def _next_regular_shabbos_after(self, ref_date):
        """
        Find the *next* regular Shabbos (Saturday that is NOT Yom Tov),
        on or after ref_date, up to ~1 year ahead.
        """
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

        # 1) Try to find an active regular Shabbos window containing 'now'
        s_raw, e_raw = self._find_active_window(now)

        # 2) If none active, pick the next upcoming regular Shabbos
        if s_raw is None or e_raw is None:
            nxt_shabbos = self._next_regular_shabbos_after(now.date())
            if nxt_shabbos:
                s_raw, e_raw = self._shabbos_window(nxt_shabbos)

        # 3) If still nothing, turn OFF and publish blanks (extremely unlikely)
        if s_raw is None or e_raw is None:
            self._attr_is_on = False
            self._attr_extra_state_attributes = {
                "Now": now.isoformat(),
                "Window_Start": "",
                "Window_End": "",
                "Activation_Logic": (
                    "ON for regular Shabbos only: Friday candle-lighting → Motzaei Shabbos (havdalah). "
                    "OFF if that Shabbos is Yom Tov. ON on Shabbos Chol HaMoed."
                ),
            }
            return

        # 4) Apply the same rounding semantics as the global No Melucha window
        window_start = _round_half_up(s_raw)
        window_end = _round_ceil(e_raw)

        # ON only inside this rounded window
        self._attr_is_on = window_start <= now < window_end
        self._attr_extra_state_attributes = {
            "Now": now.isoformat(),
            "Window_Start": window_start.isoformat(),
            "Window_End": window_end.isoformat(),
            "Activation_Logic": (
                "ON for regular Shabbos only: Friday candle-lighting → Motzaei Shabbos (havdalah). "
                "OFF if that Shabbos is Yom Tov. ON on Shabbos Chol HaMoed."
            ),
        }
