# custom_components/yidcal/early_shabbos_yt_start_time_sensor.py
from __future__ import annotations

import logging
from datetime import datetime, date, timedelta, time as dtime, timezone
from zoneinfo import ZoneInfo

import homeassistant.util.dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_time_change,
    async_track_state_change_event,
)

from hdate import HDateInfo
from zmanim.zmanim_calendar import ZmanimCalendar

from .const import DOMAIN
from .device import YidCalEarlyDevice
from .zman_sensors import get_geo
from .config_flow import (
    # Early Shabbos
    CONF_ENABLE_EARLY_SHABBOS,
    CONF_EARLY_SHABBOS_MODE,
    CONF_EARLY_SHABBOS_PLAG_METHOD,
    CONF_EARLY_SHABBOS_FIXED_TIME,
    CONF_EARLY_SHABBOS_APPLY_RULE,
    CONF_EARLY_SHABBOS_SUNSET_AFTER,
    DEFAULT_ENABLE_EARLY_SHABBOS,
    DEFAULT_EARLY_SHABBOS_MODE,
    DEFAULT_EARLY_SHABBOS_PLAG_METHOD,
    DEFAULT_EARLY_SHABBOS_FIXED_TIME,
    DEFAULT_EARLY_SHABBOS_APPLY_RULE,
    DEFAULT_EARLY_SHABBOS_SUNSET_AFTER,
    # Early Yom Tov
    CONF_ENABLE_EARLY_YOMTOV,
    CONF_EARLY_YOMTOV_MODE,
    CONF_EARLY_YOMTOV_PLAG_METHOD,
    CONF_EARLY_YOMTOV_FIXED_TIME,
    CONF_EARLY_YOMTOV_INCLUDE,
    DEFAULT_ENABLE_EARLY_YOMTOV,
    DEFAULT_EARLY_YOMTOV_MODE,
    DEFAULT_EARLY_YOMTOV_PLAG_METHOD,
    DEFAULT_EARLY_YOMTOV_FIXED_TIME,
    DEFAULT_EARLY_YOMTOV_INCLUDE,
)

_LOGGER = logging.getLogger(__name__)


def _round_half_up(dt: datetime) -> datetime:
    """Round dt to nearest minute: <30s floor, ≥30s ceil."""
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


class EarlyShabbosYtStartTimeSensor(YidCalEarlyDevice, SensorEntity):
    """Next effective start time for Shabbos/Yom Tov (supports early modes).

    • State: TIMESTAMP (UTC) of the next effective entry point
      (early Shabbos / early Yom Tov / regular candle-lighting).
    • Attributes:
        - raw_*_start_by_date           → configured early candidates
        - effective_*_start_by_date     → only dates where early actually applies
        - regular_*_start_by_date       → baseline "regular" Zman Erev times
        - next_effective_start_*        → Plag-style summary
    """

    _attr_name = "Early Shabbos/Yom Tov Start Time"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    # override strings
    OVERRIDE_AUTO = "auto"
    OVERRIDE_FORCE_EARLY = "force_early"
    OVERRIDE_FORCE_REGULAR = "force_regular"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        slug = "early_shabbos_yt_start_time"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._entry = entry
        self._geo = None
        cfg = self._get_cfg()
        self._tz = ZoneInfo(cfg.get("tzname", self.hass.config.time_zone))

        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        self._geo = await get_geo(self.hass)
        await self.async_update()

        # minute beat
        self._register_listener(
            async_track_time_change(self.hass, self.async_update, second=0)
        )

        # when we later add override selects, this will auto-refresh safely
        @callback
        def _on_override_change(_event) -> None:
            # just schedule a state update; HA will call async_update
            self.async_schedule_update_ha_state(True)

        self._register_listener(
            async_track_state_change_event(
                self.hass,
                [
                    "select.yidcal_early_shabbos_override",
                    "select.yidcal_early_yomtov_override",
                ],
                _on_override_change,
            )
        )

    @property
    def available(self) -> bool:
        """Only available if at least one early feature is enabled."""
        if not self._geo:
            return False

        cfg = self._get_cfg()
        enable_es = cfg.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS)
        enable_ey = cfg.get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV)
        return bool(enable_es or enable_ey)

    # ---------------- helpers ----------------

    def _parse_time(self, s: str, default: str = "19:00:00") -> dtime:
        try:
            parts = (s or default).split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sec = int(parts[2]) if len(parts) > 2 else 0
            return dtime(hour=h, minute=m, second=sec)
        except Exception:
            parts = default.split(":")
            return dtime(int(parts[0]), int(parts[1]), int(parts[2]))

    def _get_cfg(self) -> dict:
        base = self.hass.data.get(DOMAIN, {}).get("config", {}) or {}
        data = getattr(self._entry, "data", None) or {}
        opts = getattr(self._entry, "options", None) or {}
        return {**base, **data, **opts}  # options win

    def _get_override(self, key: str) -> str:
        overrides = (
            self.hass.data.get(DOMAIN, {})
            .get("runtime", {})
            .get("early_overrides", {})
        )
        v = overrides.get(key, self.OVERRIDE_AUTO)
        if v in (self.OVERRIDE_AUTO, self.OVERRIDE_FORCE_EARLY, self.OVERRIDE_FORCE_REGULAR):
            return v
        return self.OVERRIDE_AUTO

    def _plag_from_calendar(self, cal: ZmanimCalendar, method: str):
        # Try multiple method names to stay compatible with different zmanim libs
        if method == "ma":
            names = ["plag_hamincha_mga", "plagHaminchaMGA", "plag_hamincha_ma", "plagHaminchaMa"]
        else:
            names = ["plag_hamincha_gra", "plagHaminchaGRA", "plag_hamincha", "plagHamincha", "plagHaminchaGra"]

        for name in names:
            fn = getattr(cal, name, None)
            if callable(fn):
                try:
                    return fn().astimezone(self._tz)
                except Exception:
                    continue
        return None

    def _compute_early_dt(self, d: date, mode: str, plag_method: str, fixed_time_str: str):
        if not self._geo:
            return None

        cal = ZmanimCalendar(geo_location=self._geo, date=d)

        # "disabled" in your UI really means "manual only":
        # still compute a PLAG candidate so force_early can use it.
        if mode in ("disabled", "plag"):
            return self._plag_from_calendar(cal, plag_method)

        if mode == "fixed":
            t = self._parse_time(fixed_time_str)
            return datetime.combine(d, t, tzinfo=self._tz)

        return None

    # ---------------- main update ----------------

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        cfg = self._get_cfg()
        candle_offset = cfg.get("candlelighting_offset", cfg.get("candle", 15))
        diaspora = cfg.get("diaspora", True)

        now_local = (now or dt_util.now()).astimezone(self._tz)
        today = now_local.date()
        wd = today.weekday()

        # ---------- EARLY SHABBOS (next 2 Fridays) ----------
        enable_es = cfg.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS)
        es_mode = cfg.get(CONF_EARLY_SHABBOS_MODE, DEFAULT_EARLY_SHABBOS_MODE)
        es_plag_method = cfg.get(CONF_EARLY_SHABBOS_PLAG_METHOD, DEFAULT_EARLY_SHABBOS_PLAG_METHOD)
        es_fixed = cfg.get(CONF_EARLY_SHABBOS_FIXED_TIME, DEFAULT_EARLY_SHABBOS_FIXED_TIME)
        es_apply = cfg.get(CONF_EARLY_SHABBOS_APPLY_RULE, DEFAULT_EARLY_SHABBOS_APPLY_RULE)
        es_sunset_after = cfg.get(CONF_EARLY_SHABBOS_SUNSET_AFTER, DEFAULT_EARLY_SHABBOS_SUNSET_AFTER)
        es_override = self._get_override("early_shabbos")

        effective_shabbos_by_date: dict[str, str] = {}
        raw_shabbos_by_date: dict[str, str] = {}
        regular_shabbos_by_date: dict[str, str] = {}

        for week in (0, 1):
            days_until_fri = (4 - wd) % 7
            fri = today + timedelta(days=days_until_fri + 7 * week)

            cal_fri = ZmanimCalendar(geo_location=self._geo, date=fri)
            fri_sunset = cal_fri.sunset().astimezone(self._tz)

            # Regular Zman Erev for that Friday
            regular_start = _round_half_up(fri_sunset - timedelta(minutes=candle_offset))

            early_dt = None
            auto_applicable = False

            if enable_es:
                early_dt = self._compute_early_dt(fri, es_mode, es_plag_method, es_fixed)

                if es_apply == "every_friday":
                    auto_applicable = True
                elif es_apply == "sunset_after":
                    thresh_t = self._parse_time(es_sunset_after)
                    thresh_dt = datetime.combine(fri, thresh_t, tzinfo=self._tz)
                    auto_applicable = fri_sunset > thresh_dt

            raw_key = fri.isoformat()

            # baseline regular map
            regular_shabbos_by_date[raw_key] = regular_start.isoformat()

            # raw early candidate map (if configured)
            if early_dt:
                raw_shabbos_by_date[raw_key] = early_dt.isoformat()

            # Decide effective
            effective_dt = None
            if enable_es and early_dt:
                if es_override == self.OVERRIDE_FORCE_REGULAR:
                    effective_dt = None
                elif es_override == self.OVERRIDE_FORCE_EARLY:
                    effective_dt = early_dt
                else:  # auto
                    if es_mode != "disabled" and auto_applicable and early_dt < regular_start:
                        effective_dt = early_dt

            if effective_dt:
                effective_shabbos_by_date[raw_key] = effective_dt.isoformat()

        # ---------- EARLY YOM TOV (next upcoming YT only) ----------
        enable_ey = cfg.get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV)
        ey_mode = cfg.get(CONF_EARLY_YOMTOV_MODE, DEFAULT_EARLY_YOMTOV_MODE)
        ey_plag_method = cfg.get(CONF_EARLY_YOMTOV_PLAG_METHOD, DEFAULT_EARLY_YOMTOV_PLAG_METHOD)
        ey_fixed = cfg.get(CONF_EARLY_YOMTOV_FIXED_TIME, DEFAULT_EARLY_YOMTOV_FIXED_TIME)
        ey_include = cfg.get(CONF_EARLY_YOMTOV_INCLUDE, DEFAULT_EARLY_YOMTOV_INCLUDE) or []
        ey_override = self._get_override("early_yomtov")

        effective_yomtov_by_date: dict[str, str] = {}
        raw_yomtov_by_date: dict[str, str] = {}
        regular_yomtov_by_date: dict[str, str] = {}
        next_yt_name = None

        first_yt_day: date | None = None
        for i in range(0, 60):
            d = today + timedelta(days=i)
            hd = HDateInfo(d, diaspora=diaspora)
            if hd.is_yom_tov and not HDateInfo(d - timedelta(days=1), diaspora=diaspora).is_yom_tov:
                first_yt_day = d
                next_yt_name = str(hd.holidays[0]) if hd.holidays else "Yom Tov"
                break

        if enable_ey and first_yt_day:
            erev = first_yt_day - timedelta(days=1)

            cal_erev = ZmanimCalendar(geo_location=self._geo, date=erev)
            erev_sunset = cal_erev.sunset().astimezone(self._tz)

            # Regular Erev YT (like Zman Erev before sunset)
            regular_yt_start = _round_half_up(erev_sunset - timedelta(minutes=candle_offset))
            regular_yomtov_by_date[erev.isoformat()] = regular_yt_start.isoformat()

            early_yt_dt = self._compute_early_dt(erev, ey_mode, ey_plag_method, ey_fixed)
            if early_yt_dt:
                raw_yomtov_by_date[erev.isoformat()] = early_yt_dt.isoformat()

            effective_yt_dt = None
            if early_yt_dt:
                if ey_override == self.OVERRIDE_FORCE_REGULAR:
                    effective_yt_dt = None
                elif ey_override == self.OVERRIDE_FORCE_EARLY:
                    effective_yt_dt = early_yt_dt
                else:  # auto
                    if ey_mode != "disabled" and early_yt_dt < regular_yt_start:
                        effective_yt_dt = early_yt_dt

            if effective_yt_dt:
                effective_yomtov_by_date[erev.isoformat()] = effective_yt_dt.isoformat()

        # ---------- Pick the "next effective start" for STATE ----------

        soonest_dt: datetime | None = None
        soonest_kind: str | None = None   # shabbos_early, shabbos_regular, yomtov_early, yomtov_regular

        def consider_iso(iso_str: str, kind: str) -> None:
            nonlocal soonest_dt, soonest_kind
            try:
                dt_local = datetime.fromisoformat(iso_str).astimezone(self._tz)
            except Exception:
                return
            if dt_local < now_local:
                return
            if soonest_dt is None or dt_local < soonest_dt:
                soonest_dt = dt_local
                soonest_kind = kind

        # 1) Prefer any early entries first
        for iso in effective_shabbos_by_date.values():
            consider_iso(iso, "shabbos_early")
        for iso in effective_yomtov_by_date.values():
            consider_iso(iso, "yomtov_early")

        # 2) If no early at all, fall back to regular baseline
        if soonest_dt is None:
            for iso in regular_shabbos_by_date.values():
                consider_iso(iso, "shabbos_regular")
            for iso in regular_yomtov_by_date.values():
                consider_iso(iso, "yomtov_regular")

        # 3) Publish state + "Plag style" attributes
        if soonest_dt is not None:
            # state must be UTC
            self._attr_native_value = soonest_dt.astimezone(timezone.utc)
            simple = self._format_simple_time(soonest_dt)
            with_seconds = soonest_dt.isoformat()
        else:
            self._attr_native_value = None
            simple = ""
            with_seconds = ""

        kind_desc_map = {
            "shabbos_early": "Early Shabbos",
            "shabbos_regular": "Shabbos (regular candle-lighting)",
            "yomtov_early": "Early Yom Tov",
            "yomtov_regular": "Yom Tov (regular candle-lighting)",
        }
        desc = kind_desc_map.get(soonest_kind or "", "")

        summary = ""
        if soonest_dt is not None and desc:
            summary = f"{desc} at {simple}"

        self._attr_extra_state_attributes = {
            # REQUIRED for No Melucha global logic:
            "effective_shabbos_start_by_date": effective_shabbos_by_date,
            "effective_yomtov_start_by_date": effective_yomtov_by_date,

            # Config + override info:
            "early_shabbos_override": es_override,
            "early_shabbos_mode": es_mode,
            "early_yomtov_override": ey_override,
            "early_yomtov_mode": ey_mode,
            "early_yomtov_include": ey_include,
            "next_yomtov_name": next_yt_name,

            # Plag-style summary:
            "next_effective_start_with_seconds": with_seconds,
            "next_effective_start_simple": simple,
            "next_effective_start_kind": soonest_kind or "",
            "next_effective_start_description": desc,
            "summary": summary,

            # DEBUG ONLY – uncomment if you need to see internals in Dev Tools.
            # DO NOT DELETE — used by developers for troubleshooting.
            # "raw_shabbos_start_by_date": raw_shabbos_by_date,
            # "raw_yomtov_start_by_date": raw_yomtov_by_date,
            # "regular_shabbos_start_by_date": regular_shabbos_by_date,
            # "regular_yomtov_start_by_date": regular_yomtov_by_date,
        }
