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
from typing import Callable, Optional

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

"""
Base class for “מוצאי <holiday>” sensors.
Subclasses must set:
  - Either provide:
      • HOLIDAY_NAME: exact Hebrew string from pyluach (legacy), OR
      • day_matcher(date, diaspora) -> bool : predicate to detect the target last-day
  - _attr_name     : the friendly name, e.g. "מוצאי יום הכיפורים"
  - _attr_unique_id: a unique_id such as "yidcal_motzei_yom_kippur"

Logic for every “motzei” sensor:
  1) If *today’s* Hebrew date == HOLIDAY_NAME, holiday_date = today.
  2) Else if *yesterday’s* Hebrew date == HOLIDAY_NAME, holiday_date = yesterday.
  (If a day_matcher was supplied, it takes precedence instead of HOLIDAY_NAME checks.)
  3) Otherwise, no motzei (OFF).
  4) If we have a holiday_date, then:
       motzei_start = sunset(holiday_date) + havdalah_offset,
       motzei_end   = (holiday_date + 1 day) at Alos.
       Sensor is ON if motzei_start ≤ now < motzei_end, UNLESS that start
       falls inside Shabbos (Fri candles → Shabbos havdalah).
"""

class MotzeiHolidaySensor(YidCalDevice, BinarySensorEntity, RestoreEntity):
    _attr_icon = "mdi:checkbox-marked-circle-outline"
    # Only Yom Tov motzeis should defer to Motzaei Shabbos in 3-day blocks.
    # Fasts, Chanukah, etc. just get blocked (no motzei shown).
    _DEFER_FOR_SHABBOS: bool = False

    def __init__(
        self,
        hass: HomeAssistant,
        holiday_name: Optional[str],
        day_matcher: Optional[Callable[[date, bool], bool]],
        friendly_name: str,
        unique_id: str,
        candle_offset: int,
        havdalah_offset: int,
    ) -> None:
        super().__init__()
        self.hass = hass
        self.HOLIDAY_NAME = holiday_name
        self._day_matcher = day_matcher

        self._attr_name = friendly_name
        self._forced_unique_id = unique_id
        self._attr_unique_id = unique_id
        self._forced_entity_id = f"binary_sensor.{unique_id}"

        self._candle_offset = candle_offset
        self._havdalah_offset = havdalah_offset
        self._state: bool = False

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

        # Load shared geo (your helper) and do an initial compute
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
        """
        Decide ON/OFF for "מוצאי <holiday>".

        1) Determine holiday_date (today/yesterday, or today-2 when
           yesterday was Shabbos — covers the 3-day YT→Shabbos scenario).
        2) motzei_start = sunset(holiday_date) + havdalah_offset
           motzei_end   = (holiday_date + 1 day) at Rounded Alos
        3) If Shabbos follows the holiday (holiday ends Friday),
           DEFER motzei to Motzaei Shabbos (Sat havdalah → Sun Alos).
        4) If the holiday’s last day IS Shabbos (e.g. Shavuos ב׳ on Shabbos,
           or 8th day of Chanukah on Shabbos), use the normal motzei window
           (Sat havdalah → Sun Alos) — that already equals מוצאי שבת.
        """
        # Lazily cache geo so ad-hoc instances (e.g. from HolidaySensor) work
        if not self._geo:
            self._geo = await get_geo(self.hass)
            if not self._geo:
                return

        tz = self._tz
        now = (now or datetime.datetime.now(tz)).astimezone(tz)
        today_date = now.date()
        yesterday = today_date - timedelta(days=1)

        # 1) Check today’s, yesterday’s, or two-days-ago’s holiday target.
        #    The today-2 check handles Sunday morning (before Alos) after a
        #    3-day YT block: holiday ended Friday → Shabbos → now Sunday.
        holiday_date: datetime.date | None = None

        def _is_target(d: date) -> bool:
            if self._day_matcher is not None:
                return self._day_matcher(d, self._diaspora)
            if self.HOLIDAY_NAME:
                return PHebrewDate.from_pydate(d).holiday(hebrew=True, prefix_day=True) == self.HOLIDAY_NAME
            return False

        if _is_target(today_date):
            holiday_date = today_date
        elif _is_target(yesterday):
            holiday_date = yesterday
        elif self._DEFER_FOR_SHABBOS and yesterday.weekday() == 5 and _is_target(today_date - timedelta(days=2)):
            # Yesterday was Shabbos and 2 days ago was the holiday’s last day (Friday)
            holiday_date = today_date - timedelta(days=2)

        if not holiday_date:
            self._state = False
            return

        # 2) Compute motzei window (holiday-based) via shared cached zmanim
        sunset_hol = sunset_for_date(geo=self._geo, tz=tz, base_date=holiday_date)
        motzei_start = round_ceil(sunset_hol + timedelta(minutes=self._havdalah_offset))

        next_day = holiday_date + timedelta(days=1)
        motzei_end = alos_mga_72_for(self._geo, tz, next_day)

        # 3) Shabbos blocking / deferral
        off_from_fri = (holiday_date.weekday() - 4) % 7  # 0 if Friday
        fri = holiday_date - timedelta(days=off_from_fri)
        sat = fri + timedelta(days=1)

        fri_sunset = sunset_for_date(geo=self._geo, tz=tz, base_date=fri)
        sat_sunset = sunset_for_date(geo=self._geo, tz=tz, base_date=sat)

        shabbos_start = fri_sunset - timedelta(minutes=self._candle_offset)
        shabbos_end   = round_ceil(sat_sunset + timedelta(minutes=self._havdalah_offset))

        shabbos_blocks_motzi = (shabbos_start <= motzei_start <= shabbos_end)

        if shabbos_blocks_motzi and self._DEFER_FOR_SHABBOS and holiday_date.weekday() == 4:
            # Holiday’s last day is Friday → Shabbos follows (3-day block).
            # Defer motzei to Motzaei Shabbos: Sat havdalah → Sun Alos.
            deferred_start = round_ceil(sat_sunset + timedelta(minutes=self._havdalah_offset))
            sun = sat + timedelta(days=1)
            deferred_end = alos_mga_72_for(self._geo, tz, sun)
            self._state = (deferred_start <= now < deferred_end)
        elif shabbos_blocks_motzi and holiday_date.weekday() == 5:
            # Holiday’s last day IS Shabbos (e.g. Shavuos ב׳ on Sat, or
            # the 8th day of Chanukah on Sat). The "normal" window
            # (sunset(holiday) + havdalah → next-day Alos) already IS
            # Sat tzeis → Sun Alos, which equals מוצאי שבת — correct.
            # Applies to every holiday, not only those with _DEFER_FOR_SHABBOS.
            self._state = (motzei_start <= now < motzei_end)
        elif shabbos_blocks_motzi:
            # Non-YT holiday blocked by Shabbos, or YT without deferral
            self._state = False
        else:
            # Normal case: no Shabbos conflict
            self._state = (motzei_start <= now < motzei_end)


#
# ─── Subclasses: each “מוצאי <holiday>”──────────────────────────────────────────
#

class MotzeiYomKippurSensor(MotzeiHolidaySensor):
    """מוצאי יום הכיפורים (ט״י תשרי)"""
    _DEFER_FOR_SHABBOS = True
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=lambda d, _dias: (lambda hd: hd.month == 7 and hd.day == 10)(PHebrewDate.from_pydate(d)),
            friendly_name="מוצאי יום הכיפורים",
            unique_id="yidcal_motzei_yom_kippur",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiPesachSensor(MotzeiHolidaySensor):
    """מוצאי פסח (ט״ו ניסן)"""
    _DEFER_FOR_SHABBOS = True
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=lambda d, dias: (lambda hd: hd.month == 1 and hd.day == (22 if dias else 21))(PHebrewDate.from_pydate(d)),
            friendly_name="מוצאי פסח",
            unique_id="yidcal_motzei_pesach",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiSukkosSensor(MotzeiHolidaySensor):
    """מוצאי סוכות (ט״ו תשרי)"""
    _DEFER_FOR_SHABBOS = True
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=lambda d, dias: (lambda hd: hd.month == 7 and hd.day == (23 if dias else 22))(PHebrewDate.from_pydate(d)),
            friendly_name="מוצאי סוכות",
            unique_id="yidcal_motzei_sukkos",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiShavuosSensor(MotzeiHolidaySensor):
    """מוצאי שבועות (ב׳ שבועות)"""
    _DEFER_FOR_SHABBOS = True
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=lambda d, dias: (lambda hd: hd.month == 3 and hd.day == (7 if dias else 6))(PHebrewDate.from_pydate(d)),
            friendly_name="מוצאי שבועות",
            unique_id="yidcal_motzei_shavuos",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiRoshHashanaSensor(MotzeiHolidaySensor):
    """מוצאי ראש השנה (ב׳ תשרי)"""
    _DEFER_FOR_SHABBOS = True
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=lambda d, _dias: (lambda hd: hd.month == 7 and hd.day == 2)(PHebrewDate.from_pydate(d)),
            friendly_name="מוצאי ראש השנה",
            unique_id="yidcal_motzei_rosh_hashana",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )


class MotzeiShivaUsorBTammuzSensor(MotzeiHolidaySensor):
    """מוצאי צום שבעה עשר בתמוז (י״ז בתמוז)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        # observed 17 Tammuz (nidcheh to 18 if 17 is Shabbos) — canonical rule
        def _matcher(d: date, _dias: bool) -> bool:
            return d == he.shiva_asar_btamuz_observed(PHebrewDate.from_pydate(d).year)
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=_matcher,
            friendly_name="מוצאי צום שבעה עשר בתמוז",
            unique_id="yidcal_motzei_shiva_usor_btammuz",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

class MotzeiChanukahSensor(MotzeiHolidaySensor):
    """מוצאי חנוכה (last day of Chanukah).

    When the 8th day falls on Shabbos, מוצאי חנוכה fires at tzeis Shabbos
    (i.e. the same moment as מוצאי שבת) — handled by the
    holiday_date.weekday()==5 branch in the base class.
    """
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        def _matcher(d: date, _dias: bool) -> bool:
            # canonical Chanukah day-counting (Kislev 29/30-safe)
            return he.chanukah_day_for_date(d) == 8

        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=_matcher,
            friendly_name="מוצאי חנוכה",
            unique_id="yidcal_motzei_chanukah",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

class MotzeiTishaBavSensor(MotzeiHolidaySensor):
    """מוצאי תשעה באב (י״ט אב)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        # observed 9 Av (nidcheh to 10 if 9 is Shabbos) — canonical rule
        def _matcher(d: date, _dias: bool) -> bool:
            return d == he.tisha_bav_observed(PHebrewDate.from_pydate(d).year)
        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=_matcher,
            friendly_name="מוצאי תשעה באב",
            unique_id="yidcal_motzei_tisha_bav",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

class MotzeiLagBaOmerSensor(MotzeiHolidaySensor):
    """מוצאי ל\"ג בעומר (י\"ח באייר)"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        def _matcher(d: date, _dias: bool) -> bool:
            hd = PHebrewDate.from_pydate(d)
            # Lag BaOmer = 18 Iyar (month 2), but only if that day is NOT Shabbos
            return hd.month == 2 and hd.day == 18 and d.weekday() != 5

        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=_matcher,
            friendly_name="מוצאי ל\"ג בעומר",
            unique_id="yidcal_motzei_lag_baomer",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

class MotzeiShushanPurimSensor(MotzeiHolidaySensor):
    """מוצאי שושן פורים (ט\"ו אדר / אדר ב')"""
    def __init__(self, hass: HomeAssistant, candle_offset: int, havdalah_offset: int) -> None:
        def _matcher(d: date, _dias: bool) -> bool:
            # Fire on 15 Adar (real Adar) unless it is Shabbos, then on
            # Sunday (Purim Meshulash) — canonical rule.
            observed = he.shushan_purim_observed(PHebrewDate.from_pydate(d).year)
            return d == observed and d.weekday() != 5

        super().__init__(
            hass,
            holiday_name=None,
            day_matcher=_matcher,
            friendly_name="מוצאי שושן פורים",
            unique_id="yidcal_motzei_shushan_purim",
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

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
