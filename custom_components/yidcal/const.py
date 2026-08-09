# Constants for the YidCal integration
DOMAIN = "yidcal"

# ─── Zmanim available as individual calendars ───────────────────────
# (option key, Hebrew label, English label)
#
# The Hebrew labels of the first block are EXACTLY the labels
# ``zman_compute.compute_zmanim_for_date`` emits, so calendar.py can
# match an entry by label without a second table. The last two are
# Erev/Motzi times, which come from ``zman_erev_motzi.compute_erev_motzi``
# instead and only exist on the days they apply to.
ZMAN_CALENDAR_CHOICES: list[tuple[str, str, str]] = [
    ("alos",            "עלות השחר",              "Alos HaShachar"),
    ("talis_tefilin",   "זמן טלית ותפילין",        "Talis & Tefilin"),
    ("netz",            "הנץ החמה",               "Netz HaChamah (sunrise)"),
    ("krias_shma_mga",  "סוף זמן קריאת שמע מג״א",  "Sof Zman Krias Shma (MGA)"),
    ("krias_shma_gra",  "סוף זמן קריאת שמע גר״א",  "Sof Zman Krias Shma (GRA)"),
    ("tefilah_mga",     "סוף זמן תפילה מג״א",      "Sof Zman Tefilah (MGA)"),
    ("tefilah_gra",     "סוף זמן תפילה גר״א",      "Sof Zman Tefilah (GRA)"),
    ("chatzos_hayom",   "חצות היום",              "Chatzos HaYom"),
    ("mincha_gedola",   "מנחה גדולה",              "Mincha Gedola"),
    ("mincha_ketana",   "מנחה קטנה",               "Mincha Ketana"),
    ("plag_gra",        "פלג המנחה גר״א",          "Plag HaMincha (GRA)"),
    ("plag_mga",        "פלג המנחה מג״א",          "Plag HaMincha (MGA)"),
    ("shkia",           "שקיעת החמה",              "Shkias HaChamah (sunset)"),
    ("tzeis",           "צאת הכוכבים",             "Tzeis HaKochavim"),
    ("maariv_60",       "זמן מעריב 60",            "Zman Maariv 60"),
    ("maariv_rt",       "זמן מעריב ר״ת",           "Zman Maariv R\"T"),
    ("chatzos_haleila", "חצות הלילה",              "Chatzos HaLaila"),
    ("candle_lighting", "הדלקת הנרות",             "Candle Lighting"),
    ("havdalah",        "מוצאי שבת/יום טוב",        "Havdalah (Motzi)"),
]

#: Keys of the two Erev/Motzi entries above, which are NOT produced by
#: compute_zmanim_for_date and are resolved through compute_erev_motzi.
ZMAN_CALENDAR_EREV_MOTZI_KEYS = ("candle_lighting", "havdalah")

#: key -> (hebrew_label, english_label)
ZMAN_CALENDAR_BY_KEY: dict[str, tuple[str, str]] = {
    key: (heb, eng) for key, heb, eng in ZMAN_CALENDAR_CHOICES
}


# ─── The non-zman calendars ─────────────────────────────────────────
# One multi-select in the config flow rather than a wall of switches:
# picking calendars from a list reads far quicker than reading twelve
# separate yes/no questions, and the page stays one screen tall however
# many calendars get added later.
#
# (option key, Hebrew label, English label)
CALENDAR_CHOICES: list[tuple[str, str, str]] = [
    ("date",              "דעיט",                "Date — Hebrew date + holiday"),
    ("holiday",           "יום טוב",              "Holiday — one event per attribute, exact times"),
    ("day_type",          "דעי טייפ",             "Day Type"),
    ("shabbos_mevorchim", "שבת מברכים",          "Shabbos Mevorchim — with Molad in the details"),
    ("amud_hayomi",       "עמוד היומי",           "Amud HaYomi"),
    ("daf_hayomi",        "דף היומי",             "Daf HaYomi"),
    ("sefirah_short",     "ספירת העומר (קורץ)",   "Sefirah Counter Short"),
    ("special_shabbos",   "ספעציעלע שבת",         "Special Shabbos"),
    ("sof_kiddush_levana", "סוף זמן קידוש לבנה",  "Sof Kiddush Levunah"),
    ("longer_shachris",   "לענגערע שחרית",        "Longer Shachris"),
    ("longer_shabbos_shachris", "לענגערע שבת שחרית", "Longer Shabbos Shachris"),
]

CALENDAR_BY_KEY: dict[str, tuple[str, str]] = {
    key: (heb, eng) for key, heb, eng in CALENDAR_CHOICES
}

#: Extras that decorate the Date calendar's event titles.
#: (option key, Hebrew label, English label)
CALENDAR_DATE_EXTRA_CHOICES: list[tuple[str, str, str]] = [
    ("parsha",  "פרשה — י\"ג אב תשפ\"ו - פרשת צו", "Parsha — י\"ג אב תשפ\"ו - פרשת צו"),
    ("weekday", "וואכנטאג — י\"ג אב תשפ\"ו - ג' צו", "Weekday — י\"ג אב תשפ\"ו - ג' צו"),
]

CALENDAR_DATE_EXTRA_BY_KEY: dict[str, tuple[str, str]] = {
    key: (heb, eng) for key, heb, eng in CALENDAR_DATE_EXTRA_CHOICES
}

# ─── Weekly luach feature flag ──────────────────────────────────────
# The weekly-card luach style (one card per Sun→Shabbos week) ships
# DISABLED in this release. To enable it: change False to True below,
# restart Home Assistant, then call the yidcal.generate_luach service
# — the "Weekly (Single Card)" option reappears in the Style
# dropdown with its full descriptions, and the style works again.
# Everything else about the service is unaffected by this flag.
WEEKLY_LUACH_ENABLED: bool = False
