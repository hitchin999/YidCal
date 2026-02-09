# custom_components/yidcal/erev_after_chatzos_sensor.py
"""
Binary sensor: Erev (After Chatzos)

ON when ALL of these are true:
  1. Today is Erev Shabbos or Erev Yom Tov (and not itself Shabbos/YT)
  2. Current time is AFTER chatzos hayom (midday)
  3. Current time is BEFORE the erev window end (candle-lighting / early start)

Attributes:
  Now                 – ISO current local time
  Is_Erev_Day         – whether today qualifies as an erev day at all
  Chatzos             – ISO chatzos hayom for today
  Erev_Window_End     – ISO effective candle-lighting cutoff
  Activation_Logic    – human-readable description
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
import homeassistant.util.dt as dt_util

from hdate import HDateInfo
from zmanim.zmanim_calendar import ZmanimCalendar

from .device import YidCalSpecialDevice
from .const import DOMAIN
from .zman_sensors import get_geo


# ── rounding helpers (same conventions as the rest of YidCal) ──
def _round_half_up(dt: datetime) -> datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime) -> datetime:
    return (
        (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)
        if dt.second or dt.microsecond
        else dt
    )


class ErevAfterChatzosSensor(YidCalSpecialDevice, BinarySensorEntity):
    """ON from chatzos until candle-lighting on Erev Shabbos / Erev Yom Tov."""

    _attr_name = "Erev After Chatzos"
    _attr_icon = "mdi:weather-sunset"

    def __init__(self, hass: HomeAssistant, candle_offset: int) -> None:
        super().__init__()
        slug = "erev_after_chatzos"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"

        self.hass = hass
        self._attr_is_on = False
        self._added = False

        self._candle = candle_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg["tzname"])
        self._diaspora: bool = cfg.get("diaspora", True)

        self._geo = None

    async def async_added_to_hass(self) -> None:
        self._added = True
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # Update every minute (on the :00 second, like other YidCal sensors)
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

    # ── helpers ──

    def _chatzos_for(self, d) -> datetime:
        """Compute chatzos hayom (MGA midpoint: dawn + 6 zmaniyos hours)."""
        cal = ZmanimCalendar(geo_location=self._geo, date=d)
        sunrise = cal.sunrise().astimezone(self._tz)
        sunset = cal.sunset().astimezone(self._tz)
        dawn = sunrise - timedelta(minutes=72)
        nightfall = sunset + timedelta(minutes=72)
        hour_td = (nightfall - dawn) / 12
        return _round_half_up(dawn + hour_td * 6)

    def _effective_erev_end(self, d) -> datetime:
        """Candle-lighting time (possibly overridden by early-start maps)."""
        cal = ZmanimCalendar(geo_location=self._geo, date=d)
        sunset = cal.sunset().astimezone(self._tz)
        candle_cut = _round_half_up(sunset - timedelta(minutes=self._candle))

        # Respect early-start maps if available
        early_state = self.hass.states.get(
            "sensor.yidcal_early_shabbos_yt_start_time"
        )
        if early_state and early_state.attributes:
            attrs = early_state.attributes
            key = d.isoformat()

            def _pick(*names):
                for n in names:
                    v = attrs.get(n)
                    if isinstance(v, dict):
                        return v
                return {}

            eff_shabbos = _pick(
                "Effective shabbos start by date",
                "Effective_Shabbos_Start_By_Date",
                "effective_shabbos_start_by_date",
            )
            eff_yomtov = _pick(
                "Effective yomtov start by date",
                "Effective_Yomtov_Start_By_Date",
                "effective_yomtov_start_by_date",
            )

            cuts = [candle_cut]
            for m in (eff_shabbos, eff_yomtov):
                val = m.get(key)
                if val:
                    try:
                        dt_local = (
                            val
                            if isinstance(val, datetime)
                            else datetime.fromisoformat(str(val))
                        )
                        if dt_local.tzinfo is None:
                            dt_local = dt_local.replace(tzinfo=self._tz)
                        cuts.append(_round_half_up(dt_local.astimezone(self._tz)))
                    except Exception:
                        pass
            return min(cuts)

        return candle_cut

    def _is_erev_day(self, today) -> bool:
        """True if today is Erev Shabbos (Friday, not YT) or Erev Yom Tov (weekday, not already YT/Shabbos)."""
        hd_today = HDateInfo(today, diaspora=self._diaspora)
        hd_tomorrow = HDateInfo(today + timedelta(days=1), diaspora=self._diaspora)

        is_yomtov_today = hd_today.is_yom_tov
        is_friday = today.weekday() == 4
        is_shabbos = today.weekday() == 5

        # Erev Shabbos: Friday that isn't Yom Tov
        if is_friday and not is_yomtov_today:
            return True

        # Erev Yom Tov: tomorrow is YT, today isn't Shabbos or YT
        if hd_tomorrow.is_yom_tov and not is_shabbos and not is_yomtov_today:
            return True

        return False

    # ── main update ──

    async def async_update(self, now=None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()

        is_erev = self._is_erev_day(today)
        chatzos = self._chatzos_for(today)
        erev_end = self._effective_erev_end(today)

        self._attr_is_on = is_erev and (chatzos <= now_local < erev_end)

        self._attr_extra_state_attributes = {
            "Now": now_local.isoformat(),
            "Is_Erev_Day": is_erev,
            "Chatzos": chatzos.isoformat(),
            "Erev_Window_End": erev_end.isoformat(),
            "Activation_Logic": (
                "ON from chatzos hayom until candle-lighting (or early start) "
                "on Erev Shabbos or Erev Yom Tov. OFF otherwise."
            ),
        }

        if self._added:
            self.async_write_ha_state()
