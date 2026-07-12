"""
custom_components/yidcal/kiddush_levana_sensors.py

Kiddush-Levana sensors, all grouped under the YidCal — Special Sensors
device:

  sensor.yidcal_sof_zman_kiddush_levana      Timestamp of סוף זמן קידוש לבנה
                                             (Rema half-synodic-month
                                             deadline) for the current lunar
                                             cycle, full precision (seconds
                                             incl. chalakim).
  binary_sensor.yidcal_kiddush_levana        ON continuously from the
                                             configured start (ג׳ or ז׳
                                             שלימים, per the config-flow
                                             option) until the sof zman.
                                             Attributes carry BOTH opinions
                                             as "true"/"false" strings.
  sensor.yidcal_sof_kiddush_levana_display   Printed-luach Hebrew line for
                                             the deadline (ZMAN day/night
                                             rule), with the ז׳-שלמים line
                                             as an attribute.

CYCLE SELECTION / ROLLOVER: each sensor tracks ONE Hebrew month's molad
cycle. Once the sof zman passes, the sensors keep showing that (passed)
cycle until the first Alos (RAW sunrise − 72, the house definition, via
zman_compute.dawn_for_date) AFTER the deadline, then flip to the next
Hebrew month's cycle — whose molad-derived times are deterministic and
already computable even before that molad occurs.

All molad math lives in yidcal_lib/zman_compute.py (single source of
truth): gimmel_shleimim_local / zayin_shleimim_local (booklet method-C)
and sof_zman_kiddush_levana_rama_local (mean Table-3 method — settled
decision). Display strings reuse the SAME formatters the weekly luach
uses (_szkl_anchor_when / _zsh_anchor_when in luach_data.py, verified
against printed Table-3 12/12 and the printed KY booklet 5/5), so these
sensors and the printed luach can never disagree.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate, Month as PMonth

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import get_geo
from .config_flow import (
    CONF_KIDDUSH_LEVANA_START,
    DEFAULT_KIDDUSH_LEVANA_START,
)
from .yidcal_lib.zman_compute import (
    # In-package use of the private molad helpers is deliberate: the
    # Molad attribute must match sensor.yidcal_molad's convention
    # (announcement digits + DST on the molad date) exactly.
    _molad_announcement_naive,
    _molad_clock_local_dst,
    dawn_for_date,
    gimmel_shleimim_local,
    sof_zman_kiddush_levana_rama_local,
    zayin_shleimim_local,
)
from .yidcal_lib.luach_data import (
    # Same private-in-package reuse: the harness-verified weekly-luach
    # anchor formatters (Table-3 12/12, KY booklet 5/5).
    _szkl_anchor_when,
    _zsh_anchor_when,
)

_LOGGER = logging.getLogger(__name__)


def _next_hebrew_month(hy: int, hm: int) -> tuple[int, int]:
    """Next Hebrew (year, month) in pyluach numbering (1=Nissan …
    13=Adar II) — same rollover rules as
    ``YidCalHelper.get_next_numeric_month_year``: Elul→Tishrei bumps
    the year; Adar / Adar II→Nissan stays in the same pyluach year;
    leap-year Adar I→Adar II is handled by pyluach month validity."""
    if hm == 6:                      # Elul → Tishrei, next year
        return hy + 1, 7
    nm = hm + 1
    try:
        PMonth(hy, nm)               # valid within the same Hebrew year?
        return hy, nm
    except ValueError:               # Adar / Adar II → Nissan
        return hy, 1


class _KiddushLevanaBase(YidCalSpecialDevice):
    """Shared base: one molad cycle, Alos-after-sof-zman rollover."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.hass = hass

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._diaspora: bool = cfg.get("diaspora", True)
        self._start_opinion: str = cfg.get(
            CONF_KIDDUSH_LEVANA_START, DEFAULT_KIDDUSH_LEVANA_START
        )

        self._geo = None
        self._added = False
        self._attrs: dict = {}

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._added = True

        # Shared geo (same engine as the Date / Zman sensors)
        self._geo = await get_geo(self.hass)

        # Immediate first calculation
        await self._update_state()

        # Wall-clock minute scheduling (audit convention — survives
        # clock steps on the test rig). Boundary flips land on the
        # first :00 tick after the true molad-derived instant.
        self._register_listener(
            async_track_time_change(self.hass, self._schedule_update, second=0)
        )
        self._register_interval(
            self.hass, self._schedule_update, timedelta(minutes=1)
        )

    # ── Cycle math ───────────────────────────────────────────────────

    def _alos_after(self, deadline_naive: datetime) -> datetime:
        """First Alos (RAW sunrise − 72, house definition) at-or-after
        the naive-local ``deadline`` — the cycle-rollover boundary."""
        d = deadline_naive.date()
        deadline = deadline_naive.replace(tzinfo=self._tz)
        dawn = dawn_for_date(geo=self._geo, tz=self._tz, base_date=d)
        if deadline <= dawn:
            return dawn
        return dawn_for_date(
            geo=self._geo, tz=self._tz, base_date=d + timedelta(days=1)
        )

    def _cycle_for_now(self, now_local: datetime) -> tuple[int, int]:
        """Hebrew (year, month) whose molad cycle the sensors track at
        ``now_local``: the civil-date Hebrew month, advanced once past
        the Alos following that month's sof zman."""
        ph = PHebrewDate.from_pydate(now_local.date())
        hy, hm = ph.year, ph.month
        for _ in range(3):  # ≤2 steps ever needed; 3 = safety bound
            sof = sof_zman_kiddush_levana_rama_local(hy, hm, self._tz)
            if now_local < self._alos_after(sof):
                break
            hy, hm = _next_hebrew_month(hy, hm)
        return hy, hm

    def _compute(self, now_local: datetime) -> dict:
        hy, hm = self._cycle_for_now(now_local)
        # Molad in sensor.yidcal_molad's convention: announcement
        # digits + DST in effect on the molad date.
        molad = _molad_clock_local_dst(
            _molad_announcement_naive(hy, hm), self._tz
        ).replace(tzinfo=self._tz)
        gimmel_n = gimmel_shleimim_local(hy, hm, self._tz)
        zayin_n = zayin_shleimim_local(hy, hm, self._tz)
        sof_n = sof_zman_kiddush_levana_rama_local(hy, hm, self._tz)
        return {
            "hy": hy,
            "hm": hm,
            "month_name": PHebrewDate(hy, hm, 1).month_name(True),
            "molad": molad,
            "gimmel_naive": gimmel_n,
            "zayin_naive": zayin_n,
            "sof_naive": sof_n,
            "gimmel": gimmel_n.replace(tzinfo=self._tz),
            "zayin": zayin_n.replace(tzinfo=self._tz),
            "sof": sof_n.replace(tzinfo=self._tz),
        }

    # ── Entity plumbing ──────────────────────────────────────────────

    async def _update_state(self) -> None:
        if not self._geo:
            return
        now_local = dt_util.now().astimezone(self._tz)
        try:
            cyc = self._compute(now_local)
        except Exception as e:  # noqa: BLE001 — never kill the tick loop
            _LOGGER.error("Kiddush Levana update failed: %s", e)
            return
        self._recompute(cyc, now_local)
        if self._added:
            self.async_write_ha_state()

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        raise NotImplementedError

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self._attrs)


class SofZmanKiddushLevanaSensor(_KiddushLevanaBase, SensorEntity):
    """Timestamp of סוף זמן קידוש לבנה (Rema) for the current cycle."""

    _attr_name = "Sof Zman Kiddush Levunah"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:moon-full"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        slug = "sof_zman_kiddush_levana"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value: datetime | None = None

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        # Full precision (seconds incl. chalakim) — no rounding.
        self._attr_native_value = cyc["sof"]
        self._attrs = {
            "Month_Name": cyc["month_name"],
            "Molad": cyc["molad"].isoformat(),
            "Gimmel_Shleimim": cyc["gimmel"].isoformat(),
            "Zayin_Shleimim": cyc["zayin"].isoformat(),
        }


class KiddushLevanaSensor(_KiddushLevanaBase, BinarySensorEntity):
    """ON continuously from the configured start (ג׳/ז׳ שלימים) until
    the sof zman (raw deadline instant)."""

    _attr_name = "Kiddush Levunah"
    _attr_icon = "mdi:moon-waxing-crescent"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        slug = "kiddush_levana"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"binary_sensor.yidcal_{slug}"
        self._attr_is_on = False

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        start = (
            cyc["gimmel"]
            if self._start_opinion == "gimmel"
            else cyc["zayin"]
        )
        sof = cyc["sof"]
        self._attr_is_on = start <= now_local < sof
        # House convention: boolean ATTRIBUTES as strings for HA
        # state-condition matching. Each opinion is true from its own
        # start until the shared sof zman.
        self._attrs = {
            "Gimmel_Shleimim": (
                "true" if cyc["gimmel"] <= now_local < sof else "false"
            ),
            "Zayin_Shleimim": (
                "true" if cyc["zayin"] <= now_local < sof else "false"
            ),
        }


class SofKiddushLevanaDisplaySensor(_KiddushLevanaBase, SensorEntity):
    """Printed-luach Hebrew line for the deadline, ז׳ שלמים attribute."""

    _attr_name = "Sof Kiddush Levunah Display"
    _attr_icon = "mdi:moon-waning-crescent"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass)
        slug = "sof_kiddush_levana_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value: str | None = None

    def _recompute(self, cyc: dict, now_local: datetime) -> None:
        self._attr_native_value = "ס״ז קידוש לבנה: " + _szkl_anchor_when(
            cyc["sof_naive"],
            geo=self._geo,
            tz=self._tz,
            diaspora=self._diaspora,
        )
        self._attrs = {
            "Zayin_Shleimim": "ז׳ שלמים: " + _zsh_anchor_when(
                cyc["zayin_naive"],
                geo=self._geo,
                tz=self._tz,
                diaspora=self._diaspora,
            ),
        }
