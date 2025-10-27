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
from .device import YidCalZmanDevice


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

class ZmanErevSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
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

        cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
        sunset_today = cal_today.sunset().astimezone(self._tz)
        candle_today_std = sunset_today - timedelta(minutes=self._candle)

        today_event, today_kind = lighting_event_for_day(
            today,
            diaspora=self._diaspora,
            tz=self._tz,
            geo=self._geo,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )
        
        # ---- Helpers: first-night (erev_before_sunset) only ----
        def _next_erev_before_sunset_after(ref_dt: datetime.datetime) -> datetime.datetime | None:
            base = ref_dt.date()
            for i in range(0, 30):  # look ahead ~1 month
                d = base + timedelta(days=i)
                ev, kind = lighting_event_for_day(
                    d,
                    diaspora=self._diaspora,
                    tz=self._tz,
                    geo=self._geo,
                    candle_offset=self._candle,
                    havdalah_offset=self._havdalah,
                )
                if ev is not None and kind == "erev_before_sunset" and ev > ref_dt:
                    return ev
            return None

        def _last_erev_before_sunset_before(ref_dt: datetime.datetime) -> datetime.datetime | None:
            base = ref_dt.date()
            # include today first (if today's first-night lighting already passed)
            ev_today, kind_today = lighting_event_for_day(
                base,
                diaspora=self._diaspora,
                tz=self._tz,
                geo=self._geo,
                candle_offset=self._candle,
                havdalah_offset=self._havdalah,
            )
            if ev_today is not None and kind_today == "erev_before_sunset" and ev_today < ref_dt:
                return ev_today

            # then walk back in time
            for i in range(1, 30):  # look back ~1 month
                d = base - timedelta(days=i)
                ev, kind = lighting_event_for_day(
                    d,
                    diaspora=self._diaspora,
                    tz=self._tz,
                    geo=self._geo,
                    candle_offset=self._candle,
                    havdalah_offset=self._havdalah,
                )
                if ev is not None and kind == "erev_before_sunset" and ev < ref_dt:
                    return ev
            return None

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

        # Allow forward jump on/after the civil day that begins after the last Motzi
        def last_motzi_cutoff_date(ref: datetime.date) -> datetime.date | None:
            for back in range(0, 14):
                d   = ref - timedelta(days=back)
                hd0 = HDateInfo(d, diaspora=self._diaspora)
                hd1 = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)
                ended_shabbos = (d.weekday() == 5) and (not hd1.is_yom_tov)
                ended_yomtov  = hd0.is_yom_tov and (not hd1.is_yom_tov)
                if ended_shabbos or ended_yomtov:
                    return d + timedelta(days=1)
            return None

        hd_today = HDateInfo(today, diaspora=self._diaspora)
        cutoff = last_motzi_cutoff_date(today)
        allow_forward_jump_today = (cutoff is not None and today >= cutoff)

        # Freeze rule
        if (today.weekday() == 5 or hd_today.is_yom_tov) and now < midnight_next and today_event is None:
            y_event = most_recent_lighting_before(today) or (sunset_today - timedelta(minutes=self._candle))
            chosen_unrounded = y_event
            chosen = half_up(y_event)
        else:
            if today_event is not None:
                chosen_unrounded = today_event
                chosen = half_up(today_event)
            else:
                if allow_forward_jump_today:
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
                        wd = today.weekday()
                        days_to_fri = (4 - wd) % 7
                        d = today + timedelta(days=days_to_fri)
                        cal_fri = ZmanimCalendar(geo_location=self._geo, date=d)
                        s_fri = cal_fri.sunset().astimezone(self._tz)
                        chosen_unrounded = s_fri - timedelta(minutes=self._candle)
                        chosen = half_up(chosen_unrounded)
                else:
                    y_event = most_recent_lighting_before(today)
                    if y_event is None:
                        y_event = (sunset_today - timedelta(minutes=self._candle))
                    chosen_unrounded = y_event
                    chosen = half_up(y_event)

        # State (UTC)
        self._attr_native_value = chosen.astimezone(timezone.utc)

        # Base attrs
        attrs: dict[str, object] = {
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
            "Zman_Erev_With_Seconds": (chosen_unrounded or chosen).astimezone(self._tz).isoformat(),
            "Zman_Erev_Simple": fmt_simple(chosen.astimezone(self._tz)),
        }

        # Build events list for [today-2 .. today+7]
        events = []
        for i in range(-2, 8):
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

        def is_yt_related(d: datetime.date, kind: str) -> bool:
            if kind != "erev_before_sunset":
                return True
            return HDateInfo(d + timedelta(days=1), diaspora=self._diaspora).is_yom_tov

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

        yt_clusters = [cl for cl in clusters if any(is_yt_related(d, k) for (d, _e, k) in cl)]

        base_date = (chosen_unrounded or chosen).astimezone(self._tz).date()
        active_cluster: list[tuple[datetime.date, datetime.datetime, str]] | None = None
        if yt_clusters:
            candidate = next(
                (cl for cl in yt_clusters if cl[0][0] <= base_date <= cl[-1][0]),
                None
            )
            if candidate is not None and any(is_yt_related(d, k) for (d, _e, k) in candidate):
                active_cluster = candidate
            else:
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
            # Gate + suppression rules
            wd = today.weekday()
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

            cluster_includes_next_shabbos  = (cl_start <= next_shabbos <= cl_end)
            cluster_starts_motzaei_shabbos = (cl_start == (next_shabbos + timedelta(days=1)))
            connected_ok = cluster_includes_next_shabbos or cluster_starts_motzaei_shabbos

            next_shabbos_is_yom_tov = HDateInfo(next_shabbos, diaspora=self._diaspora).is_yom_tov
            plain_shabbos_pending = (
                not next_shabbos_is_yom_tov
                and (cl_start > next_shabbos)
                and (today < (next_shabbos + timedelta(days=1)))
            )

            show_days = (overlaps_A or overlaps_B) and (not plain_shabbos_pending or connected_ok)

            if show_days:
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
                    
        # ---- Next/Last Zman Erev (first-night only; ignores Day_1/2/3) ----
        ref_dt = (chosen_unrounded or chosen)  # current target datetime (aware)

        next_ev = _next_erev_before_sunset_after(ref_dt)
        if next_ev is not None:
            nl = next_ev.astimezone(self._tz)
            attrs["Next_Zman_Erev_Date"]   = (nl + timedelta(days=1)).date().isoformat()  # date it ushers in
            attrs["Next_Zman_Erev_Simple"] = fmt_simple(half_up(nl))

        last_ev = _last_erev_before_sunset_before(ref_dt)
        if last_ev is not None:
            ll = last_ev.astimezone(self._tz)
            attrs["Last_Zman_Erev_Date"]   = (ll + timedelta(days=1)).date().isoformat()  # date it ushered in
            attrs["Last_Zman_Erev_Simple"] = fmt_simple(half_up(ll))

        self._attr_extra_state_attributes = attrs


# ─── Zman Motzi Sensor ──────────────────────────────────────────────────────

class ZmanMotziSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
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

    # ---- Helper: find true end of current YT span (handles SA→ST in diaspora) ----
    def _yt_span_end(self, start: datetime.date) -> datetime.date:
        """
        Return the last civil date of the current Yom Tov span starting at `start`.
        In the diaspora, Shemini Atzeres is immediately followed by Simchas Torah
        (even though holiday names differ), so treat them as one continuous span.
        """
        end = start
        # Walk forward as long as the next day is also Yom Tov
        while HDateInfo(end + timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
            end += timedelta(days=1)

        if self._diaspora:
            # If the span endpoint is SA and the next day is ST, extend one more day
            name_end  = PHebrewDate.from_pydate(end).holiday(hebrew=True, prefix_day=False)
            name_next = PHebrewDate.from_pydate(end + timedelta(days=1)).holiday(hebrew=True, prefix_day=False)
            if name_end == "שמיני עצרת" and name_next == "שמחת תורה":
                end = end + timedelta(days=1)

        return end

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            return

        now = (now or dt_util.now()).astimezone(self._tz)
        today = now.date()
        midnight_next = datetime.datetime.combine(today + timedelta(days=1), datetime.time(0), tzinfo=self._tz)

        def ceil_minute(dt_local: datetime.datetime) -> datetime.datetime:
            return (dt_local + timedelta(minutes=1)).replace(second=0, microsecond=0)

        def fmt_simple(dt_local: datetime.datetime) -> str:
            h, m = dt_local.hour % 12 or 12, dt_local.minute
            ampm = "AM" if dt_local.hour < 12 else "PM"
            return f"{h}:{m:02d} {ampm}"

        def sunset_on(d: datetime.date) -> datetime.datetime:
            return ZmanimCalendar(geo_location=self._geo, date=d).sunset().astimezone(self._tz)
            
        # Helper: nearest earlier Motzi (Shabbos havdalah or YT-span end) before a reference datetime
        def _last_motzi_before(ref_dt: datetime.datetime) -> datetime.datetime | None:
            best: datetime.datetime | None = None
            for i in range(1, 30):  # look back up to ~1 month
                d = ref_dt.date() - timedelta(days=i)

                # Shabbos cand
                if d.weekday() == 5:
                    cand = sunset_on(d) + timedelta(minutes=self._havdalah)
                    if cand < ref_dt and (best is None or cand > best):
                        best = cand

                # YT span end cand (first YT day → walk to end)
                if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                    span_end = self._yt_span_end(d)  # handles SA→ST in diaspora
                    end_dt = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                    if end_dt < ref_dt and (best is None or end_dt > best):
                        best = end_dt
            return best
            
        # Helper: nearest earlier Motzi (Shabbos havdalah or YT-span end) before a reference datetime
        def _last_motzi_before(ref_dt: datetime.datetime) -> datetime.datetime | None:
            best: datetime.datetime | None = None
            for i in range(1, 30):  # look back up to ~1 month
                d = ref_dt.date() - timedelta(days=i)

                # Shabbos candidate
                if d.weekday() == 5:
                    cand = sunset_on(d) + timedelta(minutes=self._havdalah)
                    if cand < ref_dt and (best is None or cand > best):
                        best = cand

                # Yom Tov span end candidate (first day of a YT span → walk to end)
                if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                    span_end = self._yt_span_end(d)  # handles SA→ST in diaspora
                    end_dt = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                    if end_dt < ref_dt and (best is None or end_dt > best):
                        best = end_dt
            return best

        # ---------------- IN-SPAN YOM TOV: target current span's end ----------------
        hd_today = HDateInfo(today, diaspora=self._diaspora)
        if hd_today.is_yom_tov:
            # walk back to span start
            start = today
            while HDateInfo(start - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                start -= timedelta(days=1)

            # NEW: robust end (handles SA→ST)
            end_date = self._yt_span_end(start)

            target_unrounded = sunset_on(end_date) + timedelta(minutes=self._havdalah)
            full_iso = target_unrounded.isoformat()

            # freeze tonight (last YT day) until midnight
            if today == end_date and now >= target_unrounded and now < midnight_next:
                pass

            target = ceil_minute(target_unrounded)
            self._attr_native_value = target.astimezone(timezone.utc)

            # --- Also compute the NEXT Motzi after this span ends ---
            ref_dt = target_unrounded  # strictly after this for scanning

            # Next Shabbos after ref
            def next_saturday_future_from(ref_dt: datetime.datetime) -> tuple[datetime.date, datetime.datetime]:
                base_date = ref_dt.date()
                wd = base_date.weekday()
                sat = base_date if wd == 5 else base_date + timedelta(days=(5 - wd) % 7)
                cand = sunset_on(sat) + timedelta(minutes=self._havdalah)
                if cand <= ref_dt:
                    sat = sat + timedelta(days=7)
                    cand = sunset_on(sat) + timedelta(minutes=self._havdalah)
                return sat, cand

            # Next YT end after ref
            def next_yt_end_after(ref_dt: datetime.datetime) -> tuple[datetime.date, datetime.datetime] | None:
                start_scan = ref_dt.date() + timedelta(days=1)
                for i in range(0, 30):
                    d = start_scan + timedelta(days=i)
                    if HDateInfo(d, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                        span_end = self._yt_span_end(d)
                        end_dt = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                        if end_dt > ref_dt:
                            return span_end, end_dt
                return None

            _, next_sh_dt = next_saturday_future_from(ref_dt)
            yt_next = next_yt_end_after(ref_dt)
            if yt_next is not None and yt_next[1] < next_sh_dt:
                next_dt = yt_next[1]
            else:
                next_dt = next_sh_dt

            # --- Also compute the LAST Motzi before this span's end ---
            last_dt = _last_motzi_before(ref_dt)

            lt = target.astimezone(self._tz)
            human = fmt_simple(lt)
            self._attr_extra_state_attributes = {
                "Zman_Motzi_With_Seconds": full_iso,
                "Zman_Motzi_Simple": human,
                "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
                "Latitude": self._geo.latitude,
                "Longitude": self._geo.longitude,
                "Next_Zman_Motzi_Date": (next_dt + timedelta(days=1)).date().isoformat(),
                "Next_Zman_Motzi_Simple": fmt_simple(ceil_minute(next_dt).astimezone(self._tz)),
                "Last_Zman_Motzi_Date": (last_dt + timedelta(days=1)).date().isoformat() if last_dt else "",
                "Last_Zman_Motzi_Simple": fmt_simple(ceil_minute(last_dt).astimezone(self._tz)) if last_dt else "",
            }
            return

        # ---------------- NOT IN YT TODAY: compute candidates (with freeze-first) ----------------
        wd = today.weekday()  # Mon=0..Sun=6; Sat=5

        chosen_unrounded: datetime.datetime | None = None
        chosen_iso: str | None = None

        # Freeze-first: if it's Saturday night and we've already passed havdalah,
        # keep *tonight's* Shabbos havdalah until 12:00 AM (no advancing yet).
        if wd == 5:
            havdalah_tonight = sunset_on(today) + timedelta(minutes=self._havdalah)
            if now >= havdalah_tonight and now < midnight_next:
                chosen_unrounded = havdalah_tonight
                chosen_iso = havdalah_tonight.isoformat()

        if chosen_unrounded is None:
            def next_saturday_future(now_date: datetime.date) -> datetime.date:
                """Return the next Saturday such that its havdalah will be >= now."""
                base = now_date if now_date.weekday() == 5 else now_date + timedelta(days=(5 - now_date.weekday()) % 7)
                base_hav = sunset_on(base) + timedelta(minutes=self._havdalah)
                return base if base_hav >= now else (base + timedelta(days=7))

            # 1) Next Shabbos havdalah (guaranteed future-facing)
            shabbos_date = next_saturday_future(today)
            shabbos_havdalah_unrounded = sunset_on(shabbos_date) + timedelta(minutes=self._havdalah)
            shabbos_havdalah_iso = shabbos_havdalah_unrounded.isoformat()

            # 2) Next Yom Tov span END havdalah (guaranteed future-facing)
            yt_start: datetime.date | None = None
            yt_end_havdalah_unrounded: datetime.datetime | None = None
            yt_end_havdalah_iso: str | None = None

            for i in range(0, 30):  # scan up to ~1 month
                d = today + timedelta(days=i)
                hd_d = HDateInfo(d, diaspora=self._diaspora)
                hd_prev = HDateInfo(d - timedelta(days=1), diaspora=self._diaspora)
                if hd_d.is_yom_tov and not hd_prev.is_yom_tov:
                    span_end = self._yt_span_end(d)  # handles Shemini Atzeres → Simchas Torah
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

        # ---------------- publish current ----------------
        target = ceil_minute(chosen_unrounded)
        self._attr_native_value = target.astimezone(timezone.utc)

        lt = target.astimezone(self._tz)
        human = fmt_simple(lt)

        # --------- also compute the NEXT after the chosen ----------
        ref_dt = chosen_unrounded

        # next Shabbos after chosen
        def next_saturday_future_from(ref_dt: datetime.datetime) -> tuple[datetime.date, datetime.datetime]:
            base_date = ref_dt.date()
            wd2 = base_date.weekday()
            sat = base_date if wd2 == 5 else base_date + timedelta(days=(5 - wd2) % 7)
            cand = sunset_on(sat) + timedelta(minutes=self._havdalah)
            if cand <= ref_dt:
                sat = sat + timedelta(days=7)
                cand = sunset_on(sat) + timedelta(minutes=self._havdalah)
            return sat, cand

        # next YT end after chosen
        def next_yt_end_after(ref_dt: datetime.datetime) -> tuple[datetime.date, datetime.datetime] | None:
            start_scan = ref_dt.date() + timedelta(days=1)
            for i in range(0, 30):
                d2 = start_scan + timedelta(days=i)
                if HDateInfo(d2, diaspora=self._diaspora).is_yom_tov and not HDateInfo(d2 - timedelta(days=1), diaspora=self._diaspora).is_yom_tov:
                    span_end2 = self._yt_span_end(d2)
                    end_dt2 = sunset_on(span_end2) + timedelta(minutes=self._havdalah)
                    if end_dt2 > ref_dt:
                        return span_end2, end_dt2
            return None

        _, next_sh_dt = next_saturday_future_from(ref_dt)
        yt_next2 = next_yt_end_after(ref_dt)
        if yt_next2 is not None and yt_next2[1] < next_sh_dt:
            next_dt = yt_next2[1]
        else:
            next_dt = next_sh_dt

        # --------- also compute the LAST before the chosen ----------
        last_dt = _last_motzi_before(ref_dt)

        self._attr_extra_state_attributes = {
            "Zman_Motzi_With_Seconds": chosen_iso,  # unrounded
            "Zman_Motzi_Simple": human,             # rounded HH:MM
            "City": self.hass.data[DOMAIN]["config"]["city"].replace("Town of ", ""),
            "Latitude": self._geo.latitude,
            "Longitude": self._geo.longitude,
            "Next_Zman_Motzi_Date": (next_dt + timedelta(days=1)).date().isoformat(),
            "Next_Zman_Motzi_Simple": fmt_simple(ceil_minute(next_dt).astimezone(self._tz)),
            "Last_Zman_Motzi_Date": (last_dt + timedelta(days=1)).date().isoformat() if last_dt else "",
            "Last_Zman_Motzi_Simple": fmt_simple(ceil_minute(last_dt).astimezone(self._tz)) if last_dt else "",
        }
