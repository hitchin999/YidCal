"""
custom_components/yidcal/upcoming_shabbos_zmanim_sensor.py

Sensor that exposes the zmanim for the UPCOMING Shabbos, plus a
human-friendly state:
  • On most weeks:        "לפרשת <name>"
  • Special Shabbos:      "לפרשת <name> - שבת שובה" (etc.)
  • Shabbos Chol HaMoed:  "לשבת חול המועד פסח" / "לשבת חול המועד סוכות"

Rollover: 12:00 AM Sunday (civil midnight after Motzei Shabbos).

Attributes: candle lighting (Friday before sunset), Motzi/Havdalah
(after Saturday Tzeis — extending through any adjacent YT days), and
all daily zmanim for the upcoming Saturday in chronological order
(Alos → ... → Chatzos HaLaila).
"""
from __future__ import annotations

import datetime
from datetime import timedelta, date as date_cls
from zoneinfo import ZoneInfo

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity

from pyluach import dates as pl_dates
from pyluach import parshios
from pyluach.hebrewcal import HebrewDate as PHebrewDate

from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo, _no_melacha_block
from .yidcal_lib.zman_compute import (
    compute_zmanim_for_date,
    DEFAULT_TALLIS_TEFILIN_OFFSET,
)
from .yidcal_lib.zman_erev_motzi import compute_erev_motzi
from .yidcal_lib import specials


class UpcomingShabbosZmanimSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """Zmanim for the upcoming Shabbos."""

    _attr_name = "Upcoming Shabbos Zmanim"
    _attr_icon = "mdi:calendar-clock"
    _attr_unique_id = "yidcal_upcoming_shabbos_zmanim"

    def __init__(self, hass: HomeAssistant, havdalah_offset: int) -> None:
        super().__init__()
        slug = "upcoming_shabbos_zmanim"
        self.entity_id = f"sensor.yidcal_{slug}"
        self.hass = hass

        cfg = hass.data[DOMAIN]["config"]
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._havdalah = int(cfg.get("havdalah_offset", havdalah_offset))
        self._candle = int(cfg.get("candlelighting_offset", 15))
        self._tallis = int(cfg.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET))
        self._diaspora = bool(cfg.get("diaspora", True))
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

        last = await self.async_get_last_state()
        if last:
            self._state = last.state or ""
            self._attributes = dict(last.attributes or {})

        self._geo = await get_geo(self.hass)
        await self._recompute()

        # Roll at 12:00 AM Sunday (every midnight — cheap to recompute).
        unsub = async_track_time_change(
            self.hass, self._midnight_tick, hour=0, minute=0, second=0
        )
        self._register_listener(unsub)

        # Minute safety tick — handles manual clock jumps without waiting
        # until the next midnight.
        self._register_interval(self.hass, self._minute_tick, timedelta(minutes=1))

    async def _midnight_tick(self, now: datetime.datetime) -> None:
        await self._recompute()

    async def _minute_tick(self, now: datetime.datetime) -> None:
        await self._recompute()

    # ── Block identification ────────────────────────────────────────────

    def _is_chm_shabbos(self, shabbos_date: date_cls) -> tuple[bool, str]:
        """Return (is_chm, 'פסח' or 'סוכות') for the Shabbos in question.

        Diaspora CHM windows (with first/last YT days excluded):
          • Pesach:  Nisan 17–20
          • Sukkos:  Tishrei 17–20
        Israel CHM windows:
          • Pesach:  Nisan 16–20  (only Nisan 15 is YT)
          • Sukkos:  Tishrei 16–21 (Tishrei 22 = שמיני עצרת)
        """
        ph = PHebrewDate.from_pydate(shabbos_date)
        m, d = ph.month, ph.day
        if m == 1:  # Nisan
            if self._diaspora and 17 <= d <= 20:
                return (True, "פסח")
            if (not self._diaspora) and 16 <= d <= 20:
                return (True, "פסח")
        elif m == 7:  # Tishrei
            if self._diaspora and 17 <= d <= 20:
                return (True, "סוכות")
            if (not self._diaspora) and 16 <= d <= 21:
                return (True, "סוכות")
        return (False, "")

    def _compute_parsha_label(self, shabbos_date: date_cls) -> str:
        """Return 'לפרשת <name>' for the given Shabbos, or '' if none
        (e.g. Shabbos is a Yom Tov day itself).

        Mirrors parsha_sensor.py's overrides: shorten 'אחרי מות' → 'אחרי',
        and honor the `parsha_metzora_display` config.
        """
        greg = pl_dates.GregorianDate(shabbos_date.year, shabbos_date.month, shabbos_date.day)
        indices = parshios.getparsha(greg, israel=not self._diaspora)
        if not indices:
            return ""
        heb = parshios.getparsha_string(greg, israel=not self._diaspora, hebrew=True) or ""
        combined = heb.replace(", ", "-").strip()
        if not combined:
            return ""

        # Match parsha_sensor.py overrides
        combined = combined.replace("אחרי מות", "אחרי")
        cfg = self.hass.data.get(DOMAIN, {}).get("config", {}) or {}
        if cfg.get("parsha_metzora_display") == "tahara":
            combined = combined.replace("מצורע", "טהרה")

        return f"לפרשת {combined}"

    def _compute_special_shabbos(self, shabbos_date: date_cls) -> str:
        """Return the special-shabbos string for the given Shabbos, or ''."""
        try:
            raw = specials.get_special_shabbos_name(
                today=shabbos_date, is_in_israel=not self._diaspora
            )
        except TypeError:
            raw = specials.get_special_shabbos_name(today=shabbos_date)
        return raw or ""

    def _build_state(self, shabbos_date: date_cls) -> str:
        """Build the Hebrew state string for the upcoming Shabbos."""
        is_chm, tag = self._is_chm_shabbos(shabbos_date)
        if is_chm:
            return f"לשבת חול המועד {tag}"

        parts: list[str] = []
        parsha_label = self._compute_parsha_label(shabbos_date)
        if parsha_label:
            parts.append(parsha_label)

        special = self._compute_special_shabbos(shabbos_date)
        if special:
            parts.append(special)

        return " - ".join(parts)

    # ── Main recompute ──────────────────────────────────────────────────

    async def _recompute(self) -> None:
        if not self._geo:
            return

        now_local = dt_util.now().astimezone(self._tz)
        today = now_local.date()

        # Upcoming Shabbos: if today is Saturday, that's this Shabbos
        # (freezes naturally until civil midnight Sunday).
        days_to_sat = (5 - today.weekday()) % 7
        shabbos_date = today + timedelta(days=days_to_sat)

        state = self._build_state(shabbos_date)

        items = compute_zmanim_for_date(
            geo=self._geo,
            tz=self._tz,
            base_date=shabbos_date,
            tallis_offset=self._tallis,
            havdalah_offset=self._havdalah,
        )

        # Erev (Friday's candle lighting) + Motzi (havdalah at block end —
        # block typically = just Saturday, but extends when Shabbos is
        # itself a YT day, e.g., Shavuos Day 2 on Shabbos).
        #
        # Walk back through any no-melucha block that includes Saturday
        # to find the real Erev day. (When Friday is YT — Shavuos D1
        # on Friday, etc. — block.start = Friday, so real Erev = Thursday.)
        full_block = _no_melacha_block(shabbos_date, diaspora=self._diaspora)
        erev_day = (full_block[0] if full_block else shabbos_date) - timedelta(days=1)
        erev_motzi = compute_erev_motzi(
            erev_day,
            diaspora=self._diaspora,
            geo=self._geo,
            tz=self._tz,
            candle_offset=self._candle,
            havdalah_offset=self._havdalah,
        )

        fmt = self._get_time_format()
        # Build attributes in strict explicit order:
        #   1) Shabbos_Date (reference)
        #   2) הדלקת נרות
        #   3) מוצאי שבת / מוצאי יום טוב
        #   4) Daily zmanim (chronological)
        attrs: dict[str, str] = {}
        attrs["Shabbos_Date"] = f"{shabbos_date.strftime('%a')}, {shabbos_date.isoformat()}"
        if "הדלקת נרות" in erev_motzi:
            attrs["הדלקת נרות"] = self._format_simple_time(erev_motzi["הדלקת נרות"], fmt)
        for motzi_label in ("מוצאי יום טוב", "מוצאי שבת"):
            if motzi_label in erev_motzi:
                attrs[motzi_label] = self._format_simple_time(erev_motzi[motzi_label], fmt)
        for entry in items:
            attrs[entry.label] = self._format_simple_time(entry.dt_local, fmt)

        self._state = state
        self._attributes = attrs
        self.async_write_ha_state()
