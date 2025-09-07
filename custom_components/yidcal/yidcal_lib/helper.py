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
from pyluach.hebrewcal import HebrewDate as PHebrewDate, Month as PMonth




from pyluach.hebrewcal import HebrewDate as PHebrewDate, Month as PMonth

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
            # overflow (e.g., Adar II → Nissan in short form)
            return {"year": hy + 1, "month": 1}

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
        Compute Rosh Chodesh for the Hebrew month containing 'today' using pyluach.
        - If the current Hebrew month has 30 days: include the 30th of that month.
        - Always include the 1st of the next Hebrew month.
        Returns a RoshChodesh object with:
          .month = English name (e.g. "Av")
          .text = e.g. "Shabbos" or "Shabbos & Sunday"
          .days = list of weekday names
          .gdays = list of Python datetime.date for those RC days
        """
        # 1) Convert today → pyluach HebrewDate
        hd = PHebrewDate.from_pydate(today)
        hy, hm = hd.year, hd.month

        pm_cur = PMonth(hy, hm)
        length_cur = sum(1 for _ in pm_cur.iterdates())

        days: list[str] = []
        gdays: list[datetime.date] = []

        # 30th of current month (if it exists)
        if length_cur == 30:
            g30 = PHebrewDate(hy, hm, 30).to_pydate()
            days.append(self.get_day_of_week(g30))
            gdays.append(g30)

        # --- next month (handle Elul→Tishrei year rollover) ---
        if hm == 6:               # Elul → Tishrei bumps the Hebrew year
            hy_next, nm = hy + 1, 7
        else:
            hy_next, nm = hy, hm + 1
            try:
                PMonth(hy_next, nm)
            except ValueError:
                hy_next, nm = hy + 1, 1

        hd1_next = PHebrewDate(hy_next, nm, 1)

        # No "Rosh Chodesh" for Tishrei
        if nm == 7:
            return RoshChodesh(hd1_next.month_name(), "", [], [])

        # Always include day 1 of the next month
        g1_next = hd1_next.to_pydate()
        days.append(self.get_day_of_week(g1_next))
        gdays.append(g1_next)

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
        Return True if 'today' is the actual Shabbos Mevorchim:
          - Normally: the Shabbat on or before R”Ch.
          - Special‑case: if R”Ch is a one‑day RC on Shabbat, or
                          spans Shabbat & Sunday,
            then it’s the *previous* Shabbat.
        Always skip Rosh Chodesh Tishrei.
        """
        # 1) Must be Shabbat
        if not is_shabbat(today):
            return False

        # 2) Compute the RC info for the *month containing* today
        rc = self.get_rosh_chodesh_days(today)
        # Skip Rosh Chodesh Tishrei completely
        if rc.month.upper() == "TISHREI":
            return False

        # 3) Special‑case: first RC day falls on Shabbat
        #    (covers both 1-day RC on Shabbat, and 2-day RC starting on Shabbat)
        if rc.gdays and rc.gdays[0].weekday() == 5:  # Saturday=5
            # Mevorchim is the *week before* that RC‑Shabbat
            target = rc.gdays[0] - timedelta(days=7)
            return today == target

        # 4) Default: back up from the *earliest* RC gday to the prior Saturday
        #    (your existing get_shabbos_mevorchim_hebrew_day_of_month does this)
        smd = self.get_shabbos_mevorchim_hebrew_day_of_month(today)
        if smd is None:
            return False

        # Compare Hebrew‐day numbers
        hd = PHebrewDate.from_pydate(today)
        return hd.day == smd

        
    def is_upcoming_shabbos_mevorchim(self, today: datetime.date) -> bool:
        """
        Return True if the *next* Shabbat after `today` is a Mevorchim Shabbat.
        Special‑case:
          - If RC is a single day on Shabbos, Mevorchim is the previous Shabbat.
          - If RC spans Shabbos & Sunday, Mevorchim is the previous Shabbat.
        Skip Rosh Chodesh Tishrei.
        """
        # 1) figure out the date of the *next* Shabbat
        wd = today.weekday()  # Monday=0 … Saturday=5
        days_to_sat = 7 if wd == 5 else (5 - wd) % 7
        next_shabbat = today + timedelta(days=days_to_sat)

        # 2) compute Rosh Chodesh for that Shabbat
        rc = self.get_rosh_chodesh_days(next_shabbat)
        # rc.days is e.g. ["Shabbos"] or ["Shabbos","Sunday"]
        # rc.gdays is the matching python.date list

        # ─── special case ───────────────────────────────────────
        # if the *first* RC day is Shabbos (one-day RC) or
        # the two-day RC runs Shabbos & Sunday,
        # then the blessing is the week *before* that Shabbat
        if rc.days and rc.days[0] == "Shabbos":
            mevorchim_shabbat = rc.gdays[0] - timedelta(days=7)
            if next_shabbat == mevorchim_shabbat:
                return True

        # ─── default case ───────────────────────────────────────
        # otherwise only turn on if that Shabbat is actually Mevorchim by the usual rule
        if self.is_shabbos_mevorchim(next_shabbat):
            # but never for Tishrei
            if rc.month.upper() != "TISHREI":
                return True

        return False

    def get_actual_molad(self, today: datetime.date) -> Molad:
        """
        Compute the exact molad for the Hebrew month containing `today`
        (if day<3) or for the *next* Hebrew month (if day≥3), using pyluach’s
        built‑in molad_announcement().
        """
        # 1) Pick the target year/month
        hd = PHebrewDate.from_pydate(today)
        hy, hm = hd.year, hd.month

        if hd.day < 3:
            # molad for the current Hebrew month
            pass
        else:
            # molad for the *next* Hebrew month
            if hm == 6:         # Elul → Tishrei bumps the Hebrew year
                hy += 1
                hm = 7
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

        # 5) Build a tz-aware datetime in Jerusalem time
        jer_dt = datetime(
            molad_date.year,
            molad_date.month,
            molad_date.day,
            ann["hour"],
            ann["minutes"],
            tzinfo=ZoneInfo("Asia/Jerusalem")
        )
        jer_dt += jer_dt.dst() or timedelta(0)

        # 6) Format into 12-hour + chalakim (use local time)
        h24 = jer_dt.hour
        minute = jer_dt.minute
        parts  = ann["parts"]
        ampm   = "am" if h24 < 12 else "pm"
        h12    = h24 % 12 or 12
        dayname = self.get_day_of_week(jer_dt.date())
        friendly = f"{dayname}, {h12}:{minute:02d} {ampm} and {parts} chalakim"

        return Molad(dayname, h12, minute, ampm, parts, friendly, jer_dt.date(), jer_dt)

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
    
