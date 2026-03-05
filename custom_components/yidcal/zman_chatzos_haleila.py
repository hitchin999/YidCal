from __future__ import annotations
from datetime import datetime, timedelta, timezone, date as date_cls
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_point_in_time,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo


class ChatzosHaLailaSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """חצות הלילה עפ\"י המג\"א (6 שעות זמניות מתחילת הלילה).

    Anchored to the halachic night (sunset → עלות השחר), not the English date.
    Between midnight and עלות the sensor still shows the current night's חצות,
    not tomorrow night's.  It transitions to the next night only at עלות.
    """

    _attr_device_class  = SensorDeviceClass.TIMESTAMP
    _attr_icon          = "mdi:weather-night"
    _attr_name          = "Chatzos HaLaila"
    _attr_unique_id     = "yidcal_chatzos_haleila"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "chatzos_haleila"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass      = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None
        self._alot_unsub = None          # listener handle for the עלות refresh

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)

        # initial calc
        await self.async_update()

        # safety net: still refresh at midnight in case HA restarts, etc.
        async_track_time_change(
            self.hass,
            self._midnight_update,
            hour=0, minute=0, second=0,
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    # ── helpers ──────────────────────────────────────────────────────────

    def _alot_for_date(self, civil_date: date_cls) -> datetime:
        """Return עלות השחר (sunrise − 72 min) for a given civil date, tz-aware."""
        cal = ZmanimCalendar(geo_location=self._geo, date=civil_date)
        sunrise = cal.sunrise().astimezone(self._tz)
        return sunrise - timedelta(minutes=72)

    def _halachic_base_date(self, now_local: datetime) -> date_cls:
        """Return the civil date whose *sunset* started the current halachic night.

        • If now < עלות of today  → still in last night  → yesterday
        • If now ≥ עלות of today  → daytime or tonight   → today

        This means 11:56 PM Mon and 12:01 AM Tue both resolve to Monday,
        giving the same חצות for the same halachic night.
        """
        today = now_local.date()
        alot_today = self._alot_for_date(today)

        if now_local < alot_today:
            return today - timedelta(days=1)
        return today

    def _compute_chatzos_for_date(self, base_date: date_cls) -> tuple[datetime, str]:
        """Compute Chatzos HaLaila for the night that begins at base_date's sunset.

        Returns (rounded_local_dt, precise_iso_string_in_local_tz).
        """
        assert self._geo is not None

        # Start of halachic night: sunset + 72 min (tzeis R"T)
        cal_today = ZmanimCalendar(geo_location=self._geo, date=base_date)
        sunset = cal_today.sunset().astimezone(self._tz)
        night_start = sunset + timedelta(minutes=72)

        # Dawn (עלות) of the next morning
        cal_next = ZmanimCalendar(
            geo_location=self._geo, date=base_date + timedelta(days=1)
        )
        sunrise_next = cal_next.sunrise().astimezone(self._tz)
        dawn_next = sunrise_next - timedelta(minutes=72)

        # 6 שעות זמניות from night-start = midpoint
        hour_td = (dawn_next - night_start) / 12
        target = night_start + hour_td * 6

        full_iso_local = target.isoformat()

        # rounding: <30 s → floor, ≥30 s → ceil; then zero out seconds/µs
        if target.second >= 30:
            target += timedelta(minutes=1)
        target = target.replace(second=0, microsecond=0)

        return target, full_iso_local

    def _schedule_alot_refresh(self, now_local: datetime) -> None:
        """Schedule a one-shot refresh at the next עלות השחר.

        This is the moment the sensor should flip from 'tonight' to
        'the coming night'.
        """
        # cancel previous if any
        if self._alot_unsub is not None:
            self._alot_unsub()
            self._alot_unsub = None

        today = now_local.date()

        # figure out the next עלות that is still in the future
        alot_today = self._alot_for_date(today)
        if now_local < alot_today:
            next_alot = alot_today
        else:
            next_alot = self._alot_for_date(today + timedelta(days=1))

        # schedule slightly after (1 s) to avoid edge-case ties
        fire_at = (next_alot + timedelta(seconds=1)).astimezone(timezone.utc)

        @callback
        def _alot_cb(_now: datetime) -> None:
            self.hass.async_create_task(self.async_update())

        self._alot_unsub = async_track_point_in_time(
            self.hass, _alot_cb, fire_at
        )

    # ── main update ─────────────────────────────────────────────────────

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)

        # ---- determine the halachic base date ----
        base = self._halachic_base_date(now_local)

        # tonight / last night / tomorrow night
        tonight_dt,   tonight_iso   = self._compute_chatzos_for_date(base)
        last_night_dt, _            = self._compute_chatzos_for_date(base - timedelta(days=1))
        tom_night_dt,  _            = self._compute_chatzos_for_date(base + timedelta(days=1))

        # primary state → tonight's chatzos in UTC
        self._attr_native_value = tonight_dt.astimezone(timezone.utc)

        # human strings
        human_tonight   = self._format_simple_time(tonight_dt)
        human_last      = self._format_simple_time(last_night_dt)
        human_tomorrow  = self._format_simple_time(tom_night_dt)

        self._attr_extra_state_attributes = {
            "Chatzos_Haleila_With_Seconds": tonight_iso,
            "Chatzos_Haleila_Simple":       human_tonight,
            "Tomorrows_Simple":             human_tomorrow,
            "Yesterdays_Simple":            human_last,
        }

        # schedule the next refresh at עלות
        self._schedule_alot_refresh(now_local)
