# /config/custom_components/yidcal/yidcal_lib/helper.py

"""
Vendored YidCalHelper using pyluach for accurate calculations.

Requires:
    pip install pyluach
"""

import datetime
import logging

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
    def __init__(self, day: str, hours: int, minutes: int, am_or_pm: str, chalakim: int, friendly: str):
        self.day = day                # e.g. "Friday" or "Shabbos"
        self.hours = hours            # in 12-hour format (1–12)
        self.minutes = minutes
        self.am_or_pm = am_or_pm      # "am" or "pm"
        self.chalakim = chalakim      # parts
        self.friendly = friendly      # e.g. "Friday, 10:42 am and 5 chalakim"


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

        # Attempt to build PMonth(hy, nm). If invalid, roll into next year:
        try:
            PMonth(hy, nm)
        except ValueError:
            # Invalid month number (e.g. exceeding 12 or 13 in a given year)
            nm = 1
            hy += 1

        return {"year": hy, "month": nm}

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

        # 2) Determine length of current Hebrew month via pyluach (count iterdates)
        pm_cur = PMonth(hy, hm)
        length_cur = sum(1 for _ in pm_cur.iterdates())

        days: list[str] = []
        gdays: list[datetime.date] = []

        # 3a) If length = 30, compute 30th of current month
        if length_cur == 30:
            hd30 = PHebrewDate(hy, hm, 30)
            g30 = hd30.to_pydate()
            dayname_30 = self.get_day_of_week(g30)
            days.append(dayname_30)
            gdays.append(g30)

        # 3b) Compute 1st of next Hebrew month:
        nm = hm + 1
        hy_next = hy

        # If PMonth(hy, nm) is invalid, roll into month=1 of next year
        try:
            PMonth(hy, nm)
        except ValueError:
            nm = 1
            hy_next += 1

        hd1_next = PHebrewDate(hy_next, nm, 1)
        g1_next = hd1_next.to_pydate()
        dayname_1 = self.get_day_of_week(g1_next)
        days.append(dayname_1)
        gdays.append(g1_next)

        month_name = hd.month_name()  # English, e.g. "Av"
        text = " & ".join(days) if len(days) == 2 else days[0]
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
        sat_date = rc_date - datetime.timedelta(days=days_back)

        # Convert that Saturday → pyluach Hebrew-day
        hd_sat = PHebrewDate.from_pydate(sat_date)
        return hd_sat.day

    def is_shabbos_mevorchim(self, today: datetime.date) -> bool:
        """
        Return True if 'today' is Shabbos Mevorchim. Excludes Elul.
        """
        if not is_shabbat(today):
            return False

        hd_today = PHebrewDate.from_pydate(today)
        smd = self.get_shabbos_mevorchim_hebrew_day_of_month(today)
        if smd is None:
            return False

        # Exclude Elul (in pyluach, month=6 is Elul)
        if hd_today.month == 6:
            return False

        return hd_today.day == smd

    def is_upcoming_shabbos_mevorchim(self, today: datetime.date) -> bool:
        """
        Return True if the next Saturday after 'today' is Shabbos Mevorchim.
        """
        wd = today.weekday()  # 0=Monday … 5=Saturday
        if wd == 5:
            # If today is Saturday, "next Saturday" is 7 days later
            next_sat = today + datetime.timedelta(days=7)
        else:
            days_to_sat = (5 - wd) % 7
            next_sat = today + datetime.timedelta(days=days_to_sat)

        return self.is_shabbos_mevorchim(next_sat)

    def get_actual_molad(self, today: datetime.date) -> Molad:
        """
        Compute the molad for the Hebrew month containing 'today' if Hebrew-day < 3,
        otherwise compute for the next Hebrew month.

        Returns a Molad object with:
          .day      = English weekday (e.g. "Friday")
          .hours    = hours in 12h format (1–12)
          .minutes  = minutes (0–59)
          .am_or_pm = "am" or "pm"
          .chalakim = parts (integer)
          .friendly = textual string like "Friday, 10:42 am and 5 chalakim"
        """
        hd = PHebrewDate.from_pydate(today)
        hy, hm, hd_day = hd.year, hd.month, hd.day

        # Choose current vs. next month:
        if hd_day < 3:
            target_year = hy
            target_month = hm
        else:
            # Next month (roll into next Hebrew year if needed)
            nm = hm + 1
            hy_next = hy
            try:
                PMonth(hy, nm)
            except ValueError:
                nm = 1
                hy_next += 1
            target_year = hy_next
            target_month = nm

        # Now ask pyluach’s Month(...) for molad_announcement:
        pm = PMonth(target_year, target_month)
        ann = pm.molad_announcement()  # dict: {"weekday":1..7, "hour":0..23, "minutes":…, "parts":…}

        wd = ann["weekday"]   # 1=Sunday … 7=Shabbos
        h24 = ann["hour"]
        mins = ann["minutes"]
        parts = ann["parts"]

        ampm = "am" if h24 < 12 else "pm"
        h12 = h24 % 12 or 12
        day_name = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Shabbos"][wd - 1]
        friendly = f"{day_name}, {h12}:{mins:02d} {ampm} and {parts} chalakim"

        return Molad(day_name, h12, mins, ampm, parts, friendly)

    def get_molad(self, today: datetime.date) -> MoladDetails:
        """
        Return a MoladDetails object combining:
          - Molad for (today) via get_actual_molad(today)
          - is_shabbos_mevorchim(today)
          - is_upcoming_shabbos_mevorchim(today)
          - Rosh Chodesh via get_rosh_chodesh_days(today)
        """
        m = self.get_actual_molad(today)
        ism = self.is_shabbos_mevorchim(today)
        isu = self.is_upcoming_shabbos_mevorchim(today)
        rc = self.get_rosh_chodesh_days(today)
        return MoladDetails(m, ism, isu, rc)


def int_to_hebrew(num: int) -> str:
    """
    Convert an integer (1–400+) into Hebrew letters with geresh/gershayim.
    E.g. 5 → 'ה׳', 15 → 'טו״', 100 → 'ק׳'
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
    for value, letter in mapping:
        while temp >= value:
            result += letter
            temp -= value
    # add gershayim for multi-letter, geresh for single
    if len(result) > 1:
        return f"{result[:-1]}\u05F4{result[-1]}"
    return f"{result}\u05F3"
