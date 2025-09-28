# /config/custom_components/yidcal/zman_sensors.py

from __future__ import annotations

import datetime
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import (
    async_track_time_change,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalDevice


# ─── Helper: compute holiday duration via pyluach ───────────────────────────

def get_holiday_duration(pydate: datetime.date) -> int:
    """
    Return the number of consecutive Yom Tov days starting at `pydate`,
    using pyluach to detect the festival name.
    """
    hd0 = HDateInfo(pydate, diaspora=True)
    if not hd0.is_yom_tov:
        return 0  # not a festival

    # Base holiday name without prefix day ("פסח א׳" → "פסח")
    base_name = PHebrewDate.from_pydate(pydate).holiday(
        hebrew=True, prefix_day=False
    )
    length = 1

    while True:
        next_date = pydate + timedelta(days=length)
        name2 = PHebrewDate.from_pydate(next_date).holiday(
            hebrew=True, prefix_day=False
        )
        if name2 == base_name:
            length += 1
        else:
            break

    return length


# ─── Geo helpers ────────────────────────────────────────────────────────────

def _create_geo(config) -> GeoLocation:
    return GeoLocation(
        name="YidCal",
        latitude=config["latitude"],
        longitude=config["longitude"],
        time_zone=config["tzname"],
        elevation=0,
    )

async def get_geo(hass: HomeAssistant) -> GeoLocation:
    config = hass.data[DOMAIN]["config"]
    return await hass.async_add_executor_job(_create_geo, config)


# ─── Lighting helpers for multi-day schedules ───────────────────────────────

def lighting_event_for_day(
    d: datetime.date,
    *,
    diaspora: bool,
    tz: ZoneInfo,
    geo: GeoLocation,
    candle_offset: int,
    havdalah_offset: int,
) -> tuple[datetime.datetime | None, str]:
    """
    Return the datetime (aware) of that civil day's candle-lighting event (if any),
    plus a machine-friendly kind:
      - 'erev_before_sunset'             → Erev Shabbos / Erev Yom Tov (weekday)
      - 'between_yt_after_tzeis'         → Yom Tov → Yom Tov (2nd night)
      - 'motzaei_shabbos_after_tzeis'    → Shabbos → Yom Tov (after tzeis)
      - 'none'                           → no lighting that civil day
    """
    hd_today = HDateInfo(d, diaspora=diaspora)
    hd_tom   = HDateInfo(d + timedelta(days=1), diaspora=diaspora)

    is_shabbos_today = (d.weekday() == 5)          # Saturday
    is_shabbos_tom   = ((d + timedelta(days=1)).weekday() == 5)
    is_yt_today = hd_today.is_yom_tov
    is_yt_tom   = hd_tom.is_yom_tov

    cal = ZmanimCalendar(geo_location=geo, date=d)
    sunset = cal.sunset().astimezone(tz)

    # Tomorrow Shabbos → standard Erev (before sunset)
    if is_shabbos_tom:
        return (sunset - timedelta(minutes=candle_offset), "erev_before_sunset")

    # Tomorrow Yom Tov
    if is_yt_tom:
        if is_shabbos_today:
            # Shabbos → YT (after tzeis)
            return (sunset + timedelta(minutes=havdalah_offset), "motzaei_shabbos_after_tzeis")
        if is_yt_today:
            # YT → YT (2nd night, after tzeis)
            return (sunset + timedelta(minutes=havdalah_offset), "between_yt_after_tzeis")
        # Weekday → YT (before sunset)
        return (sunset - timedelta(minutes=candle_offset), "erev_before_sunset")

    return (None, "none")

def label_for_kind_and_context(d: datetime.date, kind: str, *, diaspora: bool) -> str:
    """
    Human label for dashboards (short, clear).
    """
    if kind == "erev_before_sunset":
        # Tomorrow’s context
        tom = d + timedelta(days=1)
        is_shabbos_tom = (tom.weekday() == 5)
        is_yt_tom      = HDateInfo(tom, diaspora=diaspora).is_yom_tov

        # If the first Yom Tov day is on Shabbos (e.g., Rosh Hashanah on Shabbos)
        if is_shabbos_tom and is_yt_tom:
            return "Shabbos & Yom Tov"

        if is_shabbos_tom:
            return "Shabbos"
        if is_yt_tom:
            return "Yom Tov – Night 1"
        return "Candles"

    if kind == "between_yt_after_tzeis":
        return "Yom Tov – Night 2"

    if kind == "motzaei_shabbos_after_tzeis":
        return "Motzi Shabbos → Yom Tov"

    return "—"

# ─── Zman Erev Sensor ───────────────────────────────────────────────────────

class ZmanErevSensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:candelabra-fire"
    _attr_name = "Zman Erev"
    _attr_unique_id = "yidcal_zman_erev"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "zman_erev"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass

        config = hass.data[DOMAIN]["config"]
        self._candle  = config.get("candlelighting_offset", candle_offset)
        self._havdalah = config.get("havdalah_offset",     havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(self.hass, self._midnight_update, hour=0, minute=0, second=0)

    async def _midnight_update(self, now: datetime.datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()
        yesterday = today - timedelta(days=1)
        midnight_next = datetime.datetime.combine(
            today + timedelta(days=1), datetime.time(0), tzinfo=self._tz
        )

        def half_up(dt_local: datetime.datetime) -> datetime.datetime:
            if dt_local.second >= 30:
                dt_local += timedelta(minutes=1)
            return dt_local.replace(second=0, microsecond=0)

        def fmt_simple(dt_local: datetime.datetime) -> str:
            h, m = dt_local.hour % 12 or 12, dt_local.minute
            ampm = "AM" if dt_local.hour < 12 else "PM"
            return f"{h}:{m:02d} {ampm}"

        # Today's standard before-sunset (used as a safety)
        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset_today = cal_today.sunset().astimezone(self._tz)
        candle_today_std = sunset_today - timedelta(minutes=self._candle)

        # What is *today's* lighting (if any)?
        today_event, today_kind = lighting_event_for_day(
            today,
            diaspora=self._diaspora,
            tz=self._tz,
            geo=self._geo,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )

        # Helper: find most recent past lighting (scan back up to ~10 days)
        def most_recent_lighting_before(d0: datetime.date) -> datetime.datetime | None:
            for back in range(1, 11):
                d = d0 - timedelta(days=back)
                ev, _ = lighting_event_for_day(
                    d,
                    diaspora=self._diaspora,
                    tz=self._tz,
                    geo=self._geo,
                    candle_offset=self._candle,
                    havdalah_offset=self._havdalah,
                )
                if ev is not None:
                    return ev
            return None

        # Determine if *yesterday* was a Motzi night we care about:
        #   - Yesterday was Shabbos (Saturday), OR
        #   - Yesterday was Yom-Tov and *today* is NOT Yom-Tov (i.e., yesterday was the final YT day)
        # Find the most recent Motzi cutoff date:
        # return the civil date that begins right after a Motzi night (i.e., midnight after Shabbos/YT ends).
        def last_motzi_cutoff_date(ref: datetime.date) -> datetime.date | None:
            for back in range(0, 14):  # scan up to 2 weeks back
                d   = ref - timedelta(days=back)
                hd0 = HDateInfo(d, diaspora=self._diaspora)
                hd1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)

                ended_shabbos = (d.weekday() == 5) and (not hd1.is_yom_tov)           # Motzaei Shabbos not leading into YT
                ended_yomtov  = hd0.is_yom_tov and (not hd1.is_yom_tov)               # last YT day
                if ended_shabbos or ended_yomtov:
                    return d + timedelta(days=1)  # the civil day that starts right after Motzi
            return None

        hd_yesterday = HDateInfo(yesterday, diaspora=self._diaspora)
        hd_today     = HDateInfo(today,    diaspora=self._diaspora)

        # Allow forward jump on ANY day at/after the midnight that follows the most recent Motzi.
        cutoff = last_motzi_cutoff_date(today)
        allow_forward_jump_today = (cutoff is not None and today >= cutoff)


        # ── Shabbos/YT daytime freeze: show *yesterday's* lighting until midnight ──
        if (today.weekday() == 5 or hd_today.is_yom_tov) and now < midnight_next and today_event is None:
            y_event = most_recent_lighting_before(today) or (sunset_today - timedelta(minutes=self._candle))
            chosen_unrounded = y_event
            chosen = half_up(y_event)
        else:
            if today_event is not None:
                # There IS lighting today → show it (before or after it happens) and freeze until midnight
                chosen_unrounded = today_event
                chosen = half_up(today_event)
            else:
                # No lighting today.
                if allow_forward_jump_today:
                    # It's the first civil day after Motzi → now we're allowed to jump to the *next* lighting.
                    chosen_unrounded = None
                    chosen = None
                    for i in range(1, 11):
                        d = today + timedelta(days=i)
                        ev, _ = lighting_event_for_day(
                            d,
                            diaspora=self._diaspora,
                            tz=self._tz,
                            geo=self._geo,
                            candle_offset=self._candle,
                            havdalah_offset=self._havdalah,
                        )
                        if ev is not None:
                            chosen_unrounded = ev
                            chosen = half_up(ev)
                            break
                    if chosen is None:
                        # ultimate fallback → next Friday
                        wd = today.weekday()
                        days_to_fri = (4 - wd) % 7
                        d = today + timedelta(days=days_to_fri)
                        cal_fri = ZmanimCalendar(geo_location=self._geo, date=d)
                        s_fri = cal_fri.sunset().astimezone(self._tz)
                        chosen_unrounded = s_fri - timedelta(minutes=self._candle)
                        chosen = half_up(chosen_unrounded)
                else:
                    # Not allowed to jump yet (e.g., Mon/Tue/Wed/Thu after midnight).
                    # Keep showing the most recent past lighting.
                    y_event = most_recent_lighting_before(today)
                    if y_event is None:
                        # fallback: use standard yesterday-style calc
                        y_event = (sunset_today - timedelta(minutes=self._candle))
                    chosen_unrounded = y_event
                    chosen = half_up(y_event)

        # Publish main value (rounded minute in UTC)
        self._attr_native_value = chosen.astimezone(timezone.utc)

        # ── Base attrs (always present) ───────────────────────────────────────
        attrs: dict[str, object] = {
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
            # With_Seconds = UNROUNDED; Simple = ROUNDED
            "Zman_Erev_With_Seconds": (chosen_unrounded or chosen).astimezone(self._tz).isoformat(),
            "Zman_Erev_Simple": fmt_simple(chosen.astimezone(self._tz)),
        }

        # ── Build events list for [today-2 .. today+7] so Day_1 stays static inside clusters ──
        events = []
        for i in range(-2, 8):  # backtrack up to 2 days; look ahead 7
            d = today + timedelta(days=i)
            ev, kind = lighting_event_for_day(
                d,
                diaspora=self._diaspora,
                tz=self._tz,
                geo=self._geo,
                candle_offset=self._candle,
                havdalah_offset=self._havdalah,
            )
            if ev is not None:
                events.append((d, ev, kind))

        # Helper: is this lighting YT-related?
        def is_yt_related(d: datetime.date, kind: str) -> bool:
            if kind != "erev_before_sunset":
                return True
            return HDateInfo(d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov

        # Group into consecutive-day clusters
        clusters: list[list[tuple[datetime.date, datetime.datetime, str]]] = []
        if events:
            cur = [events[0]]
            for item in events[1:]:
                if item[0] == cur[-1][0] + timedelta(days=1):
                    cur.append(item)
                else:
                    clusters.append(cur)
                    cur = [item]
            clusters.append(cur)

        # Keep clusters that involve YT (drop plain Shabbos-only clusters)
        yt_clusters = [cl for cl in clusters if any(is_yt_related(d, k) for (d, _e, k) in cl)]

        # Pick active cluster anchored to the state’s base date.
        # base_date = local date of the chosen state timestamp
        base_date = (chosen_unrounded or chosen).astimezone(self._tz).date()

        active_cluster: list[tuple[datetime.date, datetime.datetime, str]] | None = None
        if yt_clusters:
            # 1) Prefer the cluster that CONTAINS the base_date (keeps Day_1/2/3 static inside the span)
            candidate = next(
                (cl for cl in yt_clusters if cl[0][0] <= base_date <= cl[-1][0]),
                None
            )
            if candidate is not None and any(is_yt_related(d, k) for (d, _e, k) in candidate):
                active_cluster = candidate
            else:
                # 2) Otherwise, choose the NEXT YT cluster that starts within the next 7 days (no past fallback)
                upcoming = [
                    cl for cl in yt_clusters
                    if today <= cl[0][0] <= (today + timedelta(days=7))
                ]
                if upcoming:
                    active_cluster = min(upcoming, key=lambda cl: cl[0][0])

        # Initialize Day_1/2/3 placeholders
        for i in (1, 2, 3):
            attrs[f"Day_{i}_Label"] = ""
            #attrs[f"Day_{i}_Date"] = ""
            #attrs[f"Day_{i}_With_Seconds"] = ""
            attrs[f"Day_{i}_Simple"] = ""

        if active_cluster:
            # ---- WINDOW GATE (dual windows) + suppression until a plain Shabbos is over ----
            wd = today.weekday()  # Mon=0..Sun=6
            days_since_shabbos = (wd - 5) % 7
            days_until_shabbos = (5 - wd) % 7
            last_shabbos       = today - timedelta(days=days_since_shabbos)
            next_shabbos       = today + timedelta(days=days_until_shabbos)
            next_next_shabbos  = next_shabbos + timedelta(days=7)

            windowA_start = last_shabbos
            windowA_end   = next_shabbos
            windowB_start = next_shabbos
            windowB_end   = next_next_shabbos

            cl_start = active_cluster[0][0]
            cl_end   = active_cluster[-1][0]

            def overlaps(a_start: datetime.date, a_end: datetime.date,
                         b_start: datetime.date, b_end: datetime.date) -> bool:
                return not (a_end < b_start or a_start > b_end)

            overlaps_A = overlaps(cl_start, cl_end, windowA_start, windowA_end)
            overlaps_B = overlaps(cl_start, cl_end, windowB_start, windowB_end)

            # The YT cluster is “connected” to this Shabbos if it contains that Shabbos
            # OR starts on Motzaei Shabbos (civil day after Shabbos).
            cluster_includes_next_shabbos  = (cl_start <= next_shabbos <= cl_end)
            cluster_starts_motzaei_shabbos = (cl_start == (next_shabbos + timedelta(days=1)))
            connected_ok = cluster_includes_next_shabbos or cluster_starts_motzaei_shabbos

            # Suppress preview if there is a plain Shabbos between today and the cluster start
            # and we haven't yet crossed Sunday 12:00 AM after that Shabbos.
            next_shabbos_is_yom_tov = HDateInfo(next_shabbos, diaspora=self._diaspora).is_yom_tov
            plain_shabbos_pending = (
                not next_shabbos_is_yom_tov
                and (cl_start > next_shabbos)           # YT starts after that Shabbos
                and (today < (next_shabbos + timedelta(days=1)))  # it isn't past Motzaei yet
            )

            # Base windowing + global suppression until that plain Shabbos finishes (unless connected)
            show_days = (overlaps_A or overlaps_B) and (not plain_shabbos_pending or connected_ok)

            if show_days:
                # Render Day_1.. from the **start** of the cluster (keeps static across span)
                for idx, (d, ev, kind) in enumerate(active_cluster[:3], start=1):
                    ev_unrounded_local = ev.astimezone(self._tz)
                    ev_rounded_local = (
                        (ev_unrounded_local + timedelta(minutes=1)).replace(second=0, microsecond=0)
                        if ev_unrounded_local.second >= 30
                        else ev_unrounded_local.replace(second=0, microsecond=0)
                    )
                    label = label_for_kind_and_context(d, kind, diaspora=self._diaspora)

                    attrs[f"Day_{idx}_Label"]  = label
                    #attrs[f"Day_{idx}_Date"] = (ev_unrounded_local + timedelta(days=1)).date().isoformat()
                    #attrs[f"Day_{idx}_With_Seconds"] = ev_unrounded_local.isoformat()
                    attrs[f"Day_{idx}_Simple"] = fmt_simple(ev_rounded_local)

        # publish attributes
        self._attr_extra_state_attributes = attrs

# ─── Zman Motzi Sensor ──────────────────────────────────────────────────────

class ZmanMotziSensor(YidCalDevice, RestoreEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:liquor"
    _attr_name = "Zman Motzi"
    _attr_unique_id = "yidcal_zman_motzi"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "zman_motzi"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass

        config = hass.data[DOMAIN]["config"]
        self._candle  = config.get("candlelighting_offset", candle_offset)
        self._havdalah = config.get("havdalah_offset",    havdalah_offset)
        self._diaspora = config.get("diaspora", True)
        self._tz = ZoneInfo(config.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        async_track_time_change(self.hass, self._midnight_update, hour=0, minute=0, second=0)

    async def _midnight_update(self, now: datetime.datetime) -> None:
        await self.async_update()

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()
        midnight_next = datetime.datetime.combine(today + timedelta(days=1), datetime.time(0), tzinfo=self._tz)

        def ceil_minute(dt_local: datetime.datetime) -> datetime.datetime:
            return (dt_local + timedelta(minutes=1)).replace(second=0, microsecond=0)

        def sunset_on(d: datetime.date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

        # ---------------- IN-SPAN YOM TOV: target current span's end ----------------
        hd_today = HDateInfo(today, diaspora=self._diaspora)
        if hd_today.is_yom_tov:
            # walk back to span start
            start = today
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)
            duration = get_holiday_duration(start)
            end_date = start + timedelta(days=duration - 1)

            target_unrounded = sunset_on(end_date) + timedelta(minutes=self._havdalah)
            full_iso = target_unrounded.isoformat()

            # freeze tonight (last YT day) until midnight
            if today == end_date and now >= target_unrounded and now < midnight_next:
                pass

            target = ceil_minute(target_unrounded)
            self._attr_native_value = target.astimezone(timezone.utc)

            lt = target.astimezone(self._tz)
            human = f"{(lt.hour % 12 or 12)}:{lt.minute:02d} {'AM' if lt.hour < 12 else 'PM'}"
            self._attr_extra_state_attributes = {
                "Zman_Motzi_With_Seconds": full_iso,
                "Zman_Motzi_Simple": human,
                "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
                "Latitude": self._geo.latitude,
                "Longitude": self._geo.longitude,
            }
            return

        # ---------------- NOT IN YT TODAY: compute candidates ----------------
        wd = today.weekday()  # Mon=0..Sun=6; Sat=5

        def sunset_on(d: datetime.date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)

        # 1) Shabbos candidate:
        #    If it's Saturday, always use *today* (even if havdalah already passed).
        #    Otherwise, use the next coming Saturday.
        if wd == 5:
            shabbos_date = today
        else:
            shabbos_date = today + timedelta(days=(5 - wd) % 7)

        shabbos_havdalah_unrounded = sunset_on(shabbos_date) + timedelta(minutes=self._havdalah)
        shabbos_havdalah_iso = shabbos_havdalah_unrounded.isoformat()

        # 2) Next Yom Tov span END havdalah (future-facing)
        yt_start: datetime.date | None = None
        yt_end_havdalah_unrounded: datetime.datetime | None = None
        yt_end_havdalah_iso: str | None = None

        for i in range(0, 90):  # scan up to ~3 months
            d = today + timedelta(days=i)
            hd_d = HDateInfo(d, diaspora=self._diaspora)
            hd_prev = HDateInfo(d - timedelta(days=1), diaspora=self._diaspora)
            if hd_d.is_yom_tov and not hd_prev.is_yom_tov:
                # walk forward to last YT day
                span_end = d
                j = 1
                while HDateInfo(d + timedelta(days=j), diaspora=self._diaspora).is_yom_tov:
                    span_end = d + timedelta(days=j)
                    j += 1
                cand_unrounded = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                if cand_unrounded >= now:
                    yt_start = d
                    yt_end_havdalah_unrounded = cand_unrounded
                    yt_end_havdalah_iso = cand_unrounded.isoformat()
                    break

        # 3) Cluster override: if next YT starts the day AFTER this Shabbos,
        #    show the END of that YT span (not Motzaei Shabbos).
        if yt_start is not None and yt_start == (shabbos_date + timedelta(days=1)):
            chosen_unrounded = yt_end_havdalah_unrounded
            chosen_iso = yt_end_havdalah_iso
        else:
            if yt_end_havdalah_unrounded is not None and yt_end_havdalah_unrounded < shabbos_havdalah_unrounded:
                chosen_unrounded = yt_end_havdalah_unrounded
                chosen_iso = yt_end_havdalah_iso
            else:
                chosen_unrounded = shabbos_havdalah_unrounded
                chosen_iso = shabbos_havdalah_iso

        # ---------------- freeze guards (keep chosen night until midnight) ----------------
        # If it's Saturday night and chosen is *tonight's* Shabbos havdalah, hold it until 12:00 AM.
        if wd == 5 and chosen_unrounded.date() == today and now >= chosen_unrounded and now < midnight_next:
            pass  # keep tonight

        # If chosen is end of a YT span that ends today, hold it until 12:00 AM.
        if yt_end_havdalah_unrounded is not None and chosen_unrounded == yt_end_havdalah_unrounded:
            end_day = (chosen_unrounded - timedelta(minutes=self._havdalah)).date()
            if now.date() == end_day and now >= chosen_unrounded and now < midnight_next:
                pass  # keep tonight

        # ---------------- publish ----------------
        target = ceil_minute(chosen_unrounded)
        self._attr_native_value = target.astimezone(timezone.utc)

        lt = target.astimezone(self._tz)
        human = f"{(lt.hour % 12 or 12)}:{lt.minute:02d} {'AM' if lt.hour < 12 else 'PM'}"
        self._attr_extra_state_attributes = {
            "Zman_Motzi_With_Seconds": chosen_iso,  # unrounded
            "Zman_Motzi_Simple": human,             # rounded HH:MM
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
        }
