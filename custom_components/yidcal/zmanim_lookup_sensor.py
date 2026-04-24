"""
custom_components/yidcal/zmanim_lookup_sensor.py

A sensor whose state + attributes reflect the zmanim for whatever date
was most recently requested via the ``yidcal.check_zmanim`` service.

The sensor starts empty. After the service is called, its state becomes
a Hebrew label (e.g. 'לשבת פרשת פנחס', 'לפסח א׳', 'לפורים') and its
attributes contain all daily zmanim for that date plus a
``Lookup_Date`` marker.

Registered only when the user has enabled "Zmanim Lookup" in the
integration's options (reconfigure flow). A reference to the live
sensor instance is stashed in ``hass.data[DOMAIN][SENSOR_REF_KEY]`` so
the service handler (registered in ``__init__.py``) can find it.
"""
from __future__ import annotations

import datetime
from datetime import date as date_cls, timedelta
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant

from zmanim.util.geo_location import GeoLocation
from zmanim.zmanim_calendar import ZmanimCalendar
from hdate import HDateInfo

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import (
    get_geo,
    lighting_event_for_day,
    _no_melacha_block,
)
from .zman_compute import (
    compute_zmanim_for_date,
    compute_chametz_zmanim,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)
from .zman_day_label import compute_day_label

from pyluach.hebrewcal import HebrewDate as PHebrewDate


# Key under hass.data[DOMAIN] for stashing the live sensor instance so
# the service registration can reach it.
SENSOR_REF_KEY = "_zmanim_lookup_sensor_ref"


def _half_up_minute(dt):
    if dt.second >= 30:
        dt = dt + timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _ceil_minute(dt):
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


class ZmanimLookupSensor(YidCalZmanDevice, SensorEntity):
    """Holds zmanim for an ad-hoc looked-up date."""

    _attr_name = "Zmanim Lookup"
    _attr_icon = "mdi:calendar-search"
    _attr_unique_id = "yidcal_zmanim_lookup"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "zmanim_lookup"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._havdalah = int(cfg.get("havdalah_offset", havdalah_offset))
        self._candle = int(cfg.get("candlelighting_offset", 15))
        self._tallis = int(cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET))
        self._diaspora = bool(cfg.get("diaspora", True))
        self._metzora_display = cfg.get("parsha_metzora_display", "metzora")
        self._geo: GeoLocation | None = None

        self._state: str = ""
        self._attributes: dict[str, str] = {}

    @property
    def native_value(self) -> str:
        return self._state

    @property
    def extra_state_attributes(self) -> dict:
        return self._attributes

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        # Stash live reference for the service handler to reach us.
        self.hass.data.setdefault(DOMAIN, {})[SENSOR_REF_KEY] = self

    async def async_will_remove_from_hass(self) -> None:
        # Drop our reference on unload/reload.
        if self.hass.data.get(DOMAIN, {}).get(SENSOR_REF_KEY) is self:
            self.hass.data[DOMAIN].pop(SENSOR_REF_KEY, None)
        await super().async_will_remove_from_hass()

    # ── Erev / Motzi detection ──────────────────────────────────────────

    def _compute_erev_motzi_attrs(self, target: date_cls) -> dict[str, str]:
        """Return the ordered dict of Erev/Motzi attributes for `target`.

        Rules (mirrors the Zman Erev / Zman Motzi sensors):

        • ``הדלקת נרות`` — included when ``target`` has an
          ``erev_before_sunset`` candle-lighting event. This covers the
          day before Shabbos, the day before a standalone Yom Tov, and
          a Yom Tov day when the next day is Shabbos ("Shabbos as the
          2nd day"). YT-to-YT and Motzei-Shabbos-into-YT lightings (lit
          at tzeis from an existing flame) are intentionally skipped
          per the user's preference — the daily zmanim already surface
          Tzeis itself.

        • ``מוצאי שבת`` / ``מוצאי יום טוב`` — the havdalah time of the
          no-melacha *block* that ``target`` belongs to, or the block
          it is the Erev of. Uses block-end, so on a 3-day span
          (YT + YT + Shabbos, or Shabbos + YT + YT) looking up any day
          inside the block returns the final day's havdalah. The label
          follows the last day: YT → "מוצאי יום טוב" (covers YT-on-
          Shabbos too, matching the common Zman-app shortening
          "מוציו״ט"); Saturday weekday → "מוצאי שבת".
        """
        attrs: dict[str, str] = {}
        if self._geo is None:
            return attrs

        # ── 1) Candle lighting for today (only 'before-sunset' kind) ──
        event_dt, kind = lighting_event_for_day(
            target,
            diaspora=self._diaspora,
            tz=self._tz,
            geo=self._geo,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )
        if event_dt is not None and kind == "erev_before_sunset":
            fmt = self._get_time_format()
            attrs["הדלקת נרות"] = self._format_simple_time(
                _half_up_minute(event_dt), fmt
            )

        # ── 2) Motzi — relevant block end, if any ──
        # Case A: target is inside a no-melacha block.
        block = _no_melacha_block(target, diaspora=self._diaspora)

        # Case B: target is the Erev immediately before a block
        # (tomorrow is Shabbos/YT).
        if block is None:
            tomorrow = target + timedelta(days=1)
            if (
                tomorrow.weekday() == 5
                or HDateInfo(tomorrow, diaspora=self._diaspora).is_yom_tov
            ):
                block = _no_melacha_block(tomorrow, diaspora=self._diaspora)

        if block is not None:
            _start, end = block
            sunset_end = (
                ZmanimCalendar(geo_location=self._geo, date=end)
                .sunset()
                .astimezone(self._tz)
            )
            motzi_dt = _ceil_minute(sunset_end + timedelta(minutes=self._havdalah))

            # Label: YT on last day wins (including YT-on-Shabbos);
            # else it's a Shabbos end.
            last_is_yt = HDateInfo(end, diaspora=self._diaspora).is_yom_tov
            label = "מוצאי יום טוב" if last_is_yt else "מוצאי שבת"

            fmt = self._get_time_format()
            attrs[label] = self._format_simple_time(motzi_dt, fmt)

        return attrs

    # ── Attribute assembly ──────────────────────────────────────────────

    def _compute_chametz_attrs(self, target: date_cls) -> dict[str, str]:
        """Return Sof Zman Achilas/Sriefes Chametz attrs for ``target``
        if it is Nisan 14 (Erev Pesach). Empty dict otherwise.
        """
        ph = PHebrewDate.from_pydate(target)
        if not (ph.month == 1 and ph.day == 14):
            return {}
        if self._geo is None:
            return {}
        achilas, sriefes = compute_chametz_zmanim(
            geo=self._geo,
            tz=self._tz,
            base_date=target,
            havdalah_offset=self._havdalah,
        )
        fmt = self._get_time_format()
        return {
            "סוף זמן אכילת חמץ": self._format_simple_time(achilas, fmt),
            "סוף זמן שריפת חמץ": self._format_simple_time(sriefes, fmt),
        }

    def _build_day_attrs(
        self,
        target: date_cls,
        *,
        suffix: str = "",
        include_label: bool = False,
    ) -> tuple[str, dict[str, str]]:
        """Build the attribute dict for a single date.

        Returns ``(day_label, attrs)``. Keys are suffixed with ``suffix``
        (empty for Day 1, ``"_2"`` etc. for additional days). When
        ``include_label`` is True, a ``Label{suffix}`` key is added — used
        for dates 2+ since the sensor's state only shows date 1's label.
        """
        if self._geo is None:
            return ("", {})

        day_label = compute_day_label(
            target,
            diaspora=self._diaspora,
            metzora_display=self._metzora_display,
        )

        items = compute_zmanim_for_date(
            geo=self._geo,
            tz=self._tz,
            base_date=target,
            tallis_offset=self._tallis,
            havdalah_offset=self._havdalah,
        )
        fmt = self._get_time_format()
        erev_motzi = self._compute_erev_motzi_attrs(target)
        chametz = self._compute_chametz_attrs(target)

        # Build keys in a strict explicit order so the HA UI renders them
        # in this order:
        #   1) Lookup_Date{suffix}
        #   2) Label{suffix}                  (only for dates 2+)
        #   3) הדלקת נרות{suffix}             (Erev only)
        #   4) מוצאי שבת/יום טוב{suffix}       (in/before a no-melucha block)
        #   5) סוף זמן אכילת/שריפת חמץ{suffix} (Erev Pesach only)
        #   6) Daily zmanim, chronological
        attrs: dict[str, str] = {}
        attrs[f"Lookup_Date{suffix}"] = f"{target.strftime('%a')}, {target.isoformat()}"
        if include_label:
            attrs[f"Label{suffix}"] = day_label
        if "הדלקת נרות" in erev_motzi:
            attrs[f"הדלקת נרות{suffix}"] = erev_motzi["הדלקת נרות"]
        for motzi_label in ("מוצאי יום טוב", "מוצאי שבת"):
            if motzi_label in erev_motzi:
                attrs[f"{motzi_label}{suffix}"] = erev_motzi[motzi_label]
        for key, val in chametz.items():
            attrs[f"{key}{suffix}"] = val
        for entry in items:
            attrs[f"{entry.label}{suffix}"] = self._format_simple_time(
                entry.dt_local, fmt
            )
        return (day_label, attrs)

    async def async_lookup_date(self, target_date: date_cls) -> None:
        """Single-date lookup (kept as a convenience wrapper)."""
        await self.async_lookup_dates([target_date])

    async def async_lookup_dates(self, targets: list[date_cls]) -> None:
        """Compute and publish state + attrs for 1–N Gregorian dates.

        The sensor's state is the Hebrew label of the FIRST date. Its
        zmanim appear as un-suffixed attributes (backward-compatible
        with single-date lookups). Additional dates (2..N) appear with
        numeric suffixes ``_2``, ``_3``, etc., plus a ``Label_N`` key
        so the user can tell which date each section belongs to.
        """
        if self._geo is None:
            return
        if not targets:
            return

        combined: dict[str, str] = {}

        # Date 1 — no suffix (backward compat)
        primary_label, day1_attrs = self._build_day_attrs(
            targets[0], suffix="", include_label=False
        )
        combined.update(day1_attrs)

        # Dates 2..N — suffixed; include Label_N
        for i, target in enumerate(targets[1:], start=2):
            _lbl, attrs = self._build_day_attrs(
                target, suffix=f"_{i}", include_label=True
            )
            combined.update(attrs)

        self._state = primary_label
        self._attributes = combined
        self.async_write_ha_state()
