"""
custom_components/yidcal/calendar.py

YidCal's calendar platform.

Everything YidCal already knows is published as a sensor whose state
answers "what is true right now". A calendar answers a different
question — "when" — and the HA calendar panel, the ``calendar.get_events``
service and every automation that schedules against an upcoming event all
speak that language instead.

Nothing here computes a new zman, parsha or holiday rule. Each calendar
is a thin front end over the pure functions the matching sensor already
uses, so a change to a rule reaches the calendar without being copied:

  Date                 pyluach + ``parsha_sensor.compute_parsha_state``
  Holiday              ``HolidaySensor`` simulated at future moments, with
                       the exact windows it records on ``_flag_windows``
  Day Type             ``DayTypeSensor`` simulated at midday
  Shabbos Mevorchim    ``YidCalHelper.is_shabbos_mevorchim`` + ``molad_context``
  Amud / Daf HaYomi    ``compute_amud_hayomi`` / ``compute_daf_yomi``
  Sefirah (short)      ``sfirah_helper.raw_omer_day`` + ``SEFIRA_SHORT``
  Special Shabbos      ``specials.get_special_shabbos_name``
  Sof Kiddush Levunah  the molad functions in ``zman_compute``
  Longer Shachris      the two ``binary_sensor`` window rules, verbatim
  Zmanim               ``compute_zmanim_for_date`` / ``compute_erev_motzi``

Every calendar entity lands on one device, "YidCal — Calendars", which
only exists while the master toggle is on.

Cost control
------------
The calendar component polls ``event`` about once a minute. Rebuilding a
month of holiday simulations that often would be absurd, so each entity
keeps a window of events around "now", rebuilt every few hours, and each
poll only re-picks the current/next event out of it. ``async_get_events``
— which the panel calls with the range the user is actually looking at —
computes on demand and is bounded by ``_MAX_RANGE_DAYS``.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from pyluach.hebrewcal import HebrewDate as PHebrewDate

from .const import (
    CHOMETZ_ACHILAS_LABEL,
    CHOMETZ_BIUR_LABEL,
    CHOMETZ_SRIEFES_LABEL,
    DOMAIN,
    ZMAN_CALENDAR_BY_KEY,
    ZMAN_CALENDAR_EREV_MOTZI_KEYS,
)
from .config_flow import (
    CONF_ENABLE_CALENDARS, DEFAULT_ENABLE_CALENDARS,
    CONF_CALENDARS, DEFAULT_CALENDARS,
    CONF_CAL_DATE_EXTRAS,
    CONF_CAL_ZMANIM,
    CONF_KIDDUSH_LEVANA_START, DEFAULT_KIDDUSH_LEVANA_START,
    CONF_PARSHA_METZORA_DISPLAY, DEFAULT_PARSHA_METZORA_DISPLAY,
)
from .device import YidCalCalendarDevice
from .day_label_hebrew_full import hebrew_date_string
from .parsha_sensor import compute_parsha_state
from .zman_sensors import get_geo

_LOGGER = logging.getLogger(__name__)

# The live window each entity keeps around "now" so the once-a-minute
# `event` poll costs a list scan instead of a rebuild.
_LIVE_LOOKBACK = dt.timedelta(days=2)
_LIVE_LOOKAHEAD = dt.timedelta(days=45)
_LIVE_REFRESH = dt.timedelta(hours=6)

# Used by the calendars that have an event EVERY day — there is never a
# gap to look across, so a week is more than enough to answer "what's on
# now, and what's next".
_DENSE_LOOKAHEAD = dt.timedelta(days=7)

# Ceiling on what `async_get_events` will compute for one call. The panel
# asks for the visible range, which is a month or two; anything wildly
# larger is a script asking for more than it can use.
_MAX_RANGE_DAYS = 400

# Yield to the event loop every N days of a scan so a long range cannot
# stall Home Assistant.
_YIELD_EVERY = 8

# Python weekday (Mon=0 … Sun=6) → the Hebrew day-of-week letter, for the
# "י״ג אב תשפ״ו - ג׳ צו" form of the date calendar's titles.
_WEEKDAY_LETTER = {6: "א׳", 0: "ב׳", 1: "ג׳", 2: "ד׳", 3: "ה׳", 4: "ו׳", 5: "שבת"}


# ───────────────────────── small shared helpers ─────────────────────────

def _start_dt(event: CalendarEvent, tz: ZoneInfo) -> dt.datetime:
    """An event's start as an aware datetime, all-day events included."""
    value = event.start
    if isinstance(value, dt.datetime):
        return value.astimezone(tz)
    return dt.datetime.combine(value, dt.time(0, 0), tzinfo=tz)


def _end_dt(event: CalendarEvent, tz: ZoneInfo) -> dt.datetime:
    """An event's end as an aware datetime, all-day events included."""
    value = event.end
    if isinstance(value, dt.datetime):
        return value.astimezone(tz)
    return dt.datetime.combine(value, dt.time(0, 0), tzinfo=tz)


def _all_day(day: dt.date, summary: str, description: str | None = None) -> CalendarEvent:
    """One whole civil day. HA wants `date` objects and an exclusive end."""
    return CalendarEvent(
        start=day,
        end=day + dt.timedelta(days=1),
        summary=summary,
        description=description,
    )


def _timed(
    start: dt.datetime,
    end: dt.datetime,
    summary: str,
    description: str | None = None,
) -> CalendarEvent | None:
    """A timed event, or None when the window is empty/backwards.

    HA rejects an event whose end is not after its start, and a window
    that degenerate is a bug upstream rather than something to publish.
    """
    if end <= start:
        return None
    return CalendarEvent(start=start, end=end, summary=summary, description=description)


def _instant(
    moment: dt.datetime,
    summary: str,
    description: str | None = None,
) -> CalendarEvent:
    """A zero-length event at ``moment``.

    A zman is an instant, not a span, and every other Jewish calendar
    shows it as one (7:45 PM - 7:45 PM). HA accepts it: its
    MIN_EVENT_DURATION is 0 and the guard is ``duration < minimum``, so
    start == end validates. The all-day fixup that widens start == end
    to a full day only applies to ``date`` values, not ``datetime``.

    Kept separate from ``_timed``, which stays strict — for the span
    calendars a zero or backwards window really is a bug.
    """
    return CalendarEvent(
        start=moment, end=moment, summary=summary, description=description
    )


def _zman_summary(label: str) -> str:
    """Event title for an instant: the label, prefixed with זמן.

    A zman calendar publishes a moment; a span calendar publishes the
    period around it, and the two often share a name. With both on, the
    Holiday calendar's מוצאי שבת run and the Havdalah zman's single
    moment sit next to each other reading like a duplicate. The prefix
    separates them: מוצאי שבת is the period, זמן מוצאי שבת is the time.

    Labels that already say זמן — סוף זמן קריאת שמע, זמן מעריב ר״ת, the
    chometz deadlines — are returned unchanged rather than doubled up.
    """
    return label if "זמן" in label else f"זמן {label}"


def _merge_spans(spans: list[list[dt.datetime]]) -> list[tuple[dt.datetime, dt.datetime]]:
    """Coalesce overlapping or touching [start, end] pairs."""
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s[0])
    out: list[list[dt.datetime]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [(s, e) for s, e in out]


def _days(start: dt.datetime, end: dt.datetime):
    """Every civil date touched by [start, end], inclusive."""
    day = start.date()
    last = end.date()
    while day <= last:
        yield day
        day += dt.timedelta(days=1)


def _saturdays(start: dt.datetime, end: dt.datetime):
    """Every Saturday touched by [start, end], inclusive."""
    day = start.date()
    day += dt.timedelta(days=(5 - day.weekday()) % 7)
    last = end.date()
    while day <= last:
        yield day
        day += dt.timedelta(days=7)


async def _simulate_holiday(factory, moment: dt.datetime, geo):
    """``HolidaySensor`` as it will read at ``moment``.

    A throwaway instance, exactly the way ``flag_windows`` and
    ``upcoming_holiday_sensor`` do it: it carries the real entity_id but
    no ``platform``, and ``HolidaySensor.async_update`` checks for that
    before publishing anything, so a simulated row can never overwrite the
    live sensor's state.
    """
    sim = factory()
    sim._geo = geo
    await sim.async_update(moment)
    return (
        sim._attr_native_value or "",
        dict(getattr(sim, "_bool_attrs", {}) or {}),
        dict(getattr(sim, "_flag_windows", {}) or {}),
    )


# ─────────────────────────── the base entity ───────────────────────────

class YidCalCalendar(YidCalCalendarDevice, CalendarEntity):
    """Shared plumbing: geo/tz, the live-window cache, and range clamping.

    Subclasses implement ``_async_build(start, end)`` and nothing else.
    """

    _attr_should_poll = True

    # How far the live window reaches. The default suits a SPARSE
    # calendar, where "the next event" can be weeks away. A calendar with
    # an event every single day never needs to look past tomorrow to
    # answer that, so those subclasses set _DENSE_LOOKAHEAD instead and
    # save the scan — which matters most for the ones that simulate a
    # sensor per day.
    _live_lookahead: dt.timedelta = _LIVE_LOOKAHEAD
    _live_lookback: dt.timedelta = _LIVE_LOOKBACK

    def __init__(
        self,
        hass: HomeAssistant,
        slug: str,
        name: str,
        icon: str = "mdi:calendar",
    ) -> None:
        super().__init__()
        self.hass = hass
        self._attr_unique_id = f"yidcal_calendar_{slug}"
        self.entity_id = f"calendar.yidcal_{slug}"
        self._attr_name = name
        self._attr_icon = icon

        cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._tz = ZoneInfo(cfg.get("tzname", hass.config.time_zone))
        self._diaspora = bool(cfg.get("diaspora", True))
        self._candle = int(cfg.get("candlelighting_offset", cfg.get("candle", 15)))
        self._havdalah = int(cfg.get("havdalah_offset", 72))
        self._metzora_display = cfg.get(
            CONF_PARSHA_METZORA_DISPLAY, DEFAULT_PARSHA_METZORA_DISPLAY
        )

        self._geo = None
        self._event: CalendarEvent | None = None
        self._cache: list[CalendarEvent] | None = None
        self._cache_expires: dt.datetime | None = None

    # -- HA interface ---------------------------------------------------

    @property
    def event(self) -> CalendarEvent | None:
        return self._event

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        await self.async_update()

    async def async_update(self) -> None:
        now = dt_util.now().astimezone(self._tz)
        if self._cache is None or self._cache_expires is None or now >= self._cache_expires:
            await self._refresh_cache(now)
        tz = self._tz
        self._event = next(
            (e for e in (self._cache or []) if _end_dt(e, tz) > now), None
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: dt.datetime,
        end_date: dt.datetime,
    ) -> list[CalendarEvent]:
        if self._geo is None:
            self._geo = await get_geo(hass)
        tz = self._tz
        start = start_date.astimezone(tz)
        end = end_date.astimezone(tz)
        if end <= start:
            return []
        if (end - start) > dt.timedelta(days=_MAX_RANGE_DAYS):
            _LOGGER.debug(
                "YidCal %s: clamping a %s-day request to %s days",
                self.entity_id, (end - start).days, _MAX_RANGE_DAYS,
            )
            end = start + dt.timedelta(days=_MAX_RANGE_DAYS)
        events = await self._async_build(start, end)
        events.sort(key=lambda e: _start_dt(e, tz))
        return events

    # -- internals ------------------------------------------------------

    async def _refresh_cache(self, now: dt.datetime) -> None:
        if self._geo is None:
            self._geo = await get_geo(self.hass)
        start = now - self._live_lookback
        end = now + self._live_lookahead
        try:
            events = await self._async_build(start, end)
        except Exception:  # noqa: BLE001 - a broken scan must not kill the entity
            _LOGGER.exception(
                "YidCal %s could not build its events", self.entity_id
            )
            events = []
        events.sort(key=lambda e: _start_dt(e, self._tz))
        self._cache = events
        self._cache_expires = now + _LIVE_REFRESH

    async def _async_build(
        self, start: dt.datetime, end: dt.datetime
    ) -> list[CalendarEvent]:
        raise NotImplementedError

    def _at(self, day: dt.date, hour: int = 12) -> dt.datetime:
        return dt.datetime.combine(day, dt.time(hour, 0), tzinfo=self._tz)

    def _parsha(self, day: dt.date) -> str:
        """This week's parsha for ``day`` — '' when the week has none."""
        return compute_parsha_state(
            day, diaspora=self._diaspora, metzora_display=self._metzora_display
        )


# ───────────────────────────── Date calendar ────────────────────────────

class DateCalendar(YidCalCalendar):
    """The Hebrew date, one all-day event per day, plus the holiday.

    Optionally decorated with the parsha and/or the day of the week:

        parsha            י״ג אב תשפ״ו - פרשת צו
        weekday           י״ג אב תשפ״ו - ג׳ צו
        both              י״ג אב תשפ״ו - ג׳ פרשת צו

    The holiday name is whatever ``sensor.yidcal_holiday`` reports at
    midday on that date, so the calendar and the sensor can never
    disagree about what a day is called.
    """

    # One holiday simulation per day, and an event every day — so keep the
    # live window to a week and let the panel ask for wider ranges itself.
    _live_lookahead = _DENSE_LOOKAHEAD
    _live_lookback = dt.timedelta(days=1)

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        include_parsha: bool,
        include_weekday: bool,
    ) -> None:
        super().__init__(hass, "date", "YidCal Date", "mdi:calendar-range")
        self._include_parsha = include_parsha
        self._include_weekday = include_weekday

    def _suffix(self, day: dt.date) -> str:
        if not (self._include_parsha or self._include_weekday):
            return ""
        parsha = self._parsha(day)
        bare = parsha[len("פרשת "):] if parsha.startswith("פרשת ") else parsha
        letter = _WEEKDAY_LETTER[day.weekday()]

        if self._include_weekday and self._include_parsha:
            return f"{letter} {parsha}".strip() if parsha else letter
        if self._include_weekday:
            return f"{letter} {bare}".strip() if bare else letter
        return parsha

    async def _async_build(self, start, end):
        from .holiday_sensor import HolidaySensor

        def factory():
            return HolidaySensor(self.hass, self._candle, self._havdalah)

        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start, end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)

            date_str = hebrew_date_string(day)
            try:
                holiday, _row, _windows = await _simulate_holiday(
                    factory, self._at(day), self._geo
                )
            except Exception:  # noqa: BLE001 - the date itself still stands
                _LOGGER.debug(
                    "YidCal date calendar: no holiday for %s", day, exc_info=True
                )
                holiday = ""

            parts = [date_str]
            suffix = self._suffix(day)
            if suffix:
                parts.append(suffix)
            if holiday:
                parts.append(holiday)

            details = [f"תאריך: {date_str}"]
            parsha = self._parsha(day)
            if parsha:
                details.append(f"פרשה: {parsha}")
            if holiday:
                # NOT "יום טוב" — sensor.yidcal_holiday emits fasts,
                # erev/motzei days, chol hamoed and יום כיפור קטן from
                # the same field, and labelling צום שבעה עשר בתמוז a
                # יום טוב is both wrong and jarring on a somber day.
                # This label has to be true for all ~65 of its states.
                details.append(f"היום: {holiday}")

            events.append(_all_day(day, " - ".join(parts), "\n".join(details)))
        return events


# ─────────────────────────── Holiday calendar ───────────────────────────

class HolidayCalendar(YidCalCalendar):
    """One timed event per holiday attribute, spanning exactly its window.

    ``sensor.yidcal_holiday`` publishes ~106 boolean flags and records,
    on ``_flag_windows``, the precise start and end of every flag that is
    on. That is where the "from 9:35 until …" comes from — these are the
    real candle-lighting / havdalah / alos edges the flag itself is gated
    on, not a whole-day approximation.

    A flag on for several days holds a different window each day (Chanukah
    is eight, Rosh Chodesh is two), so same-flag windows that touch are
    merged into the one run they belong to.
    """

    # Two simulations per day makes this the most expensive scan here, so
    # its live window is the shortest — long enough to always hold the
    # next holiday, short enough that the six-hourly rebuild stays cheap.
    _live_lookahead = dt.timedelta(days=21)
    _live_lookback = dt.timedelta(days=1)

    # Night-only windows contain 01:00, day-only ones contain 12:00; every
    # window shape the holiday sensor uses spans at least one of the two.
    _SAMPLE_HOURS = (1, 12)

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "holiday", "YidCal Holiday", "mdi:calendar-star")

    async def _async_build(self, start, end):
        from .holiday_sensor import HolidaySensor

        def factory():
            return HolidaySensor(self.hass, self._candle, self._havdalah)

        # Scan a day either side so a run straddling the range edge is
        # reported whole rather than clipped to the sample grid.
        scan_start = start - dt.timedelta(days=1)
        scan_end = end + dt.timedelta(days=1)

        per_flag: dict[str, list[list[dt.datetime]]] = {}
        for index, day in enumerate(_days(scan_start, scan_end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            for hour in self._SAMPLE_HOURS:
                try:
                    _state, row, windows = await _simulate_holiday(
                        factory, self._at(day, hour), self._geo
                    )
                except Exception:  # noqa: BLE001
                    _LOGGER.debug(
                        "YidCal holiday calendar: sample failed for %s %02d:00",
                        day, hour, exc_info=True,
                    )
                    continue
                for flag, window in windows.items():
                    if not row.get(flag):
                        continue
                    win_start, win_end = window
                    if win_end <= win_start:
                        continue
                    per_flag.setdefault(flag, []).append([win_start, win_end])

        events: list[CalendarEvent] = []
        for flag, spans in per_flag.items():
            for span_start, span_end in _merge_spans(spans):
                if span_end <= start or span_start >= end:
                    continue
                event = _timed(
                    span_start,
                    span_end,
                    flag,
                    "\n".join([
                        f"sensor.yidcal_holiday · {flag}",
                        f"פון: {span_start.strftime('%Y-%m-%d %H:%M')}",
                        f"ביז: {span_end.strftime('%Y-%m-%d %H:%M')}",
                    ]),
                )
                if event:
                    events.append(event)
        return events


# ─────────────────────────── Day Type calendar ──────────────────────────

class DayTypeCalendar(YidCalCalendar):
    """Which day type each day is, as one all-day event per day.

    ``sensor.yidcal_day_type`` changes several times within a day (Erev
    at dawn, Shabbos at candle-lighting, Motzi at havdalah). Pinning those
    edges for a whole month would mean simulating the sensor around the
    clock; the calendar instead answers the question it was asked — which
    day type a given day IS — from the state at midday.
    """

    _live_lookahead = _DENSE_LOOKAHEAD
    _live_lookback = dt.timedelta(days=1)

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "day_type", "YidCal Day Type", "mdi:calendar-check")

    async def _async_build(self, start, end):
        from .day_type import DayTypeSensor

        sim = DayTypeSensor(self.hass, self._candle, self._havdalah)
        sim._geo = self._geo

        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start, end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            try:
                await sim.async_update(self._at(day))
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal day-type calendar: %s failed", day, exc_info=True
                )
                continue
            state = sim._attr_native_value
            if not state:
                continue
            events.append(
                _all_day(day, state, "sensor.yidcal_day_type (midday)")
            )
        return events


# ────────────────────── Shabbos Mevorchim calendar ──────────────────────

class ShabbosMevorchimCalendar(YidCalCalendar):
    """Which Shabbos is Shabbos Mevorchim, with the Molad in the details.

    The event description carries the same announcement
    ``sensor.yidcal_molad`` publishes — via the shared ``molad_context``
    — plus which day(s) Rosh Chodesh falls on and their Hebrew dates.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "shabbos_mevorchim", "YidCal Shabbos Mevorchim", "mdi:moon-new"
        )
        from .yidcal_lib.helper import YidCalHelper
        from .config_flow import CONF_MOLAD_LANGUAGE, DEFAULT_MOLAD_LANGUAGE

        self._helper = YidCalHelper(hass.config)
        self._geo = None
        cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._language = cfg.get(CONF_MOLAD_LANGUAGE, DEFAULT_MOLAD_LANGUAGE)

    async def _async_build(self, start, end):
        from .sensor import molad_context, molad_texts
        from .yidcal_lib import molad_text as MT

        language = self._language if self._language in MT.LANGUAGES else "yiddish"

        events: list[CalendarEvent] = []
        for saturday in _saturdays(start, end):
            try:
                if not self._helper.is_shabbos_mevorchim(saturday):
                    continue
                if self._geo is None:
                    self._geo = await get_geo(self.hass)
                ctx = molad_context(
                    helper=self._helper,
                    havdalah_offset=self._havdalah,
                    today=saturday,
                    geo=self._geo,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal mevorchim calendar: %s failed", saturday, exc_info=True
                )
                continue

            texts = molad_texts(ctx)
            month = ctx["month_hebrew"]

            rc_days = [
                MT.day_label(d, language)
                for d in ctx["rosh_chodesh_days_english"]
            ]
            rc_dates = [
                f"{hebrew_date_string(g)} ({g.isoformat()})"
                for g in ctx["rosh_chodesh_gdays"]
            ]

            details = [texts[language]["full"], ""]
            if rc_days:
                details.append("ראש חודש: " + " · ".join(rc_days))
            for line in rc_dates:
                details.append("  " + line)

            events.append(
                _all_day(
                    saturday,
                    f"שבת מברכים חודש {month}",
                    "\n".join(details).strip(),
                )
            )
        return events


# ─────────────────────── Amud / Daf HaYomi calendars ────────────────────

class AmudHaYomiCalendar(YidCalCalendar):
    """Today's amud, one all-day event per day."""

    _live_lookahead = _DENSE_LOOKAHEAD

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "amud_hayomi", "YidCal Amud HaYomi", "mdi:book-open-page-variant")

    async def _async_build(self, start, end):
        from .amud_hayomi_sensor import compute_amud_hayomi

        events: list[CalendarEvent] = []
        for day in _days(start, end):
            heb, eng, daf, daf_heb, amud_heb, amud_eng, cycle, day_in_cycle = (
                compute_amud_hayomi(day)
            )
            events.append(
                _all_day(
                    day,
                    f"{heb} {daf_heb} ע\"{amud_heb}",
                    "\n".join([
                        f"{eng} {daf}{amud_eng}",
                        f"Cycle {cycle}, day {day_in_cycle}",
                    ]),
                )
            )
        return events


class DafHaYomiCalendar(YidCalCalendar):
    """Today's daf, one all-day event per day."""

    _live_lookahead = _DENSE_LOOKAHEAD

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "daf_hayomi", "YidCal Daf HaYomi", "mdi:book-open-variant")

    async def _async_build(self, start, end):
        from .daf_hayomi_sensor import compute_daf_yomi

        events: list[CalendarEvent] = []
        for day in _days(start, end):
            heb, eng, daf, daf_heb, cycle, day_in_cycle = compute_daf_yomi(day)
            events.append(
                _all_day(
                    day,
                    f"{heb} {daf_heb}",
                    "\n".join([
                        f"{eng} {daf}",
                        f"Cycle {cycle}, day {day_in_cycle}",
                    ]),
                )
            )
        return events


# ────────────────────── Sefirah (short) calendar ────────────────────────

class SefirahShortCalendar(YidCalCalendar):
    """The short Omer count (ח׳ בעומר) on each day it is counted."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "sefirah_short", "YidCal Sefirah Counter Short", "mdi:counter")

    async def _async_build(self, start, end):
        from .yidcal_lib.sfirah_helper import SEFIRA_SHORT, raw_omer_day

        events: list[CalendarEvent] = []
        for day in _days(start, end):
            omer = raw_omer_day(day)
            if not 1 <= omer <= 49:
                continue
            events.append(
                _all_day(day, SEFIRA_SHORT[omer], f"יום {omer} לעומר")
            )
        return events


# ───────────────────────── Special Shabbos calendar ─────────────────────

class SpecialShabbosCalendar(YidCalCalendar):
    """Every Shabbos that carries a name — שבת שקלים, שבת הגדול, and so on."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "special_shabbos", "YidCal Special Shabbos", "mdi:calendar-star")

    async def _async_build(self, start, end):
        from .yidcal_lib import specials

        events: list[CalendarEvent] = []
        for saturday in _saturdays(start, end):
            try:
                name = specials.get_special_shabbos_name(
                    today=saturday, is_in_israel=not self._diaspora
                )
            except TypeError:
                name = specials.get_special_shabbos_name(saturday)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal special-shabbos calendar: %s failed",
                    saturday, exc_info=True,
                )
                continue
            if not name:
                continue
            events.append(_all_day(saturday, name, "sensor.yidcal_special_shabbos"))
        return events


# ──────────────────── Sof Kiddush Levunah calendar ──────────────────────

class SofKiddushLevanaCalendar(YidCalCalendar):
    """One event per lunar month: when Kiddush Levana may be said.

    The event runs from the configured start opinion (ג׳ or ז׳ שלימים,
    per the same option ``binary_sensor.yidcal_kiddush_levana`` uses) to
    the Rema's sof zman, so the event's END is the deadline the user is
    actually watching for. The printed-luach line for both the deadline
    and the ז׳-שלמים start rides along in the description.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "sof_kiddush_levana", "YidCal Sof Kiddush Levunah", "mdi:moon-waning-crescent"
        )
        cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._start_opinion = cfg.get(
            CONF_KIDDUSH_LEVANA_START, DEFAULT_KIDDUSH_LEVANA_START
        )

    async def _async_build(self, start, end):
        from .kiddush_levana_sensors import _next_hebrew_month
        from .yidcal_lib.luach_data import (
            szkl_anchor_when_with_parsha,
            zsh_anchor_when_with_parsha,
        )
        from .yidcal_lib.zman_compute import (
            gimmel_shleimim_local,
            sof_zman_kiddush_levana_rama_local,
            zayin_shleimim_local,
        )

        tz = self._tz
        # Step back one month so a cycle already under way when the range
        # opens is still reported, then walk forward until the deadline
        # clears the range.
        ph = PHebrewDate.from_pydate(start.date())
        hy, hm = ph.year, ph.month
        if hm == 7:
            hy, hm = hy - 1, 6
        else:
            hy, hm = (hy, hm - 1) if hm > 1 else (hy, 13)

        events: list[CalendarEvent] = []
        for _ in range(16):  # ≥ 14 months, the widest range we ever build
            try:
                gimmel = gimmel_shleimim_local(hy, hm, tz).replace(tzinfo=tz)
                zayin = zayin_shleimim_local(hy, hm, tz).replace(tzinfo=tz)
                sof_naive = sof_zman_kiddush_levana_rama_local(hy, hm, tz)
                sof = sof_naive.replace(tzinfo=tz)
                month_name = PHebrewDate(hy, hm, 1).month_name(True)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal kiddush-levana calendar: %s/%s failed", hy, hm, exc_info=True
                )
                hy, hm = _next_hebrew_month(hy, hm)
                continue

            if sof > start:
                begin = gimmel if self._start_opinion == "gimmel" else zayin
                if begin < end:
                    ref = sof_naive.date()
                    try:
                        deadline_line = "ס״ז קידוש לבנה: " + szkl_anchor_when_with_parsha(
                            sof_naive, geo=self._geo, tz=tz,
                            diaspora=self._diaspora,
                            metzora_display=self._metzora_display,
                            ref_date=ref,
                        )
                        zayin_line = "ז׳ שלמים: " + zsh_anchor_when_with_parsha(
                            zayin.replace(tzinfo=None), geo=self._geo, tz=tz,
                            diaspora=self._diaspora,
                            metzora_display=self._metzora_display,
                            ref_date=ref,
                        )
                    except Exception:  # noqa: BLE001
                        deadline_line = zayin_line = ""

                    event = _timed(
                        begin,
                        sof,
                        f"קידוש לבנה — {month_name}",
                        "\n".join(
                            line for line in (
                                deadline_line,
                                zayin_line,
                                f"ג׳ שלמים: {gimmel.strftime('%Y-%m-%d %H:%M')}",
                            ) if line
                        ),
                    )
                    if event:
                        events.append(event)

            if sof > end:
                break
            hy, hm = _next_hebrew_month(hy, hm)
        return events


# ─────────────────────── Longer Shachris calendars ──────────────────────

class LongerShachrisCalendar(YidCalCalendar):
    """The weekday mornings shachris runs long — 04:00–14:00 windows.

    Delegates to ``LongerShachrisSensor``'s own qualification and window
    rules so the calendar and the binary sensor agree by construction.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "longer_shachris", "YidCal Longer Shachris", "mdi:alarm")

    async def _async_build(self, start, end):
        from .longer_shachris_sensor import LongerShachrisSensor

        sim = LongerShachrisSensor(self.hass, self._candle, self._havdalah)
        sim._geo = self._geo

        events: list[CalendarEvent] = []
        for day in _days(start - dt.timedelta(days=1), end):
            try:
                if not sim._qualifies(day):
                    continue
                if sim._is_shabbos(day) or sim._is_yomtov(day):
                    continue
                window_start, window_end = sim._window_for(day)
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal longer-shachris calendar: %s failed", day, exc_info=True
                )
                continue
            if window_end <= start or window_start >= end:
                continue
            event = _timed(
                window_start, window_end, "לענגערע שחרית",
                "binary_sensor.yidcal_longer_shachris",
            )
            if event:
                events.append(event)
        return events


class LongerShabbosShachrisCalendar(YidCalCalendar):
    """The Shabbosos shachris runs long, for the whole Shabbos window."""

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "longer_shabbos_shachris",
            "YidCal Longer Shabbos Shachris", "mdi:alarm",
        )

    async def _async_build(self, start, end):
        from .longer_shabbos_shachris_sensor import LongerShabbosSensor

        sim = LongerShabbosSensor(self.hass, self._candle, self._havdalah)
        sim._geo = self._geo

        events: list[CalendarEvent] = []
        # One Shabbos earlier, so a window opening the Friday before the
        # range starts is still reported.
        for saturday in _saturdays(start - dt.timedelta(days=8), end):
            try:
                reasons = sim._get_reasons(saturday)
                if not reasons:
                    continue
                window_start, window_end = sim._shabbos_window(
                    saturday - dt.timedelta(days=1), saturday
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal longer-shabbos calendar: %s failed", saturday, exc_info=True
                )
                continue
            if window_end <= start or window_start >= end:
                continue
            event = _timed(
                window_start, window_end, " / ".join(reasons),
                "binary_sensor.yidcal_longer_shabbos_shachris\n"
                + " / ".join(reasons),
            )
            if event:
                events.append(event)
        return events


# ────────────────────────────── Zman calendars ──────────────────────────

#: Event title for a candle lighting. Deliberately NOT the label used to
#: look the time up — ``compute_erev_motzi`` and ``compute_zmanim_for_date``
#: both key on "הדלקת נרות" and the printed luach uses that wording, so the
#: display title is kept separate from the lookup key.
CANDLE_LIGHTING_SUMMARY = "הדלקת הנרות"


class ZmanCalendar(YidCalCalendar):
    """One zman, one short event per day at exactly that time.

    Daily zmanim come from ``compute_zmanim_for_date`` — the same function
    the Zmanim Lookup sensor and the printed luach use. Candle lighting
    and havdalah come from ``compute_erev_motzi`` instead, since they only
    exist on the days a no-melucha block starts or ends; that helper
    answers for any day *in or before* a block, so an event is only kept
    on the day the time actually falls on, which is also what keeps a
    three-day Yom Tov from producing three identical candle-lightings.
    """

    # Daily zmanim land every day. Candle lighting / havdalah do not, but
    # never go more than a week without one either.
    _live_lookahead = _DENSE_LOOKAHEAD

    def __init__(self, hass: HomeAssistant, key: str) -> None:
        hebrew, english = ZMAN_CALENDAR_BY_KEY[key]
        super().__init__(
            hass, f"zman_{key}", f"YidCal {english}", "mdi:clock-outline"
        )
        self._key = key
        self._hebrew = hebrew
        self._english = english
        cfg = (hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        self._tallis = int(cfg.get("tallis_tefilin_offset", 22))
        if key == "chometz":
            # Once a year, so the default week-long cache would leave
            # this entity empty for eleven months. Widening is cheap:
            # the scan does nothing on any day but 13/14 Nisan.
            self._live_lookahead = dt.timedelta(days=400)
            self._live_lookback = dt.timedelta(days=2)

    async def _async_build(self, start, end):
        if self._key == "chometz":
            return await self._build_chometz(start, end)
        if self._key == "candle_lighting":
            return await self._build_candle_lighting(start, end)
        if self._key in ZMAN_CALENDAR_EREV_MOTZI_KEYS:
            return await self._build_erev_motzi(start, end)
        return await self._build_daily(start, end)

    async def _build_daily(self, start, end):
        from .yidcal_lib.zman_compute import compute_zmanim_for_date

        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start - dt.timedelta(days=1), end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            try:
                items = compute_zmanim_for_date(
                    geo=self._geo,
                    tz=self._tz,
                    base_date=day,
                    tallis_offset=self._tallis,
                    havdalah_offset=self._havdalah,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal zman calendar %s: %s failed", self._key, day, exc_info=True
                )
                continue
            moment = next(
                (e.dt_local for e in items if e.label == self._hebrew), None
            )
            if moment is None or not (start <= moment < end):
                continue
            events.append(
                _instant(moment, _zman_summary(self._hebrew), self._english)
            )
        return events

    async def _build_chometz(self, start, end):
        """The Erev-Pesach chometz deadlines.

        Two zmanim in a normal year, three when 14 Nisan is Shabbos and
        they split across Friday and Shabbos. Which deadline lands on
        which day is a halachic question, so it is answered by
        ``halacha_events.chometz_deadline_days`` rather than worked out
        again here; the times come from ``compute_chametz_zmanim`` with
        both deadlines floored, which is the chumrah the printed luach
        uses and what the chometz sensors and the zmanim lookup now use
        too — so every surface shows the same minute.

        These are instants, like candle lighting.
        """
        from .yidcal_lib.halacha_events import chometz_deadline_days
        from .yidcal_lib.zman_compute import compute_chametz_zmanim

        #: deadline key -> (event summary, index into the returned pair)
        _WHICH = {
            "achilas": (CHOMETZ_ACHILAS_LABEL, 0),
            "sriefes": (CHOMETZ_SRIEFES_LABEL, 1),
            "biur": (CHOMETZ_BIUR_LABEL, 1),
        }

        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start, end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            try:
                heb = PHebrewDate.from_pydate(day)
                if heb.month != 1 or heb.day not in (13, 14):
                    continue
                schedule = chometz_deadline_days(heb.year)
                due = [k for k, d in schedule.items() if d == day]
                if not due:
                    continue

                pair = compute_chametz_zmanim(
                    geo=self._geo, tz=self._tz, base_date=day,
                    havdalah_offset=self._havdalah,
                    sriefes_round="floor",
                )
                wanted = [(_WHICH[k][0], pair[_WHICH[k][1]]) for k in due]
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal chometz calendar: %s failed", day, exc_info=True
                )
                continue

            for label, moment in wanted:
                if not (start <= moment < end):
                    continue
                events.append(
                    _instant(moment, _zman_summary(label), self._english)
                )
        return events

    async def _build_candle_lighting(self, start, end):
        """Every candle lighting, not just the one that opens a block.

        ``compute_erev_motzi`` answers "when does this no-melucha block
        get lit into", which is the right question for the Erev sensor
        but the wrong one here: it deliberately drops the 2nd-night Yom
        Tov and Motzei-Shabbos-into-Yom-Tov lightings, because those are
        mid-block. On a calendar you want all of them — a 2-day Yom Tov
        has two lightings and the second one is the one people look up.

        ``lighting_event_for_day`` is the per-day primitive underneath
        that helper and already distinguishes all four cases, so this
        just asks it once per civil day.
        """
        from .zman_sensors import (
            label_for_kind_and_context,
            lighting_event_for_day,
            round_lighting_for_kind,
        )

        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start - dt.timedelta(days=1), end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            try:
                moment, kind = lighting_event_for_day(
                    day,
                    diaspora=self._diaspora,
                    tz=self._tz,
                    geo=self._geo,
                    candle_offset=self._candle,
                    havdalah_offset=self._havdalah,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal zman calendar %s: %s failed", self._key, day, exc_info=True
                )
                continue

            if moment is None or kind == "none":
                continue
            # The primitive is raw; round it exactly as the Zman Erev
            # sensor does, or the calendar shows 6:59 where the sensor
            # and the luach both say 7:00.
            moment = round_lighting_for_kind(moment, kind)
            # An after-tzeis lighting can land past midnight at extreme
            # latitude; keep it on the day it actually falls on so it
            # cannot be emitted twice.
            if moment.date() != day:
                continue
            if not (start <= moment < end):
                continue

            # "Yom Tov - Night 2" / "Motzi Shabbos -> Yom Tov" etc., so the
            # event says which lighting it is rather than just "Candle
            # Lighting" three nights running.
            context = label_for_kind_and_context(
                day, kind, diaspora=self._diaspora
            )
            description = (
                f"{self._english} - {context}"
                if context and context != "\u2014"
                else self._english
            )
            events.append(_instant(
                moment, _zman_summary(CANDLE_LIGHTING_SUMMARY), description
            ))
        return events

    async def _build_erev_motzi(self, start, end):
        from .yidcal_lib.zman_erev_motzi import compute_erev_motzi

        wanted_candle = self._key == "candle_lighting"
        events: list[CalendarEvent] = []
        for index, day in enumerate(_days(start - dt.timedelta(days=1), end)):
            if index and index % _YIELD_EVERY == 0:
                await asyncio.sleep(0)
            try:
                found = compute_erev_motzi(
                    day,
                    diaspora=self._diaspora,
                    geo=self._geo,
                    tz=self._tz,
                    candle_offset=self._candle,
                    havdalah_offset=self._havdalah,
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "YidCal zman calendar %s: %s failed", self._key, day, exc_info=True
                )
                continue

            if wanted_candle:
                picked = [(CANDLE_LIGHTING_SUMMARY, found.get("הדלקת נרות"))]
            else:
                picked = [
                    (label, found.get(label))
                    for label in ("מוצאי יום טוב", "מוצאי שבת")
                ]

            for label, moment in picked:
                if moment is None:
                    continue
                # The helper answers for the whole block, so the same time
                # comes back on several days. Keep it once, on its own day.
                if moment.date() != day:
                    continue
                if not (start <= moment < end):
                    continue
                events.append(
                    _instant(moment, _zman_summary(label), self._english)
                )
        return events


# ──────────────────────────── platform setup ────────────────────────────

#: calendar key (const.CALENDAR_CHOICES) -> the class that builds it.
#: Adding a calendar means adding a row here and a row in const.py.
_CALENDAR_CLASSES = {
    "holiday": HolidayCalendar,
    "day_type": DayTypeCalendar,
    "shabbos_mevorchim": ShabbosMevorchimCalendar,
    "amud_hayomi": AmudHaYomiCalendar,
    "daf_hayomi": DafHaYomiCalendar,
    "sefirah_short": SefirahShortCalendar,
    "special_shabbos": SpecialShabbosCalendar,
    "sof_kiddush_levana": SofKiddushLevanaCalendar,
    "longer_shachris": LongerShachrisCalendar,
    "longer_shabbos_shachris": LongerShabbosShachrisCalendar,
    # "date" is handled separately — it takes the two title extras.
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Create the calendars the user picked, or nothing at all."""
    opts = (hass.data.get(DOMAIN, {}) or {}).get(entry.entry_id, {}) or {}

    if not opts.get(CONF_ENABLE_CALENDARS, DEFAULT_ENABLE_CALENDARS):
        return

    chosen = opts.get(CONF_CALENDARS)
    if chosen is None:
        chosen = DEFAULT_CALENDARS
    chosen = list(chosen)
    extras = set(opts.get(CONF_CAL_DATE_EXTRAS) or [])

    entities: list[YidCalCalendar] = []

    if "date" in chosen:
        entities.append(
            DateCalendar(
                hass,
                include_parsha="parsha" in extras,
                include_weekday="weekday" in extras,
            )
        )

    for key in chosen:
        cls = _CALENDAR_CLASSES.get(key)
        if cls is not None:
            entities.append(cls(hass))
        elif key != "date":
            _LOGGER.warning("YidCal: unknown calendar key %r — skipped", key)

    for key in opts.get(CONF_CAL_ZMANIM) or []:
        if key in ZMAN_CALENDAR_BY_KEY:
            entities.append(ZmanCalendar(hass, key))
        else:
            _LOGGER.warning("YidCal: unknown zman calendar key %r — skipped", key)

    if entities:
        async_add_entities(entities, update_before_add=False)
