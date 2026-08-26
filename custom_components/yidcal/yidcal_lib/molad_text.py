"""
custom_components/yidcal/yidcal_lib/molad_text.py

The Molad announcement, written out in Yiddish, Hebrew and English.

``sensor.yidcal_molad`` used to build its Yiddish sentence inline. It now
publishes all three renderings — the configured language as the state,
the other two as the ``English`` / ``Hebrew`` attributes — and the Shabbos
Mevorchim calendar quotes the same sentence in its event description. Three
call sites means the wording lives here rather than being copied.

Nothing in here computes a molad. The caller does that (via
``YidCalHelper.get_actual_molad``) and hands over the pieces: the weekday
in English, the announcement digits, and a *time-of-day key* rather than a
label, so the Jerusalem-clock rule that picks the bucket stays where it
already is and only the wording is decided here.
"""
from __future__ import annotations

# Time-of-day buckets. The keys are what sensor.py's Jerusalem-clock rule
# returns; the values are that bucket said in each language.
#
# ``motzash`` and ``friday_night`` are not buckets so much as overrides:
# a molad after havdalah on Shabbos is announced as מוצש״ק with no
# time-of-day at all, and Friday after tzeis is צונאכטס. Both are carried
# here so every language phrases them the same way.
TOD_DAWN = "dawn"
TOD_MORNING = "morning"
TOD_LATE_MORNING = "late_morning"
TOD_AFTERNOON = "afternoon"
TOD_NIGHT = "night"
TOD_FRIDAY_NIGHT = "friday_night"
TOD_MOTZASH = "motzash"
#: A molad in the small hours belongs to the night of the PREVIOUS
#: day; the luach anchors "אור ל" on the CURRENT civil day for these.
TOD_NIGHT_ENTERING = "night_entering"
#: The plag-hamincha-GR״A → tzeis-R״ת window.
TOD_EVENING = "evening"

_TOD_LABELS: dict[str, dict[str, str]] = {
    TOD_DAWN:         {"yiddish": "פארטאגס",  "hebrew": "לפנות בוקר",  "english": "before dawn"},
    TOD_MORNING:      {"yiddish": "אינדערפרי", "hebrew": "בבוקר",       "english": "in the morning"},
    TOD_LATE_MORNING: {"yiddish": "פארמיטאג",  "hebrew": "לפני הצהריים", "english": "late morning"},
    TOD_AFTERNOON:    {"yiddish": "נאכמיטאג",  "hebrew": "אחר הצהריים",  "english": "in the afternoon"},
    TOD_NIGHT:        {"yiddish": "ביינאכט",   "hebrew": "בלילה",       "english": "at night"},
    # KJ 5784 prints ביינאכט for BOTH Sivan 1:25 AM (night-entering)
    # and Teves 8:01 PM (night); only the luach day-label differs.
    TOD_NIGHT_ENTERING: {"yiddish": "ביינאכט", "hebrew": "בלילה",      "english": "at night"},
    TOD_EVENING:      {"yiddish": "פארנאכטס", "hebrew": "לפנות ערב",   "english": "toward evening"},
    TOD_FRIDAY_NIGHT: {"yiddish": "צונאכטס",   "hebrew": "בלילה",       "english": "at night"},
    # motzash carries no time-of-day — the day name already says it.
    TOD_MOTZASH:      {"yiddish": "",          "hebrew": "",            "english": ""},
}

#: English weekday (as produced by ``YidCalHelper.get_day_of_week``, which
#: substitutes "Shabbos" for Saturday) → that day in each language.
_DAY_LABELS: dict[str, dict[str, str]] = {
    "Sunday":    {"yiddish": "זונטאג",     "hebrew": "יום ראשון", "english": "Sunday"},
    "Monday":    {"yiddish": "מאנטאג",     "hebrew": "יום שני",   "english": "Monday"},
    "Tuesday":   {"yiddish": "דינסטאג",    "hebrew": "יום שלישי", "english": "Tuesday"},
    "Wednesday": {"yiddish": "מיטוואך",    "hebrew": "יום רביעי", "english": "Wednesday"},
    "Thursday":  {"yiddish": "דאנערשטאג",  "hebrew": "יום חמישי", "english": "Thursday"},
    "Friday":    {"yiddish": "פרייטאג",    "hebrew": "יום שישי",  "english": "Friday"},
    "Shabbos":   {"yiddish": "שבת",        "hebrew": "שבת קודש",  "english": "Shabbos"},
}

#: The Motzei-Shabbos override, which replaces the day name outright.
_MOTZASH_DAY = {
    "yiddish": 'מוצש"ק',
    "hebrew": "מוצאי שבת",
    "english": "Motzei Shabbos",
}

LANGUAGES = ("yiddish", "hebrew", "english")


def day_label(day_english: str, language: str) -> str:
    """One weekday name, in ``language``. Unknown days pass through."""
    return _DAY_LABELS.get(day_english, {}).get(language, day_english)


def tod_label(tod_key: str, language: str) -> str:
    """One time-of-day bucket, in ``language``."""
    return _TOD_LABELS.get(tod_key, {}).get(language, "")


def _chalakim_phrase(chalakim: int, language: str) -> str:
    if chalakim == 0:
        return ""
    if language == "yiddish":
        return f" און {chalakim} {'חלק' if chalakim == 1 else 'חלקים'}"
    if language == "hebrew":
        return f" ו-{chalakim} {'חלק' if chalakim == 1 else 'חלקים'}"
    return f" and {chalakim} {'chelek' if chalakim == 1 else 'chalakim'}"


def _join(parts: list[str], language: str) -> str:
    """Join Rosh Chodesh weekday names the way each language does."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    sep = {"yiddish": " און ", "hebrew": " ו", "english": " & "}[language]
    if language == "hebrew":
        # "יום ראשון ויום שני" — the vav attaches to the next word.
        return parts[0] + "".join(f"{sep}{p}" for p in parts[1:])
    return sep.join(parts)


def molad_body(
    *,
    day_english: str,
    hours: int,
    minutes: int,
    chalakim: int,
    tod_key: str,
    language: str,
) -> str:
    """The molad itself, without the leading 'מולד' / 'Molad'.

    e.g. ``'מאנטאג אינדערפרי, 45 מינוט און 3 חלקים נאך 9'``.
    """
    chal = _chalakim_phrase(chalakim, language)
    if tod_key == TOD_MOTZASH:
        day = _MOTZASH_DAY[language]
        tod = ""
    else:
        day = day_label(day_english, language)
        tod = tod_label(tod_key, language)
    head = f"{day} {tod}".strip()

    if language == "yiddish":
        return f"{head}, {minutes} מינוט{chal} נאך {hours}"
    if language == "hebrew":
        return f"{head}, {minutes} דקות{chal} אחרי {hours}"
    return f"{head}, {minutes} minutes{chal} after {hours}"


def molad_sentence(
    *,
    day_english: str,
    hours: int,
    minutes: int,
    chalakim: int,
    tod_key: str,
    language: str,
) -> str:
    """The headline molad line — what the sensor publishes as its state."""
    body = molad_body(
        day_english=day_english,
        hours=hours,
        minutes=minutes,
        chalakim=chalakim,
        tod_key=tod_key,
        language=language,
    )
    prefix = "Molad" if language == "english" else "מולד"
    return f"{prefix} {body}"


def full_molad_sentence(
    *,
    day_english: str,
    hours: int,
    minutes: int,
    chalakim: int,
    tod_key: str,
    month_hebrew: str,
    month_english: str,
    rosh_chodesh_days_english: list[str],
    language: str,
) -> str:
    """The long form: which month's molad it is, and when Rosh Chodesh falls."""
    body = molad_body(
        day_english=day_english,
        hours=hours,
        minutes=minutes,
        chalakim=chalakim,
        tod_key=tod_key,
        language=language,
    )
    rc = _join(
        [day_label(d, language) for d in (rosh_chodesh_days_english or [])],
        language,
    )
    if language == "english":
        head = f"The Molad of {month_english} will be: {body}"
        return f"{head} - Rosh Chodesh, {rc}" if rc else head
    month = month_hebrew
    head = f"מולד חודש {month} יהיה: {body}"
    if not rc:
        return head
    return f"{head} - ראש חודש, {rc}"


def all_languages(
    *,
    day_english: str,
    hours: int,
    minutes: int,
    chalakim: int,
    tod_key: str,
    month_hebrew: str,
    month_english: str,
    rosh_chodesh_days_english: list[str],
) -> dict[str, dict[str, str]]:
    """``{language: {'short': …, 'full': …}}`` for all three languages."""
    out: dict[str, dict[str, str]] = {}
    for language in LANGUAGES:
        out[language] = {
            "short": molad_sentence(
                day_english=day_english,
                hours=hours,
                minutes=minutes,
                chalakim=chalakim,
                tod_key=tod_key,
                language=language,
            ),
            "full": full_molad_sentence(
                day_english=day_english,
                hours=hours,
                minutes=minutes,
                chalakim=chalakim,
                tod_key=tod_key,
                month_hebrew=month_hebrew,
                month_english=month_english,
                rosh_chodesh_days_english=rosh_chodesh_days_english,
                language=language,
            ),
        }
    return out
