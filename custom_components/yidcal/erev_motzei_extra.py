# erev_motzei_extra.py
from __future__ import annotations
import datetime
from datetime import timedelta, time
from zoneinfo import ZoneInfo
from typing import Dict

from hdate import HDateInfo
from zmanim.zmanim_calendar import ZmanimCalendar
from zmanim.util.geo_location import GeoLocation

EXTRA_ATTRS = [
    "ערב שבת",
    "ערב יום טוב",
    "מוצאי שבת",
    "מוצאי יום טוב",
    "ערב שבת שחל ביום טוב",
    "ערב יום טוב שחל בשבת",
    "מוצאי שבת שחל ביום טוב",
    "מוצאי יום טוב שחל בשבת",
]


def _round_half_up(dt: datetime.datetime) -> datetime.datetime:
    if dt.second >= 30:
        dt += timedelta(minutes=1)
    return dt.replace(second=0, microsecond=0)


def _round_ceil(dt: datetime.datetime) -> datetime.datetime:
    return (dt + timedelta(minutes=1)).replace(second=0, microsecond=0)


def _chatzos(cal: ZmanimCalendar, tz: ZoneInfo) -> datetime.datetime:
    """
    Prefer library chatzos() if present; otherwise midpoint sunrise↔sunset.
    """
    try:
        c = cal.chatzos().astimezone(tz)
        return _round_half_up(c)
    except Exception:
        sr = cal.sunrise().astimezone(tz)
        ss = cal.sunset().astimezone(tz)
        mid = sr + (ss - sr) / 2
        return _round_half_up(mid)


def compute_erev_motzei_flags(
    *,
    now: datetime.datetime,
    tz: ZoneInfo,
    geo: GeoLocation,
    diaspora: bool,
    candle_offset: int,
    havdalah_offset: int,
) -> Dict[str, bool]:
    """
    Returns the 8 requested flags keyed by their Hebrew names.
    Windows:
      • ערב שבת / ערב יום טוב → Alos..Candle
      • מוצאי שבת / מוצאי יום טוב → Havdalah..02:00
      • ערב שבת שחל ביום טוב / ערב יום טוב שחל בשבת → Chatzos..Candle
      • מוצאי שבת שחל ביום טוב / מוצאי יום טוב שחל בשבת → Havdalah..02:00
    """
    today = now.date()
    yesterday = today - timedelta(days=1)
    tomorrow = today + timedelta(days=1)

    cal_today = ZmanimCalendar(geo_location=geo, date=today)
    sunrise = cal_today.sunrise().astimezone(tz)
    sunset  = cal_today.sunset().astimezone(tz)

    alos   = _round_half_up(sunrise - timedelta(minutes=72))
    candle = _round_half_up(sunset  - timedelta(minutes=candle_offset))
    chatzos = _chatzos(cal_today, tz)

    hd_y = HDateInfo(yesterday, diaspora=diaspora)
    hd_t = HDateInfo(today,    diaspora=diaspora)
    hd_n = HDateInfo(tomorrow, diaspora=diaspora)

    is_fri = today.weekday() == 4
    is_sat = today.weekday() == 5
    was_sat = yesterday.weekday() == 5
    will_sat = tomorrow.weekday() == 5  # i.e. today == Friday

    is_yomtov_today = hd_t.is_yom_tov
    is_yomtov_yest  = hd_y.is_yom_tov
    is_yomtov_tom   = hd_n.is_yom_tov

    # ---- helpers for motzei-style windows ----
    def _motzei_window(base_date: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
        s = ZmanimCalendar(geo_location=geo, date=base_date).sunset().astimezone(tz)
        start = _round_ceil(s + timedelta(minutes=havdalah_offset))
        end   = datetime.datetime.combine(base_date + timedelta(days=1), time(2, 0), tz)
        return start, end

    def _in(dt_start: datetime.datetime, dt_end: datetime.datetime) -> bool:
        return dt_start <= now < dt_end

    flags: Dict[str, bool] = {k: False for k in EXTRA_ATTRS}

    # ── ערב שבת (Friday, not YT today), window Alos..Candle
    if is_fri and not is_yomtov_today:
        flags["ערב שבת"] = _in(alos, candle)

    # ── ערב יום טוב (tomorrow YT, today not Shabbos/YT), window Alos..Candle
    if (not is_sat) and (not is_yomtov_today) and is_yomtov_tom:
        flags["ערב יום טוב"] = _in(alos, candle)

    # ── ערב שבת שחל ביום טוב (Friday that IS YT today), Chatzos..Candle
    if is_fri and is_yomtov_today:
        flags["ערב שבת שחל ביום טוב"] = _in(chatzos, candle)

    # ── ערב יום טוב שחל בשבת (Shabbos that is Erev YT), Chatzos..Candle
    if is_sat and is_yomtov_tom:
        flags["ערב יום טוב שחל בשבת"] = _in(chatzos, candle)

    # ── מוצאי שבת (Shabbos → chol only), Havdalah..02:00
    # Block Shabbos→Yom Tov (yaknehaz); that’s handled by מוצאי שבת שחל ביום טוב.
    motzei_shabbos_date = (
        today if (is_sat and not is_yomtov_tom)
        else yesterday if (was_sat and not is_yomtov_today)
        else None
    )
    if motzei_shabbos_date:
        s, e = _motzei_window(motzei_shabbos_date)
        flags["מוצאי שבת"] = _in(s, e)

    # ── מוצאי יום טוב (YT → chol only), Havdalah..02:00
    # Do NOT fire when YT ends into Shabbos (that’s מוצאי יום טוב שחל בשבת).
    motzei_yt_date = None
    # YT ends today → tomorrow not YT and not Shabbos
    if is_yomtov_today and not is_yomtov_tom and not will_sat:
        motzei_yt_date = today
    # YT ended yesterday → today not YT and not Shabbos
    elif is_yomtov_yest and not is_yomtov_today and not is_sat:
        motzei_yt_date = yesterday

    if motzei_yt_date:
        s, e = _motzei_window(motzei_yt_date)
        flags["מוצאי יום טוב"] = _in(s, e)

    # ── מוצאי שבת שחל ביום טוב (Shabbos → YT), Havdalah..02:00
    # Yaknehaz: Shabbos rolling straight into Yom Tov.
    yak_base = None
    # Case 1: Today is Shabbos and tomorrow is YT
    if is_sat and is_yomtov_tom:
        yak_base = today
    # Case 2: After midnight: yesterday was Shabbos and today is YT
    elif was_sat and is_yomtov_today:
        yak_base = yesterday

    if yak_base:
        s, e = _motzei_window(yak_base)
        flags["מוצאי שבת שחל ביום טוב"] = _in(s, e)

    # ── מוצאי יום טוב שחל בשבת (YT → Shabbos), Havdalah..02:00
    # Only when 2nd-day YT runs straight into a 3rd-day Shabbos:
    #   • E.g. Fri = YT (day 2), Shabbos = day 3.
    yt_shabbos_base = None
    # Case 1: YT ends today and tomorrow is Shabbos (we're on Friday)
    if is_yomtov_today and not is_yomtov_tom and will_sat:
        yt_shabbos_base = today
    # Case 2: After midnight: YT ended yesterday (Friday) and today is Shabbos
    elif is_yomtov_yest and not is_yomtov_today and yesterday.weekday() == 4 and is_sat:
        yt_shabbos_base = yesterday

    if yt_shabbos_base:
        s, e = _motzei_window(yt_shabbos_base)
        flags["מוצאי יום טוב שחל בשבת"] = _in(s, e)

    return flags
