# custom_components/yidcal/parsha_sensor.py
from __future__ import annotations
from datetime import date, timedelta
from .device import YidCalDevice
from .const import DOMAIN

from homeassistant.components.sensor import SensorEntity
from pyluach import dates, parshios

from datetime import timedelta as _timedelta  # to distinguish from pyluach.timedelta


class ParshaSensor(YidCalDevice, SensorEntity):
    """Offline Parsha sensor using pyluach for weekly readings."""

    _attr_name = "Parsha"
    _attr_icon = "mdi:book-open-page-variant"

    def __init__(self, hass) -> None:
        super().__init__()
        slug = "parsha"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._state: str | None = None
        self._last_calculated_date: date | None = None
        # Respect integration setting (default diaspora=True if missing)
        cfg = getattr(hass.data.get(DOMAIN, {}), "get", lambda *_: {})("config") if hasattr(hass.data.get(DOMAIN, {}), "get") else hass.data.get(DOMAIN, {}).get("config", {})
        self._diaspora: bool = (cfg.get("diaspora", True) if isinstance(cfg, dict) else True)
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        """Called when Home Assistant has fully started this entity."""
        await super().async_added_to_hass()
        # Do an initial state calculation immediately:
        await self._update_state()

        # Then schedule a callback every minute so that any manual time jump is detected
        self._register_interval(
            self.hass,
            self._handle_minute_tick,
            _timedelta(minutes=1),
        )

    async def _handle_minute_tick(self, now) -> None:
        """
        Every minute, check if the calendar date has changed from the last time
        we ran. If so, recalculate Parsha. This guarantees that if you manually
        jump the system clock, within 60 seconds the sensor will update.
        """
        today = date.today()
        # If we haven't calculated today yet, or if the date rolled over, update.
        if self._last_calculated_date != today:
            await self._update_state()

    @property
    def state(self) -> str:
        return self._state or ""

    def _has_regular_mon_thu(self, shabbat: date) -> bool:
        """Check if there's a regular Mon or Thu this week (Sun–Shabbat) that
        falls before any Yom Tov / Chol HaMoed — i.e. a day with a normal
        parsha-based kriah."""
        # Walk Sun(–1d from Mon) through Fri of this week
        week_start = shabbat - timedelta(days=6)  # Sunday
        for i in range(6):  # Sun=0 .. Fri=5
            d = week_start + timedelta(days=i)
            if d.weekday() not in (0, 3):  # Mon=0, Thu=3
                continue
            hd = dates.GregorianDate(d.year, d.month, d.day).to_heb()
            if self._is_during_yom_tov(hd):
                continue
            # This Mon or Thu is a regular weekday → parsha-based kriah
            return True
        return False

    def _is_during_yom_tov(self, hd) -> bool:
        """Return True if the Hebrew date falls during a Yom Tov or
        Chol HaMoed period (when there's no regular parsha-based kriah)."""
        m, d = hd.month, hd.day
        last_pesach = 22 if self._diaspora else 21
        last_sukkos = 23 if self._diaspora else 22
        last_shavuos = 7 if self._diaspora else 6
        # Pesach
        if m == 1 and 15 <= d <= last_pesach:
            return True
        # Shavuos
        if m == 3 and 6 <= d <= last_shavuos:
            return True
        # Tishrei holidays (RH, YK, Sukkos, Shmini Atzeres, Simchas Torah)
        if m == 7 and (d in (1, 2, 10) or 15 <= d <= last_sukkos):
            return True
        return False

    def _is_during_regel(self, hd) -> bool:
        """Return True if the Hebrew date falls during one of the three
        regalim (Pesach, Shavuos, Sukkos) including Chol HaMoed and
        Yom Tov Sheni — periods where the parsha name is suspended."""
        m, d = hd.month, hd.day
        last_pesach = 22 if self._diaspora else 21
        last_sukkos = 23 if self._diaspora else 22
        last_shavuos = 7 if self._diaspora else 6
        if m == 1 and 15 <= d <= last_pesach:
            return True
        if m == 3 and 6 <= d <= last_shavuos:
            return True
        if m == 7 and 15 <= d <= last_sukkos:
            return True
        return False

    async def _update_state(self) -> None:
        """Recompute which Parsha applies based on the upcoming Shabbat."""
        today = date.today()
        self._last_calculated_date = today

        # During the three regalim the week is identified by the holiday,
        # not by a parsha — show empty.
        hd_today = dates.GregorianDate(today.year, today.month, today.day).to_heb()
        if self._is_during_regel(hd_today):
            self._state = ""
            self._attr_extra_state_attributes = {
                "Next_Shabbos_Date": (today + timedelta(days=(5 - today.weekday()) % 7)).isoformat(),
                "Diaspora": self._diaspora,
            }
            self.async_write_ha_state()
            return

        # Find the next Saturday (weekday==5)
        offset = (5 - today.weekday()) % 7
        shabbat = today + timedelta(days=offset)

        # Use pyluach to get that week's Parsha
        greg = dates.GregorianDate(shabbat.year, shabbat.month, shabbat.day)
        # pyluach uses israel=True/False (inverse of diaspora)
        parsha_indices = parshios.getparsha(greg, israel=not self._diaspora)

        if parsha_indices:
            heb = parshios.getparsha_string(greg, israel=not self._diaspora, hebrew=True) or ""
            # Join double parshiyos with a hyphen for your card formatting
            combined = heb.replace(", ", "-").strip()

            # Check if this follows an א׳ week: previous Shabbos had no parsha
            # AND that week had a regular Mon/Thu (i.e. it was an א׳ week).
            suffix = ""
            prev_shabbat = shabbat - timedelta(days=7)
            prev_greg = dates.GregorianDate(prev_shabbat.year, prev_shabbat.month, prev_shabbat.day)
            prev_indices = parshios.getparsha(prev_greg, israel=not self._diaspora)
            if not prev_indices:
                prev_hd = prev_greg.to_heb()
                # Only add ב׳ if it wasn't Tishrei and the prev week had a regular Mon/Thu
                if not (prev_hd.month == 7 and prev_hd.day >= 15):
                    if self._has_regular_mon_thu(prev_shabbat):
                        suffix = " ב׳"

            self._state = f"פרשת {combined}{suffix}" if combined else ""
        else:
            # Upcoming Shabbos has no parsha (Yom Tov).
            # Only show a parsha with א׳ if there's a regular Mon/Thu
            # this week with a parsha-based kriah (before Yom Tov starts).
            # Sukkot/Tishrei → always empty.
            hd_shabbat = greg.to_heb()
            if hd_shabbat.month == 7 and hd_shabbat.day >= 15:
                # Sukkot area — no parsha association
                self._state = ""
            elif self._has_regular_mon_thu(shabbat):
                # There's a Mon/Thu with a regular kriah → find next parsha
                scan = shabbat + timedelta(days=7)
                found = False
                for _ in range(4):
                    scan_greg = dates.GregorianDate(scan.year, scan.month, scan.day)
                    scan_indices = parshios.getparsha(scan_greg, israel=not self._diaspora)
                    if scan_indices:
                        heb = parshios.getparsha_string(scan_greg, israel=not self._diaspora, hebrew=True) or ""
                        combined = heb.replace(", ", "-").strip()
                        self._state = f"פרשת {combined} א׳" if combined else ""
                        found = True
                        break
                    scan += timedelta(days=7)
                if not found:
                    self._state = ""
            else:
                # All Mon/Thu are during Yom Tov — no parsha
                self._state = ""

        # A couple of helpful attributes
        self._attr_extra_state_attributes = {
            "Next_Shabbos_Date": shabbat.isoformat(),
            "Diaspora": self._diaspora,
        }

        # Write to Home Assistant
        self.async_write_ha_state()
