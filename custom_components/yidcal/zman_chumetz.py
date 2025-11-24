from __future__ import annotations
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
import homeassistant.util.dt as dt_util

from pyluach import dates as pl_dates
from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

from .const import DOMAIN
from .device import YidCalZmanDevice
from .zman_sensors import get_geo


def _get_bedikat_day_year(today_date: date) -> tuple[int, int]:
    """Return (Hebrew year, day in Nisan) for Chametz search day (14 or 12 Nisan)."""
    for delta in (0, 1):
        civil_year = today_date.year + delta
        hy = pl_dates.GregorianDate(civil_year, 4, 1).to_heb().year

        # normally 14 Nisan
        candidate = pl_dates.HebrewDate(hy, 1, 14)
        # but if 15 Nisan falls on a Sunday, use 12 Nisan
        fifteenth = pl_dates.HebrewDate(hy, 1, 15)
        if fifteenth.to_pydate().weekday() == 6:
            candidate = pl_dates.HebrewDate(hy, 1, 12)

        cdate = candidate.to_pydate()
        if cdate >= today_date:
            return hy, candidate.day

    # fallback to next year's 14 Nisan
    return hy + 1, 14


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
        # recompute at midnight local
        async_track_time_change(
            self.hass,
            self._midnight_update,
            hour=0, minute=0, second=0,
        )

    async def _midnight_update(self, now: datetime) -> None:
        await self.async_update()

    def _compute_target(self, hours_from_dawn: float) -> datetime:
        """Compute dawn + hours * sha'ah_zmanit on the correct Nisan date."""
        now_local = dt_util.now().astimezone(self._tz)
        hy, day   = _get_bedikat_day_year(now_local.date())

        # Hebrew→civil date
        heb  = pl_dates.HebrewDate(hy, 1, day)
        g_py = heb.to_pydate()

        # get geometric sunrise & sunset
        cal     = ZmanimCalendar(geo_location=self._geo, date=g_py)
        sunrise = cal.sunrise().astimezone(self._tz)
        sunset  = cal.sunset().astimezone(self._tz)

        # MGA “day”: dawn = sunrise − havdalah, nightfall = sunset + havdalah
        dawn      = sunrise - timedelta(minutes=self._havdalah)
        nightfall = sunset  + timedelta(minutes=self._havdalah)

        # one proportional hour
        hour_len = (nightfall - dawn) / 12

        # raw target
        raw = dawn + hour_len * hours_from_dawn

        # debug attrs (with seconds)
        self._attr_extra_state_attributes = {
            #"dawn":        dawn.isoformat(),
            #"sunrise":     sunrise.isoformat(),
            #"sunset":      sunset.isoformat(),
            #"nightfall":   nightfall.isoformat(),
            #"hour_len_s":  hour_len.total_seconds(),  # seconds per sha'ah
            "Sof_Zman_Chumetz_With_Seconds":  raw.isoformat(),
        }

        # floor to the minute (any seconds 0–59)
        return raw.replace(second=0, microsecond=0)
        
    # subclasses implement async_update()


class SofZmanAchilasChumetzSensor(_BaseChumetzSensor):
    """סוף-זמן אכילת חמץ עפ\"י המג\"א (4 שעות זמניות)."""

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
        target = self._compute_target(4.0)
        self._attr_native_value = target.astimezone(timezone.utc)
        
        # 3. build your human‐readable string
        local = target.astimezone(self._tz)
        human = self._format_simple_time(local)

        # 4. merge it into the existing attributes
        attrs = {**(self._attr_extra_state_attributes or {})}
        attrs["Sof_Zman_Achilas_Chumetz_Simple"] = human
        self._attr_extra_state_attributes = attrs


class SofZmanSriefesChumetzSensor(_BaseChumetzSensor):
    """סוף-זמן שריפת חמץ עפ\"י המג\"א (5 שעות זמניות)."""

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
        target = self._compute_target(5.0)
        self._attr_native_value = target.astimezone(timezone.utc)

        # 3. build your human‐readable string
        local = target.astimezone(self._tz)
        human = self._format_simple_time(local)

        # 4. merge it into the existing attributes
        attrs = {**(self._attr_extra_state_attributes or {})}
        attrs["Sof_Zman_Sriefes_Chumetz_Simple"] = human
        self._attr_extra_state_attributes = attrs
