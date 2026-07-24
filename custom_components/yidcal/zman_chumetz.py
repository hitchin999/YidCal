from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from pyluach import dates as pl_dates
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .yidcal_lib.zman_compute import (
    compute_chametz_zmanim,
    sun_events_for_date,
)
from .zman_sensors import get_geo


def _get_pesach_info(today_date: date) -> tuple[int, bool]:
    """Return (Hebrew year, is_deferred) for the upcoming Pesach.

    is_deferred is True when 14 Nisan falls on Shabbos (15 Nisan = Sunday).
    """
    for delta in (0, 1):
        civil_year = today_date.year + delta
        hy = pl_dates.GregorianDate(civil_year, 4, 1).to_heb().year

        fourteenth = pl_dates.HebrewDate(hy, 1, 14)
        cdate = fourteenth.to_pydate()
        if cdate >= today_date:
            deferred = (cdate.weekday() == 5)  # 14 Nisan is Shabbos
            return hy, deferred

    hy_next = pl_dates.GregorianDate(today_date.year + 2, 4, 1).to_heb().year
    return hy_next, False


class _BaseChumetzSensor(YidCalZmanDevice, RestoreEntity, SensorEntity):
    """Base class for Achilas/Sriefes Chametz sensors."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self,
        hass: HomeAssistant,
        candle: int,
        havdalah: int,
        slug: str,
        name: str,
        icon: str,
        unique_id: str,
    ) -> None:
        super().__init__()
        self.hass = hass
        # user‐configurable offsets from config flow
        self._candle  = candle
        self._havdalah = havdalah

        cfg = hass.data[DOMAIN]["config"]
        self._tz: ZoneInfo = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._geo: GeoLocation | None = None

        # entity metadata
        self.entity_id = f"sensor.yidcal_{slug}"
        self._attr_name      = name
        self._attr_icon      = icon
        self._attr_unique_id = unique_id

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()
        # recompute at midnight local (registered for cleanup)
        self._register_listener(
            async_track_time_change(
                self.hass,
                self._publishing(self._midnight_update),
                hour=0, minute=0, second=0,
            )
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    def _compute_for_date(
        self, civil_date: date, hours_from_dawn: float, *, round_half_up: bool = False,
    ) -> tuple[datetime, str]:
        """Compute dawn + hours × sha'ah zmanit for a given civil date.

        Returns (rounded_local_dt, raw_iso_string).
        round_half_up=False (default) → floor to minute (machmir for deadlines).
        round_half_up=True            → ≥30s rounds up, <30s floors.

        The rounded value comes from the shared compute_chametz_zmanim
        helper (achilas = 4h/floored, sriefes = 5h/half-up — built to
        match this sensor), so the only supported argument combinations
        are (4.0, False) and (5.0, True) — exactly what the two
        sensors pass.
        """
        # Rounded deadlines from the shared helper (achilas = dawn + 4h
        # FLOORED, sriefes = dawn + 5h HALF-UP, its default sriefes_round).
        achilas, sriefes = compute_chametz_zmanim(
            geo=self._geo,
            tz=self._tz,
            base_date=civil_date,
            havdalah_offset=self._havdalah,
        )
        rounded = sriefes if round_half_up else achilas

        # Raw (unrounded) value for the *_With_Seconds attributes — same
        # MGA day the helper uses, from the shared cached sun events.
        sunrise, sunset = sun_events_for_date(
            geo=self._geo, tz=self._tz, base_date=civil_date,
        )

        # MGA "day": dawn = sunrise − havdalah, nightfall = sunset + havdalah
        dawn      = sunrise - timedelta(minutes=self._havdalah)
        nightfall = sunset  + timedelta(minutes=self._havdalah)

        # one proportional hour
        hour_len = (nightfall - dawn) / 12

        # raw target
        raw = dawn + hour_len * hours_from_dawn
        raw_iso = raw.isoformat()

        return rounded, raw_iso

    # subclasses implement async_update()


class SofZmanAchilasChumetzSensor(_BaseChumetzSensor):
    """סוף-זמן אכילת חמץ עפ\"י המג\"א (4 שעות זמניות).

    Always computed on 14 Nisan — even in a deferred year,
    the halachic deadline for eating chametz is Shabbos morning.
    """

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__(
            hass,
            candle,
            havdalah,
            slug="sof_zman_achilas_chumetz",
            name="Sof Zman Achilas Chumetz",
            icon="mdi:food-croissant",
            unique_id="yidcal_sof_zman_achilas_chumetz",
        )

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        hy, _ = _get_pesach_info(now_local.date())

        # Always 14 Nisan
        civil_14 = pl_dates.HebrewDate(hy, 1, 14).to_pydate()
        target, raw_iso = self._compute_for_date(civil_14, 4.0)

        self._attr_native_value = target.astimezone(timezone.utc)

        human = self._format_simple_time(target.astimezone(self._tz))
        self._attr_extra_state_attributes = {
            "Sof_Zman_Chumetz_With_Seconds": raw_iso,
            "Sof_Zman_Achilas_Chumetz_Simple": human,
        }


class SofZmanSriefesChumetzSensor(_BaseChumetzSensor):
    """סוף-זמן שריפת חמץ עפ\"י המג\"א (5 שעות זמניות).

    Normal year: state + _Simple = 14 Nisan 5th hour.
    Deferred year (14 Nisan on Shabbos): state + _Simple = 13 Nisan
    Friday 5th hour (physical sriefa before Shabbos). An additional
    Sof_Zman_Biur_Simple attribute shows the 14 Nisan Shabbos 5th hour
    (halachic deadline for disposing of remaining chametz via bitul/flush).
    """

    def __init__(self, hass: HomeAssistant, candle: int, havdalah: int) -> None:
        super().__init__(
            hass,
            candle,
            havdalah,
            slug="sof_zman_sriefes_chumetz",
            name="Sof Zman Sriefes Chumetz",
            icon="mdi:fire",
            unique_id="yidcal_sof_zman_sriefes_chumetz",
        )

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        now_local = (now or dt_util.now()).astimezone(self._tz)
        hy, deferred = _get_pesach_info(now_local.date())

        if deferred:
            # Sriefa is Friday (13 Nisan) — state + _Simple
            civil_13 = pl_dates.HebrewDate(hy, 1, 13).to_pydate()
            target, raw_iso = self._compute_for_date(civil_13, 5.0, round_half_up=True)

            # Biur is Shabbos (14 Nisan) — attribute only
            civil_14 = pl_dates.HebrewDate(hy, 1, 14).to_pydate()
            biur_target, biur_raw_iso = self._compute_for_date(civil_14, 5.0, round_half_up=True)
        else:
            # Normal year — sriefa and biur are the same day
            civil_14 = pl_dates.HebrewDate(hy, 1, 14).to_pydate()
            target, raw_iso = self._compute_for_date(civil_14, 5.0, round_half_up=True)
            biur_target = None
            biur_raw_iso = None

        self._attr_native_value = target.astimezone(timezone.utc)

        human = self._format_simple_time(target.astimezone(self._tz))
        attrs: dict[str, object] = {
            "Sof_Zman_Chumetz_With_Seconds": raw_iso,
            "Sof_Zman_Sriefes_Chumetz_Simple": human,
        }

        if biur_target is not None:
            attrs["Sof_Zman_Biur_With_Seconds"] = biur_raw_iso
            attrs["Sof_Zman_Biur_Simple"] = self._format_simple_time(
                biur_target.astimezone(self._tz)
            )

        self._attr_extra_state_attributes = attrs
