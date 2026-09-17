# motzi_holiday_sensor.py

from __future__ import annotations
import datetime
from datetime import timedelta, time, date
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_track_time_change

from pyluach.hebrewcal import HebrewDate as PHebrewDate
from hdate import HDateInfo

from zmanim.util.geo_location import GeoLocation

from .device import YidCalDevice
from .zman_sensors import get_geo
from .const import DOMAIN

# Shared zmanim primitives (cached; single source of truth) — replaces
# the local round_ceil / alos_mga_72 copies this module used to carry.
# Every alos in this module (live window end AND look-ahead attrs) is the
# coordinator-consistent MGA sunrise−72, half-up.
from .yidcal_lib import halacha_events as he
from .yidcal_lib.zman_compute import (
    round_ceil,
    round_half_up,
    sunset_for_date,
    dawn_for_date,
)


def alos_mga_72_for(geo: GeoLocation, tz: ZoneInfo, d: date) -> datetime.datetime:
    """MGA alos = sunrise − 72 minutes, rounded half-up like AlosSensor/HolidaySensor."""
    return round_half_up(dawn_for_date(geo=geo, tz=tz, base_date=d))

class MotzeiHolidaySensor(YidCalDevice, BinarySensorEntity, RestoreEntity):
    """A standalone “מוצאי <holiday>” entity.

    Whether it is on, and its window, come from halacha_events.FLAG_SPECS (the
    same spec sensor.yidcal_holiday publishes this flag from), so this entity
    and the holiday attribute cannot disagree. The rules - including the
    Shabbos cases (a last day on Friday: a Yom Tov מוצאי moves to Motzei
    Shabbos, any other is skipped) - live there, not here.
    """
    _attr_icon = "mdi:checkbox-marked-circle-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        friendly_name: str,
        unique_id: str,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._attr_name = friendly_name
        self._forced_unique_id = unique_id
        self._attr_unique_id = unique_id
        self._forced_entity_id = f"binary_sensor.{unique_id}"

        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state: bool = False
        # (start, end) of the current ON window; None when off.
        self._window: tuple[datetime.datetime, datetime.datetime] | None = None

        cfg = hass.data[DOMAIN]["config"]
        self._diaspora: bool = cfg.get("diaspora", True)
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

    @property
    def entity_id(self) -> str:
        return self._forced_entity_id

    @entity_id.setter
    def entity_id(self, value: str) -> None:
        return

    @property
    def unique_id(self) -> str:
        return self._forced_unique_id

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._state = (last.state == "on")

        self._geo = await get_geo(self.hass)
        await self.async_update()

        # Recalculate exactly at top-of-minute so rounded Motzi lines up with state flips
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._publishing(self.async_update),
                second=0,
            )
        )

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return
        tz = self._tz
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        self._window = he.evaluate_flag_specs(
            now=now, tz=tz, geo=self._geo, diaspora=self._diaspora,
            candle_offset=self._candle_offset, havdalah_offset=self._havdalah_offset,
            names=[self._attr_name],
        ).get(self._attr_name)
        self._state = self._window is not None


class MotzeiYomKippurSensor(MotzeiHolidaySensor):
    """מוצאי יום הכיפורים"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי יום הכיפורים', "yidcal_motzei_yom_kippur", candle_offset, havdalah_offset)


class MotzeiPesachSensor(MotzeiHolidaySensor):
    """מוצאי פסח"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי פסח', "yidcal_motzei_pesach", candle_offset, havdalah_offset)


class MotzeiSukkosSensor(MotzeiHolidaySensor):
    """מוצאי סוכות"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי סוכות', "yidcal_motzei_sukkos", candle_offset, havdalah_offset)


class MotzeiPesachFirstDaysSensor(MotzeiHolidaySensor):
    """מוצאי פסח ימים ראשונים"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי פסח ימים ראשונים', "yidcal_motzei_pesach_first_days", candle_offset, havdalah_offset)


class MotzeiSukkosFirstDaysSensor(MotzeiHolidaySensor):
    """מוצאי סוכות ימים ראשונים"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי סוכות ימים ראשונים', "yidcal_motzei_sukkos_first_days", candle_offset, havdalah_offset)


class MotzeiShavuosSensor(MotzeiHolidaySensor):
    """מוצאי שבועות"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי שבועות', "yidcal_motzei_shavuos", candle_offset, havdalah_offset)


class MotzeiRoshHashanaSensor(MotzeiHolidaySensor):
    """מוצאי ראש השנה"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי ראש השנה', "yidcal_motzei_rosh_hashana", candle_offset, havdalah_offset)


class MotzeiShivaUsorBTammuzSensor(MotzeiHolidaySensor):
    """מוצאי צום שבעה עשר בתמוז"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי צום שבעה עשר בתמוז', "yidcal_motzei_shiva_usor_btammuz", candle_offset, havdalah_offset)


class MotzeiChanukahSensor(MotzeiHolidaySensor):
    """מוצאי חנוכה"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי חנוכה', "yidcal_motzei_chanukah", candle_offset, havdalah_offset)


class MotzeiTishaBavSensor(MotzeiHolidaySensor):
    """מוצאי תשעה באב"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי תשעה באב', "yidcal_motzei_tisha_bav", candle_offset, havdalah_offset)


class MotzeiLagBaOmerSensor(MotzeiHolidaySensor):
    """מוצאי ל"ג בעומר"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי ל"ג בעומר', "yidcal_motzei_lag_baomer", candle_offset, havdalah_offset)


class MotzeiShushanPurimSensor(MotzeiHolidaySensor):
    """מוצאי שושן פורים"""

    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(hass, 'מוצאי שושן פורים', "yidcal_motzei_shushan_purim", candle_offset, havdalah_offset)


class MotziSensor(YidCalDevice, RestoreEntity, BinarySensorEntity):
    """True from havdalah on Shabbos or Yom Tov until Alos next day."""
    _attr_name = "Motzi"
    _attr_icon = "mdi:liquor"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        slug = "motzi"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self.hass = hass

        self._candle  = candle_offset
        self._havdalah = havdalah_offset

        cfg = hass.data[DOMAIN]["config"]
        self._tz  = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._diaspora: bool = cfg.get("diaspora", True)
        self._geo: GeoLocation | None = None
        self._state = False
        self._attr_extra_state_attributes: dict[str, bool | str] = {}
        self._next_start_cached: datetime.datetime | None = None
        self._next_end_cached:   datetime.datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            self._state = (last.state == "on")
        self._geo = await get_geo(self.hass)

        # Restore frozen "Next window" if we had one
        if last:
            try:
                ns = last.attributes.get("Next_Motzi_Window_Start")
                ne = last.attributes.get("Next_Motzi_Window_End")
                if ns and ne:
                    self._next_start_cached = datetime.datetime.fromisoformat(ns)
                    self._next_end_cached   = datetime.datetime.fromisoformat(ne)
            except Exception:
                # ignore parse errors
                pass

        await self.async_update()
        # Recalculate at HH:MM:00 each minute so Motzi flips exactly on rounded Motzi
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._publishing(self.async_update),
                second=0,
            )
        )

    @property
    def is_on(self) -> bool:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        now       = (now or datetime.datetime.now(self._tz)).astimezone(self._tz)
        today     = now.date()
        yesterday = today - timedelta(days=1)
        tomorrow  = today + timedelta(days=1)

        hd_yest  = HDateInfo(yesterday, diaspora=self._diaspora)
        hd_today = HDateInfo(today,    diaspora=self._diaspora)
        hd_tom   = HDateInfo(tomorrow, diaspora=self._diaspora)

        is_sat_today = (today.weekday() == 5)
        is_sat_yest  = (yesterday.weekday() == 5)

        # Choose the "event" we are ending: prefer Shabbos when today is Shabbos
        # (so R"H→Shabbos picks **Shabbos**, not the YT end on Friday).
        holiday_date: datetime.date | None = None
        is_holiday = False

        if is_sat_today and not hd_tom.is_yom_tov:
            # End of Shabbos tonight
            holiday_date = today
            is_holiday   = False
        elif is_sat_yest and not hd_today.is_yom_tov:
            # We just passed Motzaei Shabbos (keep it until Alos)
            holiday_date = yesterday
            is_holiday   = False
        elif hd_today.is_yom_tov and not hd_tom.is_yom_tov and not is_sat_today:
            # End of a Yom Tov today (weekday end)
            holiday_date = today
            is_holiday   = True
        elif hd_yest.is_yom_tov and not hd_today.is_yom_tov and not is_sat_today:
            # End of a Yom Tov was yesterday (weekday end)
            holiday_date = yesterday
            is_holiday   = True
        else:
            holiday_date = None
            is_holiday   = False

        if not self._geo:
            return

        # Compute candidate Motzi window (without blocking) via shared cached zmanim
        candidate_on = False
        if holiday_date:
            sunset_hol = sunset_for_date(geo=self._geo, tz=self._tz, base_date=holiday_date)
            start      = round_ceil(sunset_hol + timedelta(minutes=self._havdalah))
            motzi_end = alos_mga_72_for(self._geo, self._tz, holiday_date + timedelta(days=1))
            candidate_on = (start <= now < motzi_end)

        # Blocking rules (YT→Shabbos or Shabbos→YT)
        blocked_shabbos = is_sat_today and hd_tom.is_yom_tov
        blocked_holiday = hd_today.is_yom_tov and ((tomorrow.weekday() == 5))

        # Enforce the blocks
        self._state = candidate_on and not (blocked_shabbos or blocked_holiday)

        # ── Build attributes ──
        attrs: dict[str, bool | str] = {
            "Now": now.isoformat(),
            "Blocked_Motzi_Shabbos": str(blocked_shabbos).lower(),
            "Blocked_Motzi_Yom_Tov": str(blocked_holiday).lower(),
            "Is_Shabbos_Today":      str(is_sat_today).lower(),
            "Is_Yom_Tov_Today":      str(hd_today.is_yom_tov).lower(),
        }
        
        # ── יקנה"ז (Yaknehaz): only when Motzaei Shabbos is Yom Tov night ──
        # True from havdalah tonight until 02:00, independent of _state.
        def _is_yom_kippur(pydate: date) -> bool:
            hd = PHebrewDate.from_pydate(pydate)
            return hd.month == 7 and hd.day == 10

        yak_base: date | None = None
        # Case 1: Tonight is Shabbos → Yom Tov
        if is_sat_today and hd_tom.is_yom_tov:
            yak_base = today
        # Case 2: Last night was Shabbos → Yom Tov (it's after midnight)
        elif is_sat_yest and hd_today.is_yom_tov:
            yak_base = yesterday

        yak_active = False
        if yak_base is not None and self._geo:
            yak_start = round_ceil(
                sunset_for_date(geo=self._geo, tz=self._tz, base_date=yak_base)
                + timedelta(minutes=self._havdalah)
            )
            yak_end = datetime.datetime.combine(
                yak_base + timedelta(days=1),
                time(hour=2, minute=0),
                tzinfo=self._tz,
            )
            yak_active = yak_start <= now < yak_end
            attrs['יקנה"ז'] = str(yak_active).lower()
            # (Optional) helpful for debugging; remove if you want fewer attrs:
            attrs['Yaknehaz_Start'] = yak_start.isoformat()
            attrs['Yaknehaz_End']   = yak_end.isoformat()
        else:
            attrs['יקנה"ז'] = "false"

        # ── Next window look-ahead (YT-span aware, skip blocked, and FREEZE) ──
        cand_start = cand_end = None

        # Seed with the *current/tonight* window when appropriate.
        # - If we're already ON, stick to this window.
        # - If it starts later *today* and isn't blocked (plain Motzaei Shabbos or YT-end),
        #   expose tonight as the next window.
        if holiday_date:
            if self._state:
                cand_start, cand_end = start, motzi_end
            elif (holiday_date == today) and not (blocked_shabbos or blocked_holiday):
                cand_start, cand_end = start, motzi_end

        def sunset_on(d: datetime.date) -> datetime.datetime:
            return sunset_for_date(geo=self._geo, tz=self._tz, base_date=d)

        def alos_on(d: datetime.date) -> datetime.datetime:
            # Coordinator-consistent Alos (sunrise − 72, half-up) — the same
            # עלות השחר the dedicated alos sensor shows. Replaces the old
            # 16.1° cal.alos() here so the frozen Next_Motzi_Window_End
            # agrees with the live window end (which always used MGA-72).
            return alos_mga_72_for(self._geo, self._tz, d)

        def yt_span_end_from(start_date: datetime.date) -> datetime.date:
            """Walk forward while is_yom_tov is True; return the last YT day."""
            end = start_date
            j = 1
            while HDateInfo(start_date + timedelta(days=j), diaspora=self._diaspora).is_yom_tov:
                end = start_date + timedelta(days=j)
                j += 1
            return end

        # 1) If we're in a YT span, prefer the END of that span (unless it ends Fri→Shabbos).
        if cand_start is None and hd_today.is_yom_tov:
            span_end = yt_span_end_from(today)
            ends_into_shabbos = (span_end.weekday() == 4)  # Friday → Shabbos cluster is blocked
            if not ends_into_shabbos:
                raw_end = alos_on(span_end + timedelta(days=1))  # alos after last YT day
                if now < raw_end:
                    raw_start = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                    cand_start = round_ceil(raw_start)
                    cand_end   = raw_end

        # 2) Otherwise scan forward for earliest unblocked candidate (YT-end or plain Motzaei Shabbos).
        if cand_start is None:
            for i in range(1, 33):  # look ahead up to ~1 month
                d       = today + timedelta(days=i)
                hd_prev = HDateInfo(d - timedelta(days=1), diaspora=self._diaspora)
                hd_d    = HDateInfo(d,               diaspora=self._diaspora)
                hd_next = HDateInfo(d + timedelta(days=1), diaspora=self._diaspora)

                is_shab  = (d.weekday() == 5)
                is_hol   = hd_d.is_yom_tov
                hol_yest = hd_prev.is_yom_tov
                hol_tom  = hd_next.is_yom_tov

                # Case A: start of a YT span → consider END (skip if Fri→Shabbos)
                if is_hol and not hol_yest:
                    span_end = yt_span_end_from(d)
                    ends_into_shabbos = (span_end.weekday() == 4)
                    if not ends_into_shabbos:
                        raw_start = sunset_on(span_end) + timedelta(minutes=self._havdalah)
                        raw_end = alos_on(span_end + timedelta(days=1))  # alos after last YT day
                        cand_start = round_ceil(raw_start)
                        cand_end   = raw_end
                        break
                    # else: skip; the Sat case will cover the cluster end

                # Case B: plain Motzaei Shabbos (no YT tomorrow)
                if is_shab and not is_hol and not hol_tom:
                    raw_start = sunset_on(d) + timedelta(minutes=self._havdalah)
                    raw_end = alos_on(d + timedelta(days=1))  # alos after that Shabbos
                    cand_start = round_ceil(raw_start)
                    cand_end   = raw_end
                    break

        # ── FREEZE LOGIC ──
        # Cause-accurate flags (origin-aware)
        # - Shabbos if the *origin day* for this Motzi window was Saturday
        # - Yom Tov if we’re ending a YT span
        is_motzi_shabbos_attr = self._state and (holiday_date is not None) and (holiday_date.weekday() == 5)
        is_motzi_yom_tov_attr = self._state and is_holiday
        attrs.update({
            "Is_Motzi_Shabbos": is_motzi_shabbos_attr,
            "Is_Motzi_Yom_Tov": is_motzi_yom_tov_attr,
        })
        # Keep cached window until it ends; only replace if it finished,
        # or if a newly found candidate starts earlier (handles manual clock rewinds).
        if self._next_start_cached and self._next_end_cached and now < self._next_end_cached:
            if cand_start and cand_end and cand_start < self._next_start_cached:
                self._next_start_cached, self._next_end_cached = cand_start, cand_end
        else:
            if cand_start and cand_end:
                self._next_start_cached, self._next_end_cached = cand_start, cand_end
            else:
                self._next_start_cached = self._next_end_cached = None

        # Publish attributes from the frozen cache
        if self._next_start_cached and self._next_end_cached:
            attrs["Next_Motzi_Window_Start"] = self._next_start_cached.isoformat()
            attrs["Next_Motzi_Window_End"]   = self._next_end_cached.isoformat()
        else:
            attrs.pop("Next_Motzi_Window_Start", None)
            attrs.pop("Next_Motzi_Window_End",   None)

        self._attr_extra_state_attributes = attrs
