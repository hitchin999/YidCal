from __future__ import annotations
import datetime
from datetime import time
from datetime import timedelta
from zoneinfo import ZoneInfo
from .device import YidCalDisplayDevice

from homeassistant.core import HomeAssistant
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN
from . import DEFAULT_DAY_LABEL_LANGUAGE
from homeassistant.const import STATE_UNKNOWN, STATE_UNAVAILABLE

from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation
from .zman_sensors import get_geo

def _round_half_up(local_dt: datetime.datetime) -> datetime.datetime:
    """Round to nearest minute: <30s → floor, ≥30s → ceil."""
    if local_dt.second >= 30:
        local_dt += timedelta(minutes=1)
    return local_dt.replace(second=0, microsecond=0)


def _round_ceil(local_dt: datetime.datetime) -> datetime.datetime:
    """Always bump up to the *next* minute (Motzei-style rounding)."""
    return (local_dt + timedelta(minutes=1)).replace(second=0, microsecond=0)

class FullDisplaySensor(YidCalDisplayDevice, SensorEntity):
    """
    Combines day label Yiddish, parsha, holiday (from YOUR list via yidcal_holiday attrs),
    R"Chodesh, special Shabbos, and—if any other motzei sensor is ON—adds that as well.
    It will *not* show motzei for 17 Tammuz (Shi’vah Usor b’Tammuz) or Tisha B’av.
    """
    _attr_name = "Full Display"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        slug = "full_display"
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"sensor.yidcal_{slug}"

        self.hass = hass
        self._state = ""

        # Read user choice for day-label language
        cfg = hass.data[DOMAIN]["config"]
        self._day_label_language = cfg.get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE)
        self._include_date      = cfg.get("include_date", False)
        self._candle_offset     = cfg.get("candle_offset", 15)
        self._havdalah_offset = cfg.get("havdalah_offset", 72)
        self._geo: GeoLocation | None = None
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))

    async def async_added_to_hass(self) -> None:
        """Register initial update and start once-per-minute polling."""
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

        # Tick exactly at HH:MM:00 so this display follows the same rounded-minute
        # behavior as the Zman sensors and the other YidCal entities.
        self._register_listener(
            async_track_time_change(
                self.hass,
                self.async_update,
                second=0,
            )
        )

    @property
    def native_value(self) -> str:
        return self._state

    async def async_update(self, now: datetime.datetime | None = None) -> None:
        tz = self._tz
        now = now or datetime.datetime.now(tz)

        def _ok(state: str | None) -> bool:
            if state is None:
                return False
            s = str(state).strip()
            return s not in (
                "",
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
                "unknown",
                "unavailable",
            )

        text = ""

        # 1) Day label (Yiddish or Hebrew per user choice)
        label_entity = f"sensor.yidcal_day_label_{self._day_label_language}"
        day = self.hass.states.get(label_entity)
        if day and _ok(day.state):
            text = day.state.strip()

        # 2) Parsha (suppress sentinel "None")
        parsha = self.hass.states.get("sensor.yidcal_parsha")
        if parsha and _ok(parsha.state):
            ps = str(parsha.state).strip()
            if ps.lower() not in ("none", "פרשת none"):
                text += f" {ps}"

        # 3) Holiday — single state from sensor.yidcal_holiday
        hol = self.hass.states.get("sensor.yidcal_holiday")
        if hol and _ok(hol.state):
            hol_state = hol.state.strip()
            show_holiday = True

            # Suppress ALL ערב… until alos of the *halachic* day, using
            # rounded havdalah + rounded alos (aligned with other sensors).
            if hol_state.startswith("ערב") and self._geo:
                today = now.date()

                # sunset + havdalah_offset → rounded up (Motzei style)
                cal_today = ZmanimCalendar(geo_location=self._geo, date=today)
                sunset_today_raw = cal_today.sunset().astimezone(tz)
                havdalah_cut = _round_ceil(
                    sunset_today_raw + timedelta(minutes=self._havdalah_offset)
                )

                # Halachic date flips at that rounded havdalah point
                halachic_date = today if now < havdalah_cut else (today + timedelta(days=1))

                # Alos for that halachic day: sunrise - 72, rounded half-up
                cal_hal = ZmanimCalendar(geo_location=self._geo, date=halachic_date)
                dawn_raw = cal_hal.sunrise().astimezone(tz) - timedelta(minutes=72)
                dawn = _round_half_up(dawn_raw)

                # Hide ערב… on display until alos
                if now < dawn:
                    show_holiday = False

            if show_holiday:
                text += f" - {hol_state}"

        # 3b) Shabbos Erev Pesach: if this Shabbos is Erev Pesach (מוקדם year),
        # surface "ערב פסח" throughout Shabbos.
        if hol and getattr(hol, "attributes", None):
            if hol.attributes.get("שבת ערב פסח", False):
                if "ערב פסח" not in text:
                    text += " ~ ערב פסח"

        # 5) Special Shabbos — show on Fri only after 12:00 (general),
        # but for "פורים משולש" require candle-lighting; on Shabbos show until havdalah.
        special = self.hass.states.get("sensor.yidcal_special_shabbos")
        show_special = False
        if special and _ok(special.state):
            sstate = str(special.state).strip()
            if sstate.lower() not in ("no data",):
                wd = now.weekday()  # Mon=0 .. Sat=5, Sun=6

                if wd == 4:  # Friday
                    if self._geo:
                        today = now.date()
                        cal = ZmanimCalendar(geo_location=self._geo, date=today)
                        sunset_raw = cal.sunset()
                        if sunset_raw:
                            sunset = sunset_raw.astimezone(tz)
                            candle_raw = sunset - timedelta(minutes=self._candle_offset)
                            candle = _round_half_up(candle_raw)

                            if "פורים משולש" in sstate:
                                # For Purim Meshulash, only after (rounded) candles
                                show_special = now >= candle
                            else:
                                # Normal years: from 12:00 or candles, whichever first
                                show_special = (now.hour >= 12) or (now >= candle)
                    else:
                        if "פורים משולש" in sstate:
                            show_special = False
                        else:
                            show_special = now.hour >= 12

                elif wd == 5 and self._geo:  # Shabbos
                    today = now.date()
                    cal = ZmanimCalendar(geo_location=self._geo, date=today)
                    sunset_raw = cal.sunset()
                    if sunset_raw:
                        sunset = sunset_raw.astimezone(tz)
                        havdalah_raw = sunset + timedelta(minutes=self._havdalah_offset)
                        havdalah = _round_ceil(havdalah_raw)
                        if now < havdalah:
                            show_special = True

                if show_special:
                    text += f" ~ {sstate}"

        # 4) Rosh Chodesh
        rosh = self.hass.states.get("sensor.yidcal_rosh_chodesh_today")
        if rosh and _ok(rosh.state) and rosh.state != "Not Rosh Chodesh Today":
            # only add if not already covered by "שבת ראש חודש"
            if not (
                show_special
                and special
                and _ok(special.state)
                and "שבת ראש חודש" in str(special.state)
            ):
                text += f" ~ {rosh.state.strip()}"

        # 6) Optional “today’s date”
        if self._include_date:
            date_ent = self.hass.states.get("sensor.yidcal_date")
            if date_ent and _ok(date_ent.state):
                text += f" - {date_ent.state.strip()}"

        self._state = text
