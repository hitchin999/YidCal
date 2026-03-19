# /config/custom_components/yidcal/zman_multiday_candle_sensors.py

"""
Separate timestamp sensors for Night 2 and Night 3 candle lighting.
Created when the user enables "Multi-day Candle Lighting Sensors" in config.

These sensors always show the NEXT upcoming occurrence of their respective
night, scanning up to ~400 days ahead.  They freeze (hold their current
value) until 12:00 AM on the civil day after Motzi (the end of the
Shabbos/YT span), then advance to the next occurrence.
"""

from __future__ import annotations

import datetime
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from hdate import HDateInfo

from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import (
    get_geo,
    lighting_event_for_day,
)


def _build_lighting_clusters(
    start_date: datetime.date,
    days_ahead: int,
    *,
    diaspora: bool,
    tz: ZoneInfo,
    geo,
    candle_offset: int,
    havdalah_offset: int,
) -> list[list[tuple[datetime.date, datetime.datetime, str]]]:
    """
    Scan `days_ahead` civil days from `start_date` and return all
    lighting-event clusters (groups of consecutive days that each have
    a lighting event).
    """
    events: list[tuple[datetime.date, datetime.datetime, str]] = []
    for i in range(days_ahead):
        d = start_date + timedelta(days=i)
        ev, kind = lighting_event_for_day(
            d,
            diaspora=diaspora,
            tz=tz,
            geo=geo,
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )
        if ev is not None:
            events.append((d, ev, kind))

    if not events:
        return []

    clusters: list[list[tuple[datetime.date, datetime.datetime, str]]] = []
    cur = [events[0]]
    for item in events[1:]:
        if item[0] == cur[-1][0] + timedelta(days=1):
            cur.append(item)
        else:
            clusters.append(cur)
            cur = [item]
    clusters.append(cur)
    return clusters


def _span_end_date(cluster_last_date: datetime.date, *, diaspora: bool) -> datetime.date:
    """
    Given the last lighting-event date in a cluster, find the actual
    Motzi date — the civil date on which the span ends at tzeis.
    The lighting event fires on the *erev* of the next holy day, so the
    span actually ends one day after the last lighting event's target day.

    For a cluster whose last event is on a date `d`:
      - If tomorrow (d+1) is Shabbos or YT, the span continues.
      - Walk forward until neither Shabbos nor YT.
    The Motzi civil date is the last Shabbos/YT day in the span.
    """
    d = cluster_last_date + timedelta(days=1)  # the day the last lighting ushers in
    while True:
        next_d = d + timedelta(days=1)
        is_shabbos_next = (next_d.weekday() == 5)
        is_yt_next = HDateInfo(next_d, diaspora=diaspora).is_yom_tov
        if is_shabbos_next or is_yt_next:
            d = next_d
        else:
            break
    return d  # this is the last holy day; Motzi is the evening of this date


def _motzi_civil_advance_date(span_end: datetime.date) -> datetime.date:
    """
    The civil date at whose 12:00 AM the sensors should advance.
    That's the day AFTER the span-end date (Motzi evening is on
    span_end; the next civil midnight is span_end + 1).
    """
    return span_end + timedelta(days=1)


class _MultidayCandleSensorBase(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """
    Abstract base for Night-2 / Night-3 candle lighting sensors.
    Subclasses set `_night_index` (1-based position in the cluster, so
    Night 2 → index 1, Night 3 → index 2).
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:candelabra-fire"
    _night_index: int  # 1 = Night 2, 2 = Night 3

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        self.hass = hass

        config = hass.data[DOMAIN]["config"]
        self._candle = config.get("candlelighting_offset", candle_offset)
        self._havdalah = config.get("havdalah_offset", havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(
            self.hass, self._midnight_update, hour=0, minute=0, second=0
        )

    async def _midnight_update(self, now: datetime.datetime) -> None:
        await self.async_update()

    def _ceil_minute(self, dt_local: datetime.datetime) -> datetime.datetime:
        return (dt_local + timedelta(minutes=1)).replace(second=0, microsecond=0)

    def _half_up(self, dt_local: datetime.datetime) -> datetime.datetime:
        if dt_local.second >= 30:
            dt_local += timedelta(minutes=1)
        return dt_local.replace(second=0, microsecond=0)

    def _round_for_kind(self, dt_local: datetime.datetime, kind: str) -> datetime.datetime:
        """After-tzeis candle lighting rounds up (chumrah); before-sunset uses half-up."""
        if kind in ("between_yt_after_tzeis", "motzaei_shabbos_after_tzeis"):
            return self._ceil_minute(dt_local)
        return self._half_up(dt_local)

    def _find_current_or_next(
        self, today: datetime.date, now: datetime.datetime
    ) -> tuple[datetime.datetime | None, str, list | None]:
        """
        Find the cluster that currently applies (we're still inside its
        span, before midnight-advance) or the next future cluster that has
        enough nights.

        Returns (target_event_dt, kind, cluster) or (None, "none", None).
        """
        if not self._geo:
            return None, "none", None

        # First check: are we inside an active span that hasn't advanced yet?
        # Look back up to 10 days to find a cluster whose span we might still be in.
        clusters_back = _build_lighting_clusters(
            today - timedelta(days=10),
            20,  # 10 back + 10 forward overlap
            diaspora=self._diaspora,
            tz=self._tz,
            geo=self._geo,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )

        for cl in clusters_back:
            if len(cl) < self._night_index + 1:
                continue  # not enough nights in this cluster
            span_end = _span_end_date(cl[-1][0], diaspora=self._diaspora)
            advance_date = _motzi_civil_advance_date(span_end)
            # If we haven't hit the advance date yet, this cluster is active
            if today < advance_date:
                entry = cl[self._night_index]
                return entry[1], entry[2], cl

        # Not inside an active span — scan forward for the next occurrence
        clusters_fwd = _build_lighting_clusters(
            today,
            400,
            diaspora=self._diaspora,
            tz=self._tz,
            geo=self._geo,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )

        for cl in clusters_fwd:
            if len(cl) < self._night_index + 1:
                continue
            entry = cl[self._night_index]
            target_ev = entry[1]
            # Must be in the future (or at least today)
            if target_ev.date() >= today:
                return target_ev, entry[2], cl
            # Even if the event date is past, check if we're still in the span
            span_end = _span_end_date(cl[-1][0], diaspora=self._diaspora)
            advance_date = _motzi_civil_advance_date(span_end)
            if today < advance_date:
                return target_ev, entry[2], cl

        return None, "none", None

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()

        target_dt, kind, cluster = self._find_current_or_next(today, now)

        if target_dt is None:
            self._attr_native_value = None
            self._attr_extra_state_attributes = {
                "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
                "Latitude": self._geo.latitude if self._geo else "",
                "Longitude": self._geo.longitude if self._geo else "",
            }
            return

        target_local = target_dt.astimezone(self._tz)
        rounded = self._round_for_kind(target_local, kind)

        self._attr_native_value = rounded.astimezone(timezone.utc)
        self._attr_extra_state_attributes = {
            "Zman_With_Seconds": target_local.isoformat(),
            "Zman_Simple": self._format_simple_time(rounded),
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
        }


class Night2CandleLightingSensor(_MultidayCandleSensorBase):
    """Timestamp sensor: next Night 2 candle lighting."""

    _attr_name = "Night 2 Candle Lighting"
    _attr_unique_id = "yidcal_night_2_candle_lighting"
    _night_index = 1  # second event in the cluster (0-based)

    def __init__(self, hass, candle_offset, havdalah_offset):
        super().__init__(hass, candle_offset, havdalah_offset)
        self.entity_id = "sensor.yidcal_night_2_candle_lighting"


class Night3CandleLightingSensor(_MultidayCandleSensorBase):
    """Timestamp sensor: next Night 3 candle lighting."""

    _attr_name = "Night 3 Candle Lighting"
    _attr_unique_id = "yidcal_night_3_candle_lighting"
    _night_index = 2  # third event in the cluster (0-based)

    def __init__(self, hass, candle_offset, havdalah_offset):
        super().__init__(hass, candle_offset, havdalah_offset)
        self.entity_id = "sensor.yidcal_night_3_candle_lighting"
