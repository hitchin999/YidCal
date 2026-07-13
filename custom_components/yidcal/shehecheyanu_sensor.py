"""
custom_components/yidcal/shehecheyanu_sensor.py

sensor.yidcal_shehecheyanu_display — is שהחיינו said at the NEXT candle
lighting? Grouped under the YidCal — Special Sensors device.

    "שהחיינו"             the lighting brings in a Yom Tov on which
                          שהחיינו IS said
    "אין אומרים שהחיינו"   it does not — either a plain Shabbos, or
                          שביעי / אחרון של פסח

ALWAYS populated. There is always a next lighting (Erev Shabbos at worst), so
the sensor never blanks; it rolls forward the moment the lighting it is
describing passes.

NOTHING here re-derives halacha. Both halves come from existing house code:

    zman_sensors.lighting_event_for_day()
        WHICH lightings exist and WHEN. A lighting on civil day ``d`` brings in
        ``d + 1``: 'erev_before_sunset' (before shkia) or the after-tzeis night
        kinds 'between_yt_after_tzeis' / 'motzaei_shabbos_after_tzeis'.

    halacha_events.is_yt_without_shehecheyanu()
        WHETHER the incoming Yom Tov is one of the two on which שהחיינו is NOT
        said — 21 Nissan, plus 22 Nissan in the diaspora. This is the very
        predicate that stamps "א״א שהחיינו" on the printed luach's Erev row, so
        the sensor and the sheet can never disagree.

Every other Yom Tov lighting is a yes — including the ones easy to miss: Erev
Yom Kippur, ליל ב׳ of ר״ה / סוכות / פסח / שבועות, and שמחת תורה. Israel mode
falls out for free: אחרון של פסח simply is not a Yom Tov there, so no lighting
is generated for it at all.

NOTE: this reads ``lighting_event_for_day`` directly, so it sees night-2/3
lightings whether or not the optional Night 2 / Night 3 candle sensors are
switched on — that option is a display preference, not a halachic one.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from hdate import HDateInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .const import DOMAIN
from .device import YidCalSpecialDevice
from .zman_sensors import (
    get_geo,
    label_for_kind_and_context,
    lighting_event_for_day,
)
from .yidcal_lib.halacha_events import (
    is_yt_without_shehecheyanu,
    major_yt_name,
)
from .yidcal_lib.zman_compute import (
    round_ceil as _ceil_minute,
    round_half_up as _half_up,
)

_LOGGER = logging.getLogger(__name__)

SAID_HE = "שהחיינו"
NOT_SAID_HE = "אין אומרים שהחיינו"

# A lighting is never more than a week away — Erev Shabbos guarantees one.
_SCAN_DAYS = 8

# After-tzeis lightings round UP (chumrah); before-sunset uses half-up. Same
# rule ZmanErevSensor.round_for_kind applies, so this sensor rolls over on
# exactly the minute sensor.yidcal_zman_erev publishes.
_AFTER_TZEIS_KINDS = ("between_yt_after_tzeis", "motzaei_shabbos_after_tzeis")


def _round_for_kind(dt_local: datetime, kind: str) -> datetime:
    return _ceil_minute(dt_local) if kind in _AFTER_TZEIS_KINDS else _half_up(dt_local)


class ShehecheyanuDisplaySensor(YidCalSpecialDevice, SensorEntity):
    """Whether שהחיינו is said at the next candle lighting."""

    _attr_should_poll = False
    _attr_name = "Shehecheyanu Display"
    _attr_icon = "mdi:candle"

    def __init__(
        self,
        hass: HomeAssistant,
        candle_offset: int,
        havdalah_offset: int,
        diaspora: bool,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._candle = candle_offset
        self._havdalah = havdalah_offset
        self._diaspora = diaspora

        cfg = hass.data.get(DOMAIN, {}).get("config", {}) or {}
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

        self._geo = None
        self._added = False
        self._attrs: dict = {}

        slug = "shehecheyanu_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_native_value: str | None = None

    def _schedule_update(self, *_args) -> None:
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._update_state())
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._added = True

        self._geo = await get_geo(self.hass)
        await self._update_state()

        # Wall-clock minute scheduling (house convention — survives clock steps).
        self._register_listener(
            async_track_time_change(self.hass, self._schedule_update, second=0)
        )
        self._register_interval(
            self.hass, self._schedule_update, timedelta(minutes=1)
        )

    # ── The lookup ───────────────────────────────────────────────────────

    def _next_lighting(self, now_local: datetime) -> dict:
        """The next candle lighting of ANY kind — Shabbos or Yom Tov — and
        whether שהחיינו is said at it."""
        for i in range(_SCAN_DAYS):
            d = now_local.date() + timedelta(days=i)
            ev, kind = lighting_event_for_day(
                d,
                diaspora=self._diaspora,
                tz=self._tz,
                geo=self._geo,
                candle_offset=self._candle,
                havdalah_offset=self._havdalah,
            )
            if ev is None or kind == "none":
                continue

            ev = _round_for_kind(ev, kind)
            if now_local >= ev:          # already lit — keep looking
                continue

            tom = d + timedelta(days=1)  # the day this lighting brings in
            is_yt = HDateInfo(tom, diaspora=self._diaspora).is_yom_tov

            if not is_yt:
                said = False                                        # plain Shabbos
            elif is_yt_without_shehecheyanu(tom, diaspora=self._diaspora):
                said = False                                        # שביעי / אחרון
            else:
                said = True

            if is_yt:
                incoming = major_yt_name(
                    PHebrewDate.from_pydate(tom), diaspora=self._diaspora
                )
            elif tom.weekday() == 5:
                incoming = "שבת קודש"
            else:                        # unreachable: lightings only bring in
                incoming = None          # Shabbos or Yom Tov

            return {
                "said": said,
                "lighting": ev,
                "lighting_date": d,
                "kind": kind,
                "label": label_for_kind_and_context(
                    d, kind, diaspora=self._diaspora
                ),
                "incoming_date": tom,
                "incoming": incoming,
                "is_yom_tov": is_yt,
                "days_until": i,
            }

        raise RuntimeError("no candle lighting found within the next week")

    # ── Entity plumbing ──────────────────────────────────────────────────

    async def _update_state(self) -> None:
        if not self._geo:
            return
        now_local = dt_util.now().astimezone(self._tz)
        try:
            ev = self._next_lighting(now_local)
        except Exception as e:  # noqa: BLE001 — never kill the tick loop
            _LOGGER.error("Shehecheyanu update failed: %s", e)
            return
        self._recompute(ev, now_local)
        if self._added:
            self.async_write_ha_state()

    def _recompute(self, ev: dict, now_local: datetime) -> None:
        self._attr_native_value = SAID_HE if ev["said"] else NOT_SAID_HE
        self._attrs = {
            # House convention: boolean ATTRIBUTES as strings, so HA state
            # conditions can match them directly.
            "Shehecheyanu": "true" if ev["said"] else "false",
            "Candle_Lighting": ev["lighting"].isoformat(),
            "Lighting_Date": ev["lighting_date"].isoformat(),
            "Lighting_Kind": ev["kind"],
            "Lighting_Label": ev["label"],
            "Incoming_Date": ev["incoming_date"].isoformat(),
            "Incoming_Day": ev["incoming"],
            "Is_Yom_Tov": "true" if ev["is_yom_tov"] else "false",
            "Days_Until": ev["days_until"],
            "Is_Today": "true" if ev["days_until"] == 0 else "false",
            "Activation_Logic": (
                "Whether שהחיינו is said at the NEXT candle lighting — Shabbos "
                "or Yom Tov, whichever comes first. Said at every Yom Tov "
                "lighting, including Erev Yom Kippur and the ליל ב׳ lightings, "
                "EXCEPT שביעי and אחרון של פסח. A plain Shabbos lighting is "
                "always 'אין אומרים שהחיינו'. Rolls to the next lighting at "
                "candle-lighting time."
            ),
        }

    @property
    def extra_state_attributes(self) -> dict:
        return dict(self._attrs)
