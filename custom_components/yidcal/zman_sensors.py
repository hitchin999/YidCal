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
        hd_yesterday = HDateInfo(yesterday, diaspora=self._diaspora)
        hd_today = HDateInfo(today, diaspora=self._diaspora)
        yesterday_was_shabbos = (yesterday.weekday() == 5)
        yesterday_was_final_yt = (hd_yesterday.is_yom_tov and not hd_today.is_yom_tov)
        allow_forward_jump_today = (yesterday_was_shabbos or yesterday_was_final_yt)

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

        # ── Build multi-lighting attributes (STATIC across cluster) ──────────
        # Collect events from yesterday through ~10 days ahead so we can
        # backtrack to the cluster's first day if we're already inside it.
        events = []
        for i in range(-2, 12):  # include a couple days back
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

        # Find any adjacency, then backtrack to earliest consecutive-day start (cluster anchor)
        cluster_start_idx = None
        for j in range(len(events) - 1):
            if events[j + 1][0] == events[j][0] + timedelta(days=1):
                # backtrack while previous days are also consecutive
                k = j
                while k - 1 >= 0 and events[k][0] == events[k - 1][0] + timedelta(days=1):
                    k -= 1
                cluster_start_idx = k
                break

        def fmt_simple(dt_local: datetime.datetime) -> str:
            h, m = dt_local.hour % 12 or 12, dt_local.minute
            ampm = "AM" if dt_local.hour < 12 else "PM"
            return f"{h}:{m:02d} {ampm}"

        # Base attrs (always present)
        attrs: dict[str, object] = {
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
            # With_Seconds = UNROUNDED; Simple = ROUNDED
            "Zman_Erev_With_Seconds": (chosen_unrounded or chosen).astimezone(self._tz).isoformat(),
            "Zman_Erev_Simple": fmt_simple(chosen.astimezone(self._tz)),
            # helpful flags
            #"has_cluster": False,
            #"cluster_size": 0,
        }

        # ALWAYS initialize Day 1/2/3 placeholders (stable schema)
        for i in (1, 2, 3):
            attrs[f"Day_{i}_Label"] = ""
            #attrs[f"Day_{i}_Date"] = ""
            #attrs[f"Day_{i}_With_Seconds"] = ""
            attrs[f"Day_{i}_Simple"] = ""

        if cluster_start_idx is not None:
            # From the first day of the cluster, take up to 3 consecutive days
            cluster = [events[cluster_start_idx]]
            k = cluster_start_idx + 1
            while (
                k < len(events)
                and events[k][0] == cluster[-1][0] + timedelta(days=1)
                and len(cluster) < 3
            ):
                cluster.append(events[k])
                k += 1

            # ---- UPDATED WINDOW GATE (dual windows) ----
            # Show Day_* if the cluster overlaps EITHER:
            #   A) [last regular Shabbos .. next regular Shabbos]   (handles Sun/Mon/Tue YT like RH)
            #   B) [next regular Shabbos .. the Shabbos after that] (handles Fri Shabbos → YT)
            wd = today.weekday()  # Mon=0..Sun=6
            days_since_shabbos = (wd - 5) % 7
            days_until_shabbos = (5 - wd) % 7
            last_shabbos = today - timedelta(days=days_since_shabbos)
            next_shabbos = today + timedelta(days=days_until_shabbos)
            next_next_shabbos = next_shabbos + timedelta(days=7)

            windowA_start = last_shabbos
            windowA_end   = next_shabbos
            windowB_start = next_shabbos
            windowB_end   = next_next_shabbos

            cluster_start_date = cluster[0][0]
            cluster_end_date   = cluster[-1][0]

            def overlaps(a_start: datetime.date, a_end: datetime.date,
                         b_start: datetime.date, b_end: datetime.date) -> bool:
                return not (a_end < b_start or a_start > b_end)

            overlaps_A = overlaps(cluster_start_date, cluster_end_date, windowA_start, windowA_end)
            overlaps_B = overlaps(cluster_start_date, cluster_end_date, windowB_start, windowB_end)
            show_days = overlaps_A or overlaps_B

            #attrs["has_cluster"] = True
            #attrs["cluster_size"] = len(cluster)

            if show_days:
                for idx, (d, ev, kind) in enumerate(cluster, start=1):
                    ev_unrounded_local = ev.astimezone(self._tz)
                    # Erev uses half-up rounding for display
                    ev_rounded_local = (ev_unrounded_local + timedelta(minutes=1)).replace(second=0, microsecond=0) \
                        if ev_unrounded_local.second >= 30 else ev_unrounded_local.replace(second=0, microsecond=0)
                    label = label_for_kind_and_context(d, kind, diaspora=self._diaspora)

                    attrs[f"Day_{idx}_Label"] = label                # e.g., "Shabbos", "Yom Tov – Night 1"
                    #attrs[f"Day_{idx}_Date"] = d.isoformat()
                    #attrs[f"Day_{idx}_With_Seconds"] = ev_unrounded_local.isoformat()
                    attrs[f"Day_{idx}_Simple"] = fmt_simple(ev_rounded_local)


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

        def next_saturday_future(now_date: datetime.date) -> datetime.date:
            """Return the next Saturday such that its havdalah will be >= now."""
            base = now_date if now_date.weekday() == 5 else now_date + timedelta(days=(5 - now_date.weekday()) % 7)
            base_hav = sunset_on(base) + timedelta(minutes=self._havdalah)
            return base if base_hav >= now else (base + timedelta(days=7))

        # 1) Next Shabbos havdalah (guaranteed future-facing)
        shabbos_date = next_saturday_future(today)
        shabbos_havdalah_unrounded = sunset_on(shabbos_date) + timedelta(minutes=self._havdalah)
        shabbos_havdalah_iso = shabbos_havdalah_unrounded.isoformat()

        # 2) Next Yom Tov span END havdalah (walk with is_yom_tov; guaranteed future-facing)
        yt_start: datetime.date | None = None
        yt_end_havdalah_unrounded: datetime.datetime | None = None
        yt_end_havdalah_iso: str | None = None

        for i in range(0, 90):  # scan up to ~3 months
            d = today + timedelta(days=i)
            hd_d = HDateInfo(d, diaspora=self._diaspora)
            hd_prev = HDateInfo(d - timedelta(days=1), diaspora=self._diaspora)
            # first civil day of an upcoming YT span
            if hd_d.is_yom_tov and not hd_prev.is_yom_tov:
                # Walk forward while is_yom_tov stays True → end is last True day
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

        # 3) Cluster override: if next YT starts the day AFTER the next Shabbos,
        #    show the END of that YT span (not Motzaei Shabbos).
        if yt_start is not None and yt_start == (shabbos_date + timedelta(days=1)):
            chosen_unrounded = yt_end_havdalah_unrounded
            chosen_iso = yt_end_havdalah_iso
        else:
            # Otherwise pick the earliest FUTURE candidate that exists
            if yt_end_havdalah_unrounded is not None and yt_end_havdalah_unrounded < shabbos_havdalah_unrounded:
                chosen_unrounded = yt_end_havdalah_unrounded
                chosen_iso = yt_end_havdalah_iso
            else:
                chosen_unrounded = shabbos_havdalah_unrounded
                chosen_iso = shabbos_havdalah_iso

        # ---------------- freeze guards (keep chosen night until midnight) ----------------
        # If chosen is Shabbos tonight
        if wd == 5 and chosen_unrounded.date() == today and now >= chosen_unrounded and now < midnight_next:
            pass  # keep tonight's Shabbos havdalah

        # If chosen is end of a YT span that ends today
        if yt_end_havdalah_unrounded is not None and chosen_unrounded == yt_end_havdalah_unrounded:
            end_day = (chosen_unrounded - timedelta(minutes=self._havdalah)).date()
            if now.date() == end_day and now >= chosen_unrounded and now < midnight_next:
                pass  # keep tonight's YT end

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

