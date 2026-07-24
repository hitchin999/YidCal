"""Runtime UI language table for the YidCal config & options flows.

Single source of truth for every string the config/options flow shows.
The language is chosen *inside the flow* (see CONF_UI_LANGUAGE) rather
than being dictated by the user's Home Assistant profile language.

How this works
--------------
strings.json / translations/en.json are pure ICU *templates*: every
visible string is a bare "{token}". The real text is supplied at render
time via description_placeholders, built from the tables below. The HA
frontend passes description_placeholders into the lookups for step
title, step description, data.* labels, data_description.* helpers and
error.* messages, so all of them can be swapped at runtime.

Two constraints the frontend imposes -- both are load-bearing:

  1. MENU steps must have NO "title" key in the JSON. renderMenuHeader()
     is the one renderer that does NOT pass description_placeholders, so
     a "{title}" template there would render the literal text
     "Translation Error ...". Menu headings therefore live in
     "description" (which does take placeholders); the header falls back
     to the integration title, "YidCal".

  2. EVERY {token} present in a step's template MUST be supplied on
     EVERY render of that step, including error re-renders. A missing
     placeholder does not fall back -- intl-messageformat throws and the
     frontend prints "Translation Error ..." in place of the label.
     This is why config_flow.py funnels every async_show_form() through
     a single _form() helper that always attaches placeholders(step).

Adding a language: add it to UI_LANGUAGES + LANGUAGE_NAMES, then add the
key to every dict below. Nothing in strings.json needs to change.
"""

from __future__ import annotations

CONF_UI_LANGUAGE = "ui_language"

#: Fallback when the HA language gives us nothing to go on. Historically
#: translations/en.json held Yiddish, so every non-he / non-en-GB user
#: was already seeing Yiddish -- keep that behaviour for existing installs.
DEFAULT_UI_LANGUAGE = "yi"

UI_LANGUAGES = ("yi", "he", "en")

#: Shown in the language picker itself, so each is written in its own language.
LANGUAGE_NAMES = {
    "yi": "אידיש",
    "he": "עברית",
    "en": "English",
}


def guess_ui_language(hass) -> str:
    """Best-effort default for the language picker / pre-existing entries.

    NOTE: hass.config.language is the *server* language. The frontend
    translates against the *user profile* language, which the backend
    cannot see. So this is only a guess -- it decides the pre-selected
    default, never the final answer. The user always overrides it.
    """
    raw = (getattr(hass.config, "language", None) or "").strip().lower().replace("_", "-")
    if raw.startswith("he") or raw.startswith("iw"):   # iw = legacy Hebrew code
        return "he"
    if raw.startswith("yi") or raw.startswith("ji"):   # ji = legacy Yiddish code
        return "yi"
    if raw.startswith("en-gb"):
        # en-GB.json has always carried the English flow; don't flip those
        # users to Yiddish on upgrade.
        return "en"
    return DEFAULT_UI_LANGUAGE


# ---------------------------------------------------------------------------
# Field labels, helper texts, menu labels, errors.
# Extracted verbatim from the previous translations/{en,he,en-GB}.json --
# no wording changed.
# ---------------------------------------------------------------------------
LABELS = {
    "lbl_is_in_israel": {"yi": "אויב איר זענט אין ארץ ישראל, צייכנט דאס", "he": "אם אתה בישראל, סמן אפשרות זו", "en": "If you live in Israel, check this"},
    "lbl_strip_nikud": {"yi": "נעם אראפ די נְקֻודּוֹת", "he": "הסר ניקוד", "en": "Strip vowel points (nikud) from Hebrew text"},
    "lbl_candlelighting_offset": {"yi": "וויפיל מינוט פארן שקיעה איז הדלקת הנרות", "he": "כמה דקות לפני שקיעה להדלקת נרות", "en": "Minutes before sunset for candle lighting"},
    "lbl_havdalah_offset": {"yi": "וויפיל מינוט נאכן שקיעה איז מוצאי", "he": "כמה דקות אחרי שקיעה להבדלה", "en": "Minutes after sunset for Havdalah"},
    "lbl_tallis_tefilin_offset": {"yi": "וויפיל מינוט נאכן עלות איז טלית ותפילין", "he": "כמה דקות אחרי עלות השחר לטלית ותפילין", "en": "Minutes after dawn for tallis & tefilin"},
    "lbl_korbanos_yud_gimmel_midos": {"yi": "?ליינט מען קרבנות אום שלוש עשרה מדות", "he": "האם יש קריאת קרבנות במנחה של שלוש עשרה מידות?", "en": "Is Korbanos read at Mincha on Shlosh Esrei Middos?"},
    "lbl_mishne_torah_hoshana_rabba": {"yi": "?ליינט מען משנה תורה הושענא רבה ביינאכט", "he": "האם יש קריאת משנה תורה בליל הושענא רבה?", "en": "Is Mishneh Torah read on the night of Hoshana Rabba?"},
    "lbl_day_label_language": {"yi": "Full Display Sensor וויזוי דו ווילסט זעהן דעם טאג ביי די", "he": "כיצד להציג את שם היום בחיישן התצוגה המלאה (Full Display Sensor)", "en": "Day label language for the Full Display sensor"},
    "lbl_haftorah_minhag": {"yi": "הפטרה סענסאר מנהג", "he": "מנהג ההפטרה", "en": "Haftorah sensor minhag"},
    "lbl_parsha_metzora_display": {"yi": "פרשת מצורע אדער פרשת טהרה", "he": "כיצד להציג את פרשת מצורע", "en": "How to display parshas Metzora"},
    "lbl_time_format": {"yi": "צייט־פארמאט (נאר פאר Simple Zmanim)", "he": "פורמט שעה (לזמני Simple Zmanim בלבד)", "en": "Time format (For Simple Zmanim only)"},
    "lbl_include_date": {"yi": "Full Display Sensor צולייגען די דעיט - ג׳ תמוז תשפ״ה צום", "he": "להוסיף את התאריך (לדוגמה: ג׳ תמוז תשפ״ה) לחיישן התצוגה המלאה (Full Display Sensor)", "en": "Include Hebrew date on the Full Display sensor"},
    "lbl_include_attribute_sensors": {"yi": "צולייגען באזונדערע סענסאָרס פאר די ימים טובים", "he": "יצירת חיישני־משנה נפרדים עבור ימי החג", "en": "Create separate sensors for holiday attributes"},
    "lbl_include_sefirah_short_in_full": {"yi": "Full Display Sensor צולייגען די קורצע ספירת העומר - ח' בעומר צום", "he": "הוספת ספירת העומר (קצר) לחיישן Full Display", "en": "Include short Sefiras HaOmer count on the Full Display sensor"},
    "lbl_enable_multiday_candles": {"yi": "צולייגען באזונדערע הדלקת הנרות סענסאָרס פאר 2טע/3טע נאכט", "he": "יצירת חיישני הדלקת נרות נפרדים עבור לילה 2/לילה 3", "en": "Create separate candle lighting sensors for Night 2/Night 3"},
    "lbl_enable_daf_hayomi": {"yi": "צולייגען דף היומי סענסאר", "he": "הוספת חיישן דף היומי", "en": "Create Daf HaYomi sensor"},
    "lbl_slichos_label_rollover": {"yi": "ווען זאל זיך די סליחות טאג טוישן", "he": "מתי מתחלף יום הסליחות", "en": "When should the Selichos label roll over"},
    "lbl_kiddush_levana_start": {"yi": "ווען הייבט זיך אן קידוש לבנה - ג' אדער ז' שלימים", "he": "מתי מתחיל זמן קידוש לבנה - ג' או ז' שלמים", "en": "When does Kiddush Levana begin - 3 (Gimmel) or 7 (Zayin) Shleimim"},
    "lbl_upcoming_lookahead_days": {"yi": "Upcoming Holiday Sensor וויפיל טעג פאראויס זאל קוקן די", "he": "כמה ימים קדימה יחפש חיישן 'החג הקרוב' (Upcoming Holiday Sensor)", "en": "Days to look ahead in the Upcoming Holiday sensor"},
    "lbl_enable_zmanim_lookup": {"yi": "צולייגען די זמנים Lookup & service call sensors", "he": "הוספת חיישן Zmanim Lookup ושירות yidcal.check_zmanim", "en": "Create the Zmanim Lookup sensor and yidcal.check_zmanim service"},
    "lbl_enable_luach_pdf": {"yi": "צולייגען די לוח PDF סערוויס", "he": "הוספת שירות יצירת לוח (PDF)", "en": "Create the Generate Luach (PDF) service"},
    "lbl_enable_yurtzeit_daily": {"yi": "צולייגען די טעגליכע יארצייטן סענסאר", "he": "הפעלת חיישן יארצייט יומי", "en": "Create daily Yahrtzeit sensor"},
    "lbl_enable_weekly_yurtzeit": {"yi": "צולייגען די וועכנטליכע יארצייטן סענסאר", "he": "הפעלת חיישן יארצייט שבועי", "en": "Create weekly Yahrtzeit sensor"},
    "lbl_yurtzeit_databases": {"yi": "וועלכע יארצייטן דאטאבעיס(ן) צו ניצן", "he": "באילו מאגרי יארצייטים להשתמש", "en": "Which Yahrtzeit database(s) to use"},
    "lbl_enable_early_shabbos": {"yi": "עקטיוועט פריער שבת", "he": "הפעלת שבת מוקדמת", "en": "Enable early Shabbos"},
    "lbl_early_shabbos_mode": {"yi": "וויזוי צו רעכענען פריער שבת", "he": "אופן חישוב זמן תחילת שבת מוקדמת", "en": "How to calculate early Shabbos"},
    "lbl_early_shabbos_plag_method": {"yi": "וועלכן פּלג־שיטה צו נוצן", "he": "איזו שיטת פלג המנחה להשתמש", "en": "Which Plag method to use"},
    "lbl_early_shabbos_fixed_time": {"yi": "א געוויסע צייט פאר פריער שבת", "he": "שעת שעון קבועה לשבת מוקדמת", "en": "Fixed clock time for early Shabbos"},
    "lbl_early_shabbos_apply_rule": {"yi": "ווען זאל פריער שבת ווערן אפלייעד", "he": "מתי להחיל שבת מוקדמת", "en": "When should early Shabbos apply"},
    "lbl_early_shabbos_sunset_after": {"yi": "נאר ווען פרייטאג שקיעה איז נאך …", "he": "רק כאשר שקיעת יום שישי אחרי…", "en": "Only when Friday sunset is after…"},
    "lbl_enable_early_yomtov": {"yi": "עקטיוועט פריער יום טוב", "he": "הפעלת יום טוב מוקדם", "en": "Enable early Yom Tov"},
    "lbl_early_yomtov_mode": {"yi": "וויזוי צו רעכענען פריער יום טוב", "he": "אופן חישוב זמן תחילת יום טוב מוקדם", "en": "How to calculate early Yom Tov"},
    "lbl_early_yomtov_plag_method": {"yi": "וועלכן פּלג־שיטה צו נוצן", "he": "איזו שיטת פלג המנחה להשתמש", "en": "Which Plag method to use"},
    "lbl_early_yomtov_fixed_time": {"yi": "א געוויסע צייט פאר פריער יום טוב", "he": "שעת שעון קבועה ליום טוב מוקדם", "en": "Fixed clock time for early Yom Tov"},
    "lbl_early_yomtov_include": {"yi": "אויף וועלכע ימים טובים מעג מען פריער אננעמען", "he": "על אילו חגים להחיל יום טוב מוקדם", "en": "Which Yomim Tovim allow early acceptance"},
    "lbl_early_yomtov_allow_second_days": {"yi": "ערלויבן פריער אננעמען אויך אויף צווייטע טעג יום טוב", "he": "לאפשר יום טוב מוקדם גם ביום השני (בחו״ל)", "en": "Allow early acceptance on second days of Yom Tov"},
    "dsc_enable_multiday_candles": {"yi": "ווען דאס איז אנגעצינדן, בלייבט דער זמן ערב סענסאר אויף די ערשטע נאכט, און טוישט זיך הערשט 12 ביינאכט מוצאי.", "he": "כאשר מופעל, חיישן זמן ערב הופך לסטטי (לילה ראשון בלבד) ומתעדכן רק ב-12:00 AM במוצאי", "en": "When enabled, the Zman Erev sensor becomes static (Night 1 only) and only advances at 12:00 AM on Motzi night"},
    "dsc_enable_zmanim_lookup": {"yi": "צולייגען א sensor.yidcal_zmanim_lookup מיט א סערוויס yidcal.check_zmanim וואס מען קען רופן מיט א דאטום און עס וועט ווייזן די זמנים פון דעם טאג.", "he": "מוסיף את החיישן sensor.yidcal_zmanim_lookup ואת השירות yidcal.check_zmanim. ניתן להפעיל את השירות עם כל תאריך (עד ±100 שנים) והחיישן יתמלא בזמנים של אותו יום ובתווית יום עברית.", "en": "Adds sensor.yidcal_zmanim_lookup plus the yidcal.check_zmanim service. Call the service with any date (±100 years) and the sensor will populate with that day's zmanim and a Hebrew day label."},
    "dsc_enable_luach_pdf": {"yi": "לייגט צו די 'yidcal.generate_luach' סערוויס, וואס מאכט א לוח (PDF) פאר א געוויסע צייט און לייגט עס אריין אלץ א /config/www/yidcal-data/.", "he": "מוסיף את השירות yidcal.generate_luach שיוצר לוח להדפסה (PDF) עבור טווח תאריכים, ושומר אותו תחת /config/www/yidcal-data/.", "en": "Adds the yidcal.generate_luach service, which creates a printable luach (PDF) for a given date range and writes it under /config/www/yidcal-data/."},
    "menu_general": {"yi": "אלגעמיינע סעטינגס", "he": "הגדרות כלליות", "en": "General settings"},
    "menu_yurtzeit": {"yi": "יארצייטן סענסאָרס", "he": "חיישני יארצייט", "en": "Yahrtzeit sensors"},
    "menu_early": {"yi": "פריער שבת / יום טוב", "he": "שבת / יום טוב מוקדמים", "en": "Early Shabbos / Yom Tov"},
    "menu_early_shabbos": {"yi": "פריער שבת", "he": "שבת מוקדמת", "en": "Early Shabbos"},
    "menu_early_yomtov": {"yi": "פריער יום טוב", "he": "יום טוב מוקדם", "en": "Early Yom Tov"},
    "err_select_db_required": {"yi": "אויב איר סעלעקט א יארצייט־סענסאר, דארפט איר אויסוועלן כאטש איין דאטאבעיס", "he": "אם הופעל חיישן יומי או שבועי – יש לבחור לפחות מאגר אחד", "en": "If you enable a Yahrtzeit sensor, you must select at least one database."},
}


# ---------------------------------------------------------------------------
# Step titles. Reuse the menu wording so a step's header matches the row
# that opened it.
# ---------------------------------------------------------------------------
TITLES = {
    "general": LABELS["menu_general"],
    "yurtzeit": LABELS["menu_yurtzeit"],
    "early_shabbos_yt": LABELS["menu_early"],
    "early_shabbos": LABELS["menu_early_shabbos"],
    "early_yomtov": LABELS["menu_early_yomtov"],
}

#: Menu step headings (menus have no title -- see module docstring).
MENU_DESC = {
    "language": {
        "yi": "**קלייבט אויס א שפראך פאר די סעטינגס**",
        "he": "**בחר שפה עבור מסך ההגדרות**",
        "en": "**Choose the language for these settings**",
    },
    "init": {"yi": "", "he": "", "en": ""},
    "early_shabbos_yt": TITLES["early_shabbos_yt"],
}

#: Label for the new language row in the options menu.
MENU_LANGUAGE = {
    "yi": "שפראך",
    "he": "שפה",
    "en": "Language",
}

#: Unit shown on the upcoming_lookahead_days slider.
UNIT_DAYS = {"yi": "טעג", "he": "ימים", "en": "days"}


# ---------------------------------------------------------------------------
# Selector option labels.
#
# These were hard-coded single-language in config_flow.py. The Hebrew /
# Yiddish wording below is carried over verbatim; only the English column
# is new.
#
# >>> TODO(Yoel): the blocks marked NEEDS-YI-HE are English-only today.
# >>> Their yi/he columns currently repeat the English string, so nothing
# >>> changes until you fill them in. Naming is yours, not mine.
# ---------------------------------------------------------------------------
def _same(text: str) -> dict[str, str]:
    """Same string in every language (placeholder until translated)."""
    return {"yi": text, "he": text, "en": text}


SELECTORS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "day_label_language": [
        ("yiddish", {"yi": "זונטאג, מאנטאג", "he": "זונטאג, מאנטאג", "en": "Yiddish — זונטאג, מאנטאג"}),
        ("hebrew",  {"yi": "יום א', יום ב", "he": "יום א', יום ב", "en": "Hebrew — יום א', יום ב"}),
    ],
    "haftorah_minhag": [
        ("ashkenazi", {"yi": "אשכנזי", "he": "אשכנזי", "en": "Ashkenazi"}),
        ("sephardi",  {"yi": "ספרדי", "he": "ספרדי", "en": "Sephardi"}),
    ],
    "parsha_metzora_display": [
        ("metzora", {"yi": "מצורע", "he": "מצורע", "en": "Metzora (מצורע)"}),
        ("tahara",  {"yi": "טהרה", "he": "טהרה", "en": "Tahara (טהרה)"}),
    ],
    "slichos_label_rollover": [
        ("havdalah", {"yi": "זמן הבדלה", "he": "זמן הבדלה", "en": "Havdalah time"}),
        ("midnight", {"yi": "12 AM", "he": "12 AM", "en": "Midnight (12 AM)"}),
    ],
    "kiddush_levana_start": [
        ("gimmel", {"yi": "ג' שלימים", "he": "ג' שלימים", "en": "3 (Gimmel) Shleimim"}),
        ("zayin",  {"yi": "ז' שלימים", "he": "ז' שלימים", "en": "7 (Zayin) Shleimim"}),
    ],

    # ---- NEEDS-YI-HE --------------------------------------------------
    "time_format": [
        ("12", _same("12-hour (AM/PM)")),
        ("24", _same("24-hour")),
    ],
    "yurtzeit_databases": [
        ("standard", _same("Standard")),
        ("satmar",   _same("Satmar")),
    ],
    "early_mode": [
        ("plag",     _same("By Plag Hamincha (weekly)")),
        ("fixed",    _same("Fixed time (clock)")),
        ("disabled", _same("Disabled (manual only)")),
    ],
    "early_plag_method": [
        ("gra", _same("GRA (default)")),
        ("ma",  _same("Magen Avraham (advanced)")),
    ],
    "early_shabbos_apply_rule": [
        ("every_friday", _same("Every Friday")),
        ("sunset_after", _same("Only when sunset is after…")),
    ],
    "early_yomtov_include": [
        ("rosh_hashana",     _same("Rosh Hashana (Day 1 only)")),
        ("yom_kippur",       _same("Yom Kippur")),
        ("sukkos",           _same("Sukkos (Day 1 only)")),
        ("shemini_atzeres",  _same("Shemini Atzeres (first day only)")),
        ("pesach_last_days", _same("Last days of Pesach (Shvi'i only)")),
        ("pesach_first_day", _same("Pesach Day 1 (accept early; seder at night)")),
        ("shavuos",          _same("Shavuos (advanced)")),
    ],
    # -------------------------------------------------------------------
}


# ---------------------------------------------------------------------------
# Which fields live on which step. strings.json is GENERATED from this map
# (tools/build_flow_strings.py), so the templates and the placeholders can
# never drift apart.
# ---------------------------------------------------------------------------
_GENERAL_FIELDS = [
    "is_in_israel", "strip_nikud", "candlelighting_offset", "havdalah_offset",
    "tallis_tefilin_offset", "korbanos_yud_gimmel_midos", "mishne_torah_hoshana_rabba",
    "day_label_language", "haftorah_minhag", "parsha_metzora_display", "time_format",
    "include_date", "include_attribute_sensors", "include_sefirah_short_in_full",
    "enable_multiday_candles", "enable_daf_hayomi", "slichos_label_rollover",
    "kiddush_levana_start", "upcoming_lookahead_days", "enable_zmanim_lookup",
]
_YURTZEIT_FIELDS = ["enable_yurtzeit_daily", "enable_weekly_yurtzeit", "yurtzeit_databases"]

#: Every error token, attached to every form step so an error re-render can
#: never be missing its placeholder.
_ERROR_TOKENS = ["err_select_db_required"]

STEPS: dict[str, dict] = {
    # --- config flow ---
    "config.user":     {"kind": "menu", "menu": "language"},
    "config.general":  {"kind": "form", "title": "general",
                        "fields": _GENERAL_FIELDS + ["enable_luach_pdf"],
                        "descs": ["enable_multiday_candles", "enable_zmanim_lookup",
                                  "enable_luach_pdf"]},
    "config.yurtzeit": {"kind": "form", "title": "yurtzeit",
                        "fields": _YURTZEIT_FIELDS, "descs": []},

    # --- options flow ---
    "options.init":     {"kind": "menu", "menu": "init"},
    "options.language": {"kind": "menu", "menu": "language"},
    "options.general":  {"kind": "form", "title": "general",
                         "fields": _GENERAL_FIELDS + ["enable_luach_pdf"],
                         "descs": ["enable_multiday_candles", "enable_zmanim_lookup",
                                   "enable_luach_pdf"]},
    "options.yurtzeit": {"kind": "form", "title": "yurtzeit",
                         "fields": _YURTZEIT_FIELDS, "descs": []},
    "options.early_shabbos_yt": {"kind": "menu", "menu": "early_shabbos_yt"},
    "options.early_shabbos": {"kind": "form", "title": "early_shabbos",
                              "fields": ["enable_early_shabbos", "early_shabbos_mode",
                                         "early_shabbos_plag_method", "early_shabbos_fixed_time",
                                         "early_shabbos_apply_rule", "early_shabbos_sunset_after"],
                              "descs": []},
    "options.early_yomtov": {"kind": "form", "title": "early_yomtov",
                             "fields": ["enable_early_yomtov", "early_yomtov_mode",
                                        "early_yomtov_plag_method", "early_yomtov_fixed_time",
                                        "early_yomtov_include", "early_yomtov_allow_second_days"],
                             "descs": []},
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def _pick(table: dict[str, str], lang: str) -> str:
    return table.get(lang) or table.get(DEFAULT_UI_LANGUAGE) or ""


def t(token: str, lang: str) -> str:
    """One string, in `lang`."""
    return _pick(LABELS[token], lang)


def sel(key: str, lang: str) -> list[dict[str, str]]:
    """Selector options with explicit labels (bypasses HA translations)."""
    return [{"value": v, "label": _pick(lbl, lang)} for v, lbl in SELECTORS[key]]


def unit_days(lang: str) -> str:
    return _pick(UNIT_DAYS, lang)


def menu_language(lang: str) -> str:
    """Label for the Language row in the options menu."""
    return _pick(MENU_LANGUAGE, lang)


def placeholders(step: str, lang: str) -> dict[str, str]:
    """EVERY token in `step`'s template. Must never omit one."""
    spec = STEPS[step]
    out: dict[str, str] = {}

    if spec["kind"] == "menu":
        out["desc"] = _pick(MENU_DESC[spec["menu"]], lang)
        return out

    out["title"] = _pick(TITLES[spec["title"]], lang) if spec.get("title") else ""
    out["desc"] = ""
    for f in spec["fields"]:
        out[f"lbl_{f}"] = t(f"lbl_{f}", lang)
    for f in spec["descs"]:
        out[f"dsc_{f}"] = t(f"dsc_{f}", lang)
    for e in _ERROR_TOKENS:
        out[e] = t(e, lang)
    return out


def menu_labels(keys: dict[str, str], lang: str) -> dict[str, str]:
    """{step_id: token} -> {step_id: localized label}. Dict menu_options are
    used verbatim by the frontend, so no translation keys are involved."""
    return {step_id: t(token, lang) for step_id, token in keys.items()}
