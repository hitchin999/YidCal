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

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo
from .yidcal_lib.zman_compute import (
    compute_zmanim_for_date,
    compute_chametz_zmanim,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)
from .yidcal_lib.zman_day_label import compute_day_label
from .yidcal_lib.zman_erev_motzi import compute_erev_motzi

from pyluach.hebrewcal import HebrewDate as PHebrewDate


# Key under hass.data[DOMAIN] for stashing the live sensor instance so
# the service registration can reach it.
SENSOR_REF_KEY = "_zmanim_lookup_sensor_ref"


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

    def _compute_erev_motzi_attrs(
        self,
        target: date_cls,
        *,
        geo=None,
        tz=None,
    ) -> dict[str, str]:
        """Return formatted ``{label: time}`` Erev/Motzi attrs for ``target``.

        Thin wrapper over the shared ``compute_erev_motzi`` helper —
        formats datetimes as display strings and applies the sensor's
        location/tz overrides for the ``location`` service parameter.
        """
        eff_geo = geo if geo is not None else self._geo
        eff_tz = tz if tz is not None else self._tz
        if eff_geo is None:
            return {}

        em = compute_erev_motzi(
            target,
            diaspora=self._diaspora,
            geo=eff_geo,
            tz=eff_tz,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )
        fmt = self._get_time_format()
        return {label: self._format_simple_time(dt, fmt) for label, dt in em.items()}

    # ── Attribute assembly ──────────────────────────────────────────────

    def _compute_chametz_attrs(
        self,
        target: date_cls,
        *,
        geo=None,
        tz=None,
    ) -> dict[str, str]:
        """Return Sof Zman Achilas/Sriefes Chametz attrs for ``target``
        if it is Nisan 14 (Erev Pesach). Empty dict otherwise.

        ``geo`` / ``tz`` default to the sensor's configured values.
        """
        ph = PHebrewDate.from_pydate(target)
        if not (ph.month == 1 and ph.day == 14):
            return {}
        eff_geo = geo if geo is not None else self._geo
        eff_tz = tz if tz is not None else self._tz
        if eff_geo is None:
            return {}
        achilas, sriefes = compute_chametz_zmanim(
            geo=eff_geo,
            tz=eff_tz,
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
        geo=None,
        tz=None,
    ) -> tuple[str, dict[str, str]]:
        """Build the attribute dict for a single date.

        Returns ``(day_label, attrs)``. Keys are suffixed with ``suffix``
        (empty for Day 1, ``"_2"`` etc. for additional days). When
        ``include_label`` is True, a ``Label{suffix}`` key is added — used
        for dates 2+ since the sensor's state only shows date 1's label.

        ``geo`` / ``tz`` default to the sensor's configured values; pass
        non-None values to compute for an alternate location.
        """
        eff_geo = geo if geo is not None else self._geo
        eff_tz = tz if tz is not None else self._tz
        if eff_geo is None:
            return ("", {})

        day_label = compute_day_label(
            target,
            diaspora=self._diaspora,
            metzora_display=self._metzora_display,
            include_year=True,
        )

        items = compute_zmanim_for_date(
            geo=eff_geo,
            tz=eff_tz,
            base_date=target,
            tallis_offset=self._tallis,
            havdalah_offset=self._havdalah,
        )
        fmt = self._get_time_format()
        erev_motzi = self._compute_erev_motzi_attrs(target, geo=eff_geo, tz=eff_tz)
        chametz = self._compute_chametz_attrs(target, geo=eff_geo, tz=eff_tz)

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

    async def async_lookup_dates(self, targets: list[date_cls], *, resolved=None) -> None:
        """Compute and publish state + attrs for 1–N Gregorian dates.

        The sensor's state is the Hebrew label of the FIRST date. Its
        zmanim appear as un-suffixed attributes (backward-compatible
        with single-date lookups). Additional dates (2..N) appear with
        numeric suffixes ``_2``, ``_3``, etc., plus a ``Label_N`` key
        so the user can tell which date each section belongs to.

        ``resolved`` is an optional ``ResolvedLocation`` (from
        ``zman_geocoder.resolve_location``) that overrides the sensor's
        configured location. When provided, all zmanim are computed for
        that location's lat/lon/timezone, and a ``Location`` attribute
        showing the resolved display name is included.
        """
        if self._geo is None:
            return
        if not targets:
            return

        # Resolve which geo+tz to use. When `resolved` is provided, build
        # a one-off GeoLocation for the override and use its tz; otherwise
        # fall back to the sensor's configured values (no API call).
        eff_geo = self._geo
        eff_tz = self._tz
        location_display: str | None = None
        if resolved is not None:
            from zmanim.util.geo_location import GeoLocation
            from zoneinfo import ZoneInfo
            eff_geo = GeoLocation(
                name=resolved.display_name[:64] or "lookup",
                latitude=resolved.latitude,
                longitude=resolved.longitude,
                time_zone=resolved.tzname,
                elevation=0,
            )
            eff_tz = ZoneInfo(resolved.tzname)
            location_display = resolved.display_name

        combined: dict[str, str] = {}

        # Date 1 — no suffix (backward compat)
        primary_label, day1_attrs = self._build_day_attrs(
            targets[0], suffix="", include_label=False, geo=eff_geo, tz=eff_tz,
        )

        # Inject the Location attribute right after Lookup_Date so it
        # renders near the top. Only present when the user supplied an
        # override location for this call.
        if location_display:
            ordered: dict[str, str] = {}
            for k, v in day1_attrs.items():
                ordered[k] = v
                if k == "Lookup_Date":
                    ordered["Location"] = location_display
            day1_attrs = ordered

        combined.update(day1_attrs)

        # Dates 2..N — suffixed; include Label_N
        for i, target in enumerate(targets[1:], start=2):
            _lbl, attrs = self._build_day_attrs(
                target, suffix=f"_{i}", include_label=True, geo=eff_geo, tz=eff_tz,
            )
            combined.update(attrs)

        self._state = primary_label
        self._attributes = combined
        self.async_write_ha_state()
