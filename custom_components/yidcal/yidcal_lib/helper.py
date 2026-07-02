# /config/custom_components/yidcal/yidcal_lib/helper.py

"""
Vendored YidCalHelper using pyluach for accurate calculations.

Requires:
    pip install pyluach
"""

import logging
import datetime
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from pyluach.hebrewcal import HebrewDate as PHebrewDate, Month as PMonth, Year as PYear

_LOGGER = logging.getLogger(__name__)


# Mapping from hdate.Months.name to pyluach month number (1=Nissan … 13=Adar II)
_HD2PY = {
    "NISSAN":      1, "IYYAR":     2, "SIVAN":     3,
    "TAMMUZ":      4, "AV":        5, "ELUL":      6,
    "TISHREI":     7, "CHESHVAN":  8, "MARCHESHVAN": 8,
    "TEVET":      10, "SHEVAT":   11, "ADAR":     12,
    "KISLEV":      9, "ADAR_I":   12, "ADAR_II":  13,
}


def is_shabbat(gdate: datetime.date) -> bool:
    """Return True if the given Gregorian date is Saturday (Shabbat)."""
    return gdate.weekday() == 5  # Python: Monday=0 … Saturday=5


class Molad:
    def __init__(self, day: str, hours: int, minutes: int, am_or_pm: str, chalakim: int, friendly: str, date: date, dt: datetime):
        self.day = day
        self.hours = hours
        self.minutes = minutes
        self.am_or_pm = am_or_pm
        self.chalakim = chalakim
        self.friendly = friendly
        self.date = date
        self.dt = dt


class RoshChodesh:
    def __init__(self, month: str, text: str, days: list[str], gdays: list[datetime.date] | None = None):
        self.month = month            # English month name, e.g. "Av"
        self.text = text              # e.g. "Shabbos" or "Shabbos & Sunday"
        self.days = days              # list of English weekday names
        self.gdays = gdays or []      # list of Python datetime.date objects for those RC days


class MoladDetails:
    def __init__(
        self,
        molad: Molad,
        is_shabbos_mevorchim: bool,
        is_upcoming_shabbos_mevorchim: bool,
        rosh_chodesh: RoshChodesh,
    ):
        self.molad = molad
        self.is_shabbos_mevorchim = is_shabbos_mevorchim
        self.is_upcoming_shabbos_mevorchim = is_upcoming_shabbos_mevorchim
        self.rosh_chodesh = rosh_chodesh


class YidCalHelper:
    def __init__(self, config):
        self.config = config
        self.tz = ZoneInfo(self.config.time_zone)
        # config may contain offsets or location data if needed elsewhere

    def get_numeric_month_year(self, gdate: datetime.date) -> dict[str, int]:
        """
        Given a Python date, return the Hebrew year/month via pyluach.
        Example: gdate = 2025-07-26 → {"year": 5785, "month": 5} (Av).
        """
        hd = PHebrewDate.from_pydate(gdate)
        return {"year": hd.year, "month": hd.month}

    def get_next_numeric_month_year(self, gdate: datetime.date) -> dict[str, int]:
        """
        Given a Python date, return the numeric Hebrew year/month of the next Hebrew month.
        Rolls over into the next Hebrew year when needed.
        """
        hd = PHebrewDate.from_pydate(gdate)
        hy, hm = hd.year, hd.month
        nm = hm + 1

        if hm == 6:            # Elul → Tishrei
            return {"year": hy + 1, "month": 7}

        try:
            PMonth(hy, nm)     # valid within the same Hebrew year?
            return {"year": hy, "month": nm}
        except ValueError:
            # overflow (e.g., Adar → Nissan): same pyluach year
            return {"year": hy, "month": 1}

    def get_gdate(self, numeric_date: dict[str, int], day: int) -> datetime.date:
        """
        Given numeric_date={"year":HYear, "month":HMonth} and a Hebrew-day number,
        return the corresponding Gregorian date (Python datetime.date).
        """
        hd = PHebrewDate(numeric_date["year"], numeric_date["month"], day)
        return hd.to_pydate()

    def get_day_of_week(self, gdate: datetime.date) -> str:
        """
        Return the English weekday for gdate, but substitute "Shabbos" for Saturday.
        """
        wd = gdate.weekday()  # 0=Monday … 5=Saturday, 6=Sunday
        if wd == 5:
            return "Shabbos"
        return gdate.strftime("%A")
        
    def get_rosh_chodesh_days(self, today: datetime.date) -> RoshChodesh:
        """
        Rosh Chodesh of the month FOLLOWING the one containing ``today``.

        The civil RC dates now come from the canonical
        ``halacha_events.rosh_chodesh_civil_days`` (single source of
        truth, proven equal to the previous inline computation across
        5779-5812); this method just wraps them in the RoshChodesh
        display object (weekday names, "Shabbos & Sunday" text).
        """
        from . import halacha_events as he

        hd = PHebrewDate.from_pydate(today)
        hy, hm = hd.year, hd.month

        # --- next month (handle Elul→Tishrei year rollover) ---
        if hm == 6:               # Elul → Tishrei bumps the Hebrew year
            hy_next, nm = hy + 1, 7
        else:
            hy_next, nm = hy, hm + 1
            try:
                PMonth(hy_next, nm)
            except ValueError:
                hy_next, nm = hy, 1   # Adar → Nissan: same pyluach year

        hd1_next = PHebrewDate(hy_next, nm, 1)

        # No "Rosh Chodesh" for Tishrei
        if nm == 7:
            return RoshChodesh(hd1_next.month_name(), "", [], [])

        gdays = list(he.rosh_chodesh_civil_days(hy_next, nm))
        days = [self.get_day_of_week(g) for g in gdays]

        month_name = hd1_next.month_name()  # upcoming month
        text = " & ".join(days) if len(days) == 2 else (days[0] if days else "")
        return RoshChodesh(month_name, text, days, gdays)


    def get_shabbos_mevorchim_hebrew_day_of_month(self, today: datetime.date) -> int | None:
        """
        Compute the Hebrew-day number on which Shabbos Mevorchim falls for the month of 'today':
        - Find the first R”Ch date (either the 30th of the previous month or the 1st of next month).
        - Back up to the most recent Saturday on or before that date.
        - Return that Saturday’s Hebrew-calendar day (integer).
        If no RC is found, return None.
        """
        rc = self.get_rosh_chodesh_days(today)
        if not rc.gdays:
            return None

        # rc.gdays[0] is the earliest RC date (Python datetime.date)
        rc_date = rc.gdays[0]
        wd_rc = rc_date.weekday()  # Monday=0 … Saturday=5
        days_back = (wd_rc - 5) % 7
        sat_date = rc_date - timedelta(days=days_back)

        # Convert that Saturday → pyluach Hebrew-day
        hd_sat = PHebrewDate.from_pydate(sat_date)
        return hd_sat.day

    def is_shabbos_mevorchim(self, today: datetime.date) -> bool:
        """
        True if 'today' is the actual Shabbos Mevorchim.

        Delegates to the canonical
        ``halacha_events.mevorchim_shabbos_for_month`` (single source of
        truth shared with the luach) — proven equal to the previous
        inline special-case logic across 5779-5812. Rosh Chodesh Tishrei
        is always skipped.
        """
        from . import halacha_events as he

        if not is_shabbat(today):
            return False

        rc = self.get_rosh_chodesh_days(today)
        if rc.month.upper() == "TISHREI" or not rc.gdays:
            return False

        nxt = PHebrewDate.from_pydate(rc.gdays[-1])
        return today == he.mevorchim_shabbos_for_month(nxt.year, nxt.month)

        
    def is_upcoming_shabbos_mevorchim(self, today: datetime.date) -> bool:
        """
        Return True if the *next* Shabbat after `today` is a Mevorchim Shabbat.
        Special-case:
          - If RC is a single day on Shabbos, Mevorchim is the previous Shabbat.
          - If RC spans Shabbos & Sunday, Mevorchim is the previous Shabbat.
        Skip Rosh Chodesh Tishrei.
        """
        # 1) figure out the date of the *next* Shabbat
        wd = today.weekday()  # Monday=0 … Saturday=5
        days_to_sat = 7 if wd == 5 else (5 - wd) % 7
        next_shabbat = today + timedelta(days=days_to_sat)

        # Canonical predicate handles all special cases (RC on Shabbos /
        # Shabbos+Sunday) and the Tishrei skip.
        return self.is_shabbos_mevorchim(next_shabbat)

    def get_actual_molad(self, today: datetime.date) -> Molad:
        """
        Compute the exact molad for the Hebrew month containing `today`
        (if day<3) or for the *next* Hebrew month (if day≥3), using pyluach’s
        built-in molad_announcement().
        """
        # 1) Pick the target year/month
        hd = PHebrewDate.from_pydate(today)
        hy, hm = hd.year, hd.month

        if hd.day < 3:
            # Molad for the current Hebrew month
            pass
        else:
            # Molad for the *next* Hebrew month
            is_leap = PYear(hy).leap
            # Elul (6) -> Tishrei (7) bumps the *year*
            if hm == 6:
                hy += 1
                hm = 7
            # Adar I (12, in a leap year) -> Adar II (13), *same year*.
            # Without this branch the old code collapsed Adar I and Adar II
            # together via `elif hm in (12, 13)`, causing the molad sensor to
            # report Nisan's molad on days 3..30 of Adar I (and skip the
            # Adar II announcement entirely on Shabbos Mevorchim Adar II).
            elif hm == 12 and is_leap:
                hm = 13
            # Adar (12, non-leap) or Adar II (13, leap) -> Nissan (1), *same year*
            elif hm in (12, 13):
                hm = 1
            else:
                hm += 1

        # 2) Ask pyluach for its announcement
        pm = PMonth(hy, hm)
        ann = pm.molad_announcement()
        # ann is dict: {"weekday":1..7, "hour":0..23, "minutes":0..59, "parts":0..1079}

        # 3) Convert pyluach’s weekday (1=Sunday…7=Saturday) → Python’s (Mon=0…Sun=6)
        weekday_py = (ann["weekday"] + 5) % 7

        # 4) Find the calendar date of that weekday in the molad-week
        first_of_month = PHebrewDate(hy, hm, 1).to_pydate()
        # how many days from the first to get to that weekday?
        delta_days = (weekday_py - first_of_month.weekday()) % 7
        molad_date = first_of_month + timedelta(days=delta_days)
        if molad_date > first_of_month:
            molad_date -= timedelta(days=7)

        # 5) Build a naive datetime with the raw molad time from pyluach.
        #    pyluach returns the traditional announcement time (no timezone).
        #    Communities adjust for their own DST: when local clocks spring
        #    forward, add 1 hour so the announced time matches the clock.
        molad_dt = datetime(
            molad_date.year,
            molad_date.month,
            molad_date.day,
            ann["hour"],
            ann["minutes"],
        )

        # Check if the user's local timezone has DST active on the molad date
        local_dt = molad_dt.replace(tzinfo=self.tz)
        dst = local_dt.dst()
        if dst:
            molad_dt += dst

        # 6) Format into 12-hour + chalakim
        h24 = molad_dt.hour
        minute = molad_dt.minute
        parts  = ann["parts"]
        ampm   = "am" if h24 < 12 else "pm"
        h12    = h24 % 12 or 12
        dayname = self.get_day_of_week(molad_dt.date())
        friendly = f"{dayname}, {h12}:{minute:02d} {ampm} and {parts} chalakim"

        # Store tz-aware datetime for the Molad object
        molad_dt = molad_dt.replace(tzinfo=self.tz)

        return Molad(dayname, h12, minute, ampm, parts, friendly, molad_dt.date(), molad_dt)

    def get_molad(self, today: datetime.date) -> MoladDetails:
        """
        Package up your Molad + Mevorchim + RoshChodesh into one object.
        """
        m    = self.get_actual_molad(today)
        ism  = self.is_shabbos_mevorchim(today)
        isu  = self.is_upcoming_shabbos_mevorchim(today)
        rc   = self.get_rosh_chodesh_days(today)
        return MoladDetails(m, ism, isu, rc)

def int_to_hebrew(num: int) -> str:
    """
    Convert an integer (1–400+) into Hebrew letters with geresh/gershayim.
    E.g. 5 → 'ה׳', 15 → 'טו״', 100 → 'ק׳', 115 → 'קט״ו'
    """
    mapping = [
        (400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"),
        (90,  "צ"),  (80,  "פ"),  (70,  "ע"),  (60,  "ס"),  (50,  "נ"),
        (40,  "מ"),  (30,  "ל"),  (20,  "כ"),  (10,  "י"),
        (9,   "ט"),  (8,   "ח"),  (7,   "ז"),  (6,   "ו"),  (5,   "ה"),
        (4,   "ד"),  (3,   "ג"),  (2,   "ב"),  (1,   "א"),
    ]
    
    result = ""
    temp = num
    
    # Handle numbers ending in 15 or 16 specially
    # Check if the last two digits are 15 or 16
    if num % 100 == 15:
        # Handle hundreds part if exists
        hundreds = num - 15
        for value, letter in mapping:
            while hundreds >= value:
                result += letter
                hundreds -= value
        # Add ט״ו for the 15 part
        result += "טו"
    elif num % 100 == 16:
        # Handle hundreds part if exists
        hundreds = num - 16
        for value, letter in mapping:
            while hundreds >= value:
                result += letter
                hundreds -= value
        # Add ט״ז for the 16 part
        result += "טז"
    else:
        # Normal processing for all other numbers
        for value, letter in mapping:
            while temp >= value:
                result += letter
                temp -= value
    
    # Add gershayim for multi-letter, geresh for single
    if len(result) > 1:
        return f"{result[:-1]}\u05F4{result[-1]}"
    return f"{result}\u05F3"
