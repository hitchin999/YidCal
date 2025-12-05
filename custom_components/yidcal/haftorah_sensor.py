from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval, async_track_time_change

from pyluach.dates import GregorianDate, HebrewDate
import pyluach.hebrewcal as hebrewcal
from pyluach.hebrewcal import Year as HebrewYear
import pyluach.parshios as parshios

from .const import DOMAIN
from .device import YidCalDisplayDevice
from .zman_sensors import get_geo
from zmanim.zmanim_calendar import ZmanimCalendar

_LOGGER = logging.getLogger(__name__)

CONF_HAFTORAH_MINHAG = "haftorah_minhag"  # "ashkenazi" | "sephardi"
DEFAULT_HAFTORAH_MINHAG = "ashkenazi"

def _data_path(filename: str) -> Path:
    here = Path(__file__).resolve().parent
    return here / "data" / filename

# Load data once at module import (before async event loop starts)
_COMPILED_DATA_PATH = _data_path("haftorah.json")
_COMPILED_DATA: dict[str, Any] | None = None

def _load_compiled_sync() -> dict[str, Any]:
    """Load the compiled JSON file synchronously."""
    global _COMPILED_DATA
    if _COMPILED_DATA is None:
        _COMPILED_DATA = json.loads(_COMPILED_DATA_PATH.read_text(encoding="utf-8"))
    return _COMPILED_DATA


def _get_compiled_data() -> dict[str, Any]:
    """Get the compiled data, loading it if necessary."""
    return _load_compiled_sync()


def _prev_or_same_shabbos(d: date) -> date:
    return d - timedelta(days=(d.weekday() - 5) % 7)  # Saturday=5


def _prev_shabbos_strict(d: date) -> date:
    s = _prev_or_same_shabbos(d)
    return s if s < d else (s - timedelta(days=7))


def _next_shabbos_from(d: date) -> date:
    return d + timedelta(days=(5 - d.weekday()) % 7)


def _to_pydate(gd: GregorianDate) -> date:
    return gd.to_pydate()


def _greg_from_pydate(d: date) -> GregorianDate:
    return GregorianDate(d.year, d.month, d.day)


def _is_rosh_chodesh(hd: HebrewDate) -> bool:
    return hd.day in (1, 30)


def _determine_shabbos_target(
    now_local: datetime,
    geo,
    candle_offset: int,
    havdalah_offset: int,
) -> tuple[date, bool]:
    """
    If inside Shabbos window (Fri candle -> Sat havdalah) => return THIS Shabbos.
    Else => return upcoming Shabbos.
    """
    today = now_local.date()
    wd = now_local.weekday()
    tz = now_local.tzinfo

    in_window = False
    shabbos_date = _next_shabbos_from(today)

    if wd in (4, 5):  # Fri/Sat
        friday = today if wd == 4 else (today - timedelta(days=1))
        saturday = friday + timedelta(days=1)

        fri_sunset = ZmanimCalendar(geo_location=geo, date=friday).sunset().astimezone(tz)
        sat_sunset = ZmanimCalendar(geo_location=geo, date=saturday).sunset().astimezone(tz)

        candle = fri_sunset - timedelta(minutes=candle_offset)
        havdalah = sat_sunset + timedelta(minutes=havdalah_offset)

        if candle <= now_local < havdalah:
            in_window = True
            shabbos_date = saturday
            
    # Keep showing "this week's" Haftorah all day Saturday.
    # Switch to next week's automatically at 00:00 Sunday.
    if wd == 5:
        shabbos_date = today

    return shabbos_date, in_window


@dataclass(frozen=True)
class HaftorahResolved:
    haftarah_id: str
    display_name: str
    full_name: str
    variants: dict[str, str] | None
    source_ref: str
    reason: str
    notes: str | None
    extra: dict[str, Any]


class HaftorahResolver:
    """
    Uses ONE compiled JSON: haftorah.json

    Expected structure (loose):
      - haftarot_catalog (or haftarot_list): dict of haftarah_id -> info
      - holidays/special rules: keys for special_shabbatot / holidays
      - weekly_rules or equivalent: parsha_key -> haftarah_id mapping
      - parsha mapping helpers (optional)
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        # Use provided data or load from cache
        self.data = data if data is not None else _get_compiled_data()

        # Catalog
        self.catalog = (
            self.data.get("haftarot_catalog")
            or self.data.get("haftarot_list")
            or {}
        )

        # Weekly rules (list or dict)
        self.weekly_rules = self.data.get("weekly_rules") or {}
        if isinstance(self.weekly_rules, list):
            # normalize to suffix -> rule
            self._rule_by_suffix: dict[str, dict[str, Any]] = {}
            for r in self.weekly_rules:
                pk = r.get("parsha_key", "")
                if not pk:
                    continue
                parts = pk.split("_")
                suffix = "_".join(parts[1:]) if len(parts) > 1 else pk
                self._rule_by_suffix[suffix] = r
        elif isinstance(self.weekly_rules, dict):
            # allow direct mapping suffix-> {haftarah_id:...} or suffix->id
            self._rule_by_suffix = dict(self.weekly_rules)
        else:
            self._rule_by_suffix = {}

        # Special/holiday table (your compiled JSON uses "holiday_haftarot")
        self.hol = (
            self.data.get("holiday_haftarot")
            or self.data.get("holidays")
            or self.data.get("haftarot_holidays_complete")
            or self.data
        )

        # Parsha normalization map
        self._parsha_norm_to_slug = self._build_parsha_name_map()
        self._validate_parsha_map()

    def _validate_parsha_map(self) -> None:
        def norm(s: str) -> str:
            return (
                s.lower()
                .replace("'", "")
                .replace("-", " ")
                .replace("–", " ")
                .strip()
                .replace(" ", "")
            )
        missing = [nm for nm in parshios.PARSHIOS if norm(nm) not in self._parsha_norm_to_slug]
        if missing:
            _LOGGER.warning("Haftorah parsha slug map missing for: %s", missing)

    def _build_parsha_name_map(self) -> dict[str, str]:
        def n(s: str) -> str:
            return (
                s.lower()
                .replace("'", "'")
                .replace("'", "")
                .replace("-", " ")
                .replace("–", " ")
                .strip()
                .replace(" ", "")
            )

        # Keep internal slugs matching *your* parsha_key suffixes in the compiled file.
        mapping = {
            "bereishis": "bereishit",
            "noach": "noach",
            "lechlecha": "lech_lecha",
            "vayeira": "vayeira",
            "chayeisarah": "chayei_sara",
            "toldos": "toldot",
            "vayeitzei": "vayetzei",
            "vayishlach": "vayishlach",
            "vayeishev": "vayeshev",
            "miketz": "miketz",
            "mikeitz": "miketz",
            "vayigash": "vayigash",
            "vayechi": "vayechi",
            "shemos": "shemot",
            "vaeira": "vaera",
            "bo": "bo",
            "beshalach": "beshalach",
            "yisro": "yitro",
            "mishpatim": "mishpatim",
            "terumah": "terumah",
            "tetzaveh": "tetzaveh",
            "kisisa": "ki_tisa", 
            "vayakhel": "vayakhel",
            "pekudei": "pekudei",
            "vayikra": "vayikra",
            "tzav": "tzav",
            "shemini": "shemini",
            "tazria": "tazria",
            "metzora": "metzora",
            "achareimos": "acharei_mot", 
            "kedoshim": "kedoshim",
            "emor": "emor",
            "behar": "behar",
            "bechukosai": "bechukotai",
            "bamidbar": "bamidbar",
            "nasso": "naso", 
            "behaaloscha": "behaalotecha",
            "shelach": "shelach", 
            "korach": "korach",
            "chukas": "chukat",
            "balak": "balak",
            "pinchas": "pinchas",
            "mattos": "matot",
            "masei": "masei",
            "devarim": "devarim",
            "vaeschanan": "vaetchanan",
            "eikev": "eikev",
            "reeh": "reeh",
            "shoftim": "shoftim",
            "kiseitzei": "ki_tetzei",
            "kisavo": "ki_tavo", 
            "nitzavim": "nitzavim",
            "vayeilech": "vayelech",
            "haazinu": "haazinu",
            "vezoshaberachah": "vezot_haberachah",
        }
        return {n(k): v for k, v in mapping.items()}

    def _catalog_entry(self, haftarah_id: str) -> dict[str, Any] | None:
        # catalog keys might be "1"..."77" or ints etc
        return self.catalog.get(str(haftarah_id)) or self.catalog.get(haftarah_id)

    def _pick_name(self, ent: dict[str, Any], minhag: str) -> tuple[str, str, dict[str, str] | None]:
        full_name = ent.get("name_hebrew") or ent.get("name") or ent.get("title") or ""
        variants = ent.get("minhag")
        if isinstance(variants, dict) and minhag in variants:
            return str(variants[minhag]), str(full_name), {k: str(v) for k, v in variants.items()}
        return str(full_name), str(full_name), ({k: str(v) for k, v in variants.items()} if isinstance(variants, dict) else None)

    def _lookup_special(self, path: list[str]) -> dict[str, Any] | None:
        cur: Any = self.hol
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                return None
            cur = cur[k]
        return cur if isinstance(cur, dict) else None

    def _resolve_festival(self, shabbos: date, israel: bool) -> tuple[str | None, str | None, dict[str, Any]]:
        gd = _greg_from_pydate(shabbos)
        hd = gd.to_heb()
        fest = hebrewcal.festival(gd, israel=israel, hebrew=False, include_working_days=True)
        extra: dict[str, Any] = {"festival": fest or ""}

        # Chanuka (your JSON uses: special_shabbatot -> shabbat_chanukah -> first_shabbat/second_shabbat)
        if fest == "Chanuka":
            hy = hd.year
            start_g = _to_pydate(HebrewDate(hy, 9, 25).to_greg())  # 25 Kislev
            s1 = _next_shabbos_from(start_g)
            which = "first" if shabbos == s1 else "second"
            extra["chanuka_shabbos"] = which

            chan = self._lookup_special(["special_shabbatot", "shabbat_chanukah"])
            if isinstance(chan, dict):
                node = chan.get("first_shabbat") if which == "first" else chan.get("second_shabbat")
                if not node and which == "second":
                    node = chan.get("first_shabbat")  # safety fallback
                if isinstance(node, dict) and "id" in node:
                    return str(node["id"]), f"chanuka:{which}", extra

        # try a generic lookup table inside compiled file if it exists
        # (many compiled versions store these under holidays.<n>.<variant>)
        if fest:
            # Common patterns in compiled data:
            # holidays.pesach.day_1.id  etc.
            # holidays.rosh_hashana.day_1.id etc.
            by = self.data.get("holidays_by_festival")
            if isinstance(by, dict):
                # user may have built a direct dict
                hit = by.get(fest)
                if isinstance(hit, dict) and "id" in hit:
                    return str(hit["id"]), f"festival:{fest}", extra

        # If your compiled file followed the earlier structure:
        if fest == "Rosh Hashana":
            if hd.month == 7 and hd.day == 1:
                ent = self._lookup_special(["rosh_hashana", "day_1"])
                if ent and "id" in ent:
                    return str(ent["id"]), "rosh_hashana:day_1", extra
            if hd.month == 7 and hd.day == 2:
                ent = self._lookup_special(["rosh_hashana", "day_2"])
                if ent and "id" in ent:
                    return str(ent["id"]), "rosh_hashana:day_2", extra

        if fest == "Yom Kippur":
            ent = self._lookup_special(["yom_kippur", "morning"])
            if ent and "id" in ent:
                return str(ent["id"]), "yom_kippur", extra

        if fest == "Shavuos":
            if hd.month == 3 and hd.day == 6:
                ent = self._lookup_special(["shavuot", "day_1"])
                if ent and "id" in ent:
                    return str(ent["id"]), "shavuot:day_1", extra
            if (not israel) and hd.month == 3 and hd.day == 7:
                ent = self._lookup_special(["shavuot", "day_2_diaspora"])
                if ent and "id" in ent:
                    return str(ent["id"]), "shavuot:day_2_diaspora", extra

        if fest == "Pesach":
            if hd.month == 1 and hd.day == 15:
                ent = self._lookup_special(["pesach", "day_1"])
                if ent and "id" in ent:
                    return str(ent["id"]), "pesach:day_1", extra
            if (not israel) and hd.month == 1 and hd.day == 16:
                ent = self._lookup_special(["pesach", "day_2_diaspora"])
                if ent and "id" in ent:
                    return str(ent["id"]), "pesach:day_2_diaspora", extra
            if hd.month == 1 and 17 <= hd.day <= 20:
                ent = self._lookup_special(["pesach", "chol_hamoed"])
                if ent and "id" in ent:
                    return str(ent["id"]), "pesach:chol_hamoed", extra
            if hd.month == 1 and hd.day == 21:
                ent = self._lookup_special(["pesach", "day_7"])
                if ent and "id" in ent:
                    return str(ent["id"]), "pesach:day_7", extra
            if (not israel) and hd.month == 1 and hd.day == 22:
                ent = self._lookup_special(["pesach", "day_8_diaspora"])
                if ent and "id" in ent:
                    return str(ent["id"]), "pesach:day_8_diaspora", extra

        if fest in ("Succos", "Shmini Atzeres", "Simchas Torah"):
            # Sukkos/CHM
            if hd.month == 7 and hd.day == 15:
                ent = self._lookup_special(["sukkot", "day_1"])
                if ent and "id" in ent:
                    return str(ent["id"]), "sukkot:day_1", extra
            if (not israel) and hd.month == 7 and hd.day == 16:
                ent = self._lookup_special(["sukkot", "day_2_diaspora"])
                if ent and "id" in ent:
                    return str(ent["id"]), "sukkot:day_2_diaspora", extra
            if hd.month == 7 and 17 <= hd.day <= 21:
                ent = self._lookup_special(["sukkot", "chol_hamoed"])
                if ent and "id" in ent:
                    return str(ent["id"]), "sukkot:chol_hamoed", extra

            if fest == "Shmini Atzeres":
                if israel:
                    ent = self._lookup_special(["simchat_torah"])
                    if ent and "id" in ent:
                        return str(ent["id"]), "shemini_atzeres:israel_simchat_torah", extra
                else:
                    ent = self._lookup_special(["sukkot", "shemini_atzeret_diaspora"])
                    if ent and "id" in ent:
                        return str(ent["id"]), "shemini_atzeres:diaspora", extra

            if fest == "Simchas Torah":
                ent = self._lookup_special(["simchat_torah"])
                if ent and "id" in ent:
                    return str(ent["id"]), "simchat_torah", extra

        return None, None, extra

    def _extract_haftarah_from_special(self, ent: dict[str, Any], minhag: str) -> dict[str, Any]:
        """
        Extract minhag-specific haftarah info from a holiday_haftarot entry.
        
        The entry may have:
          - haftarah_ashkenazi / haftarah_sephardi (for entries with minhag variants)
          - haftarah (for entries with single haftarah for all)
          
        Each haftarah sub-object has: name_hebrew, source, etc.
        """
        result: dict[str, Any] = {}
        
        # Map minhag to key variants
        minhag_key = "ashkenazi" if minhag in ("ashkenazi", "ashkenaz") else "sephardi"
        
        # Try minhag-specific first
        haft = ent.get(f"haftarah_{minhag_key}")
        if not haft:
            # Try alternate spelling
            alt_key = "sephardi" if minhag_key == "ashkenazi" else "ashkenazi"
            haft = ent.get(f"haftarah_{alt_key}")
        if not haft:
            # Fall back to generic "haftarah" key
            haft = ent.get("haftarah")
        
        if isinstance(haft, dict):
            result["haftarah_name"] = haft.get("name_hebrew", "")
            result["haftarah_source"] = haft.get("source", "")
            result["haftarah_name_english"] = haft.get("name_english", "")
            result["haftarah_source_english"] = haft.get("source_english", "")
        
        # Also extract both variants for the Variants attribute
        variants = {}
        if ent.get("haftarah_ashkenazi"):
            ash = ent["haftarah_ashkenazi"]
            name = ash.get("name_hebrew", "")
            src = ash.get("source", "")
            variants["ashkenazi"] = f"{name} ({src})" if name and src else name
        if ent.get("haftarah_sephardi"):
            seph = ent["haftarah_sephardi"]
            name = seph.get("name_hebrew", "")
            src = seph.get("source", "")
            variants["sephardi"] = f"{name} ({src})" if name and src else name
        if ent.get("haftarah") and not variants:
            # Single haftarah, no variants
            haft = ent["haftarah"]
            name = haft.get("name_hebrew", "")
            src = haft.get("source", "")
            variants["all"] = f"{name} ({src})" if name and src else name
            
        if variants:
            result["haftarah_variants"] = variants
            
        return result

    def _resolve_special_shabbatot(self, shabbos: date, minhag: str = "ashkenazi") -> tuple[str | None, str | None, dict[str, Any]]:
        gd = _greg_from_pydate(shabbos)
        hd = gd.to_heb()
        extra: dict[str, Any] = {}

        # Shabbat Shuva (3..9 Tishrei)
        if hd.month == 7 and 3 <= hd.day <= 9:
            ent = self._lookup_special(["special_shabbatot", "shabbat_shuva"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                return str(ent["id"]), "shabbat_shuva", extra

        hy = hd.year
        leap = HebrewYear(hy).leap
        purim_month = 13 if leap else 12

        # Arba parshiyot
        rc_adar = _to_pydate(HebrewDate(hy, purim_month, 1).to_greg())
        shekalim = _prev_or_same_shabbos(rc_adar)

        purim = _to_pydate(HebrewDate(hy, purim_month, 14).to_greg())
        zachor = _prev_shabbos_strict(purim)
        parah = zachor + timedelta(days=7)

        rc_nisan = _to_pydate(HebrewDate(hy, 1, 1).to_greg())
        hachodesh = _prev_or_same_shabbos(rc_nisan)

        pesach1 = _to_pydate(HebrewDate(hy, 1, 15).to_greg())
        hagadol = _prev_or_same_shabbos(pesach1 - timedelta(days=1))

        # Try both key formats: "57_shekalim" (your JSON) and "shabbat_shekalim" (fallback)
        if shabbos == shekalim:
            ent = self._lookup_special(["arba_parshiyot", "57_shekalim"]) or self._lookup_special(["arba_parshiyot", "shabbat_shekalim"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                extra["special_shabbos"] = "שבת שקלים"
                return str(ent["id"]), "arba_parshiyot:shekalim", extra
        if shabbos == zachor:
            ent = self._lookup_special(["arba_parshiyot", "58_zachor"]) or self._lookup_special(["arba_parshiyot", "shabbat_zachor"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                extra["special_shabbos"] = "שבת זכור"
                return str(ent["id"]), "arba_parshiyot:zachor", extra
        if shabbos == parah:
            ent = self._lookup_special(["arba_parshiyot", "59_parah"]) or self._lookup_special(["arba_parshiyot", "shabbat_parah"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                extra["special_shabbos"] = "שבת פרה"
                return str(ent["id"]), "arba_parshiyot:parah", extra
        if shabbos == hachodesh:
            ent = self._lookup_special(["arba_parshiyot", "60_hachodesh"]) or self._lookup_special(["arba_parshiyot", "shabbat_hachodesh"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                extra["special_shabbos"] = "שבת החודש"
                return str(ent["id"]), "arba_parshiyot:hachodesh", extra
        if shabbos == hagadol:
            ent = self._lookup_special(["arba_parshiyot", "61_shabbat_hagadol"]) or self._lookup_special(["special_shabbatot", "shabbat_hagadol"])
            if ent and "id" in ent:
                extra.update(self._extract_haftarah_from_special(ent, minhag))
                extra["special_shabbos"] = "שבת הגדול"
                return str(ent["id"]), "shabbat_hagadol", extra

        return None, None, extra

    def _check_additional_pesukim(self, shabbos: date, reason: str) -> dict[str, Any]:
        """
        Check if we need to add first/last pesukim of Rosh Chodesh or Machar Chodesh.
        
        This applies when:
        - Shabbat is Rosh Chodesh (day 1 or 30) but we read a different haftarah
          (e.g., Chanukah, Shekalim on RC Adar, HaChodesh on RC Nisan)
        - Shabbat is Erev Rosh Chodesh but we read a different haftarah
        
        Special case: When Shabbat is day 30 (first day of two-day RC), tomorrow 
        is day 1 (second day of RC), so we add BOTH RC and Machar Chodesh pesukim.
        
        We don't add pesukim when:
        - The haftarah IS the Rosh Chodesh or Machar Chodesh haftarah
        - Regular parsha reading (then RC/MC haftarah is read instead, not added)
        
        Note: Month lengths vary - Cheshvan/Kislev can have 29 or 30 days.
        Day 30 only exists (and is RC) if the month has 30 days.
        """
        result: dict[str, Any] = {}
        
        gd = _greg_from_pydate(shabbos)
        hd = gd.to_heb()
        
        # Check if this is Rosh Chodesh
        # Day 1 is always RC. Day 30, if it exists in the date, is also RC.
        is_rc = hd.day in (1, 30)
        is_rc_day_30 = hd.day == 30  # First day of two-day RC
        
        # Check if this is Erev Rosh Chodesh (tomorrow is RC but today is not)
        is_erev_rc = False
        tomorrow_is_rc = False
        if not is_rc:
            try:
                # Try to get tomorrow in the same month
                tomorrow_hd = HebrewDate(hd.year, hd.month, hd.day + 1)
                # If tomorrow is day 30, it's RC (and today is Erev RC)
                if tomorrow_hd.day == 30:
                    is_erev_rc = True
                    tomorrow_is_rc = True
            except (ValueError, TypeError):
                # Tomorrow doesn't exist in this month, so tomorrow is 1st of next month = RC
                # Therefore today is Erev RC
                is_erev_rc = True
                tomorrow_is_rc = True
        elif is_rc_day_30:
            # Today is day 30 (RC), and tomorrow is day 1 (also RC)
            tomorrow_is_rc = True
        
        # Only add pesukim if we're reading a special haftarah that overrides RC/MC
        # (festivals, Chanukah, arba parshiyot, etc.) - NOT for regular parsha
        special_reasons = [
            "chanuka", "arba_parshiyot", "shabbat_shuva", "shabbat_hagadol",
            "festival", "rosh_hashana", "yom_kippur", "sukkot", "pesach", "shavuot"
        ]
        
        is_special = any(sr in reason.lower() for sr in special_reasons)
        
        if not is_special:
            return result
        
        additions = []
        sources = []
        
        # Check for Rosh Chodesh addition
        if is_rc and "rosh_chodesh" not in reason.lower():
            additions.append("ר\"ח")
            sources.append("ישעיהו סו:א + סו:כג")
        
        # Check for Machar Chodesh addition
        # This applies when tomorrow is RC (either Erev RC, or day 30 with day 1 tomorrow)
        if tomorrow_is_rc and "machar_chodesh" not in reason.lower():
            # Don't add MC if we're on day 1 of RC (tomorrow is day 2, not RC)
            if hd.day != 1:
                additions.append("מחר חודש")
                sources.append("שמואל א כ:יח + כ:מב")
        
        if additions:
            result["add_pesukim"] = "גם פסוק ראשון ואחרון של " + " ושל ".join(additions)
            result["add_pesukim_source"] = " + ".join(sources)
            result["add_pesukim_type"] = "both" if len(additions) > 1 else ("rosh_chodesh" if "ר\"ח" in additions else "machar_chodesh")
            
        return result

    def _resolve_rosh_machar(self, shabbos: date) -> tuple[str | None, str | None, dict[str, Any]]:
        gd = _greg_from_pydate(shabbos)
        hd = gd.to_heb()
        extra: dict[str, Any] = {}

        if _is_rosh_chodesh(hd):
            ent = self._lookup_special(["special_shabbatot", "shabbat_rosh_chodesh"])
            if ent and "id" in ent:
                extra["rosh_chodesh"] = True
                return str(ent["id"]), "shabbat_rosh_chodesh", extra

        tomorrow = _greg_from_pydate(shabbos + timedelta(days=1)).to_heb()
        if (not _is_rosh_chodesh(hd)) and _is_rosh_chodesh(tomorrow):
            ent = self._lookup_special(["special_shabbatot", "shabbat_machar_chodesh"])
            if ent and "id" in ent:
                extra["machar_chodesh"] = True
                return str(ent["id"]), "shabbat_machar_chodesh", extra

        return None, None, extra

    def _resolve_parsha(self, shabbos: date, israel: bool) -> tuple[str | None, str | None, dict[str, Any]]:
        extra: dict[str, Any] = {}
        gd = _greg_from_pydate(shabbos)
        p = parshios.getparsha(gd, israel=israel)
        if not p:
            return None, None, extra

        # pyluach getparsha() returns 0-based indices for GregorianDate
        names = [parshios.PARSHIOS[i] for i in p]
        extra["parsha_names"] = names

        def norm(s: str) -> str:
            return (
                s.lower()
                .replace("'", "'")
                .replace("'", "")
                .replace("-", " ")
                .replace("–", " ")
                .strip()
                .replace(" ", "")
            )

        slugs: list[str] = []
        for nm in names:
            slug = self._parsha_norm_to_slug.get(norm(nm))
            if not slug:
                extra["parsha_slug_missing_for"] = nm
                return None, None, extra
            slugs.append(slug)

        suffix = "_".join(slugs)
        # keep the suffix internal for lookup, but don't expose it as an attribute

        rule = self._rule_by_suffix.get(suffix)
        if isinstance(rule, dict):
            hid = rule.get("haftarah_id") or rule.get("id")
            if hid:
                return str(hid), f"parsha:{suffix}", extra
        elif rule is not None:
            # direct mapping suffix -> id
            return str(rule), f"parsha:{suffix}", extra

        extra["weekly_rule_missing_for_suffix"] = suffix
        return None, None, extra

    def resolve(self, shabbos: date, israel: bool, minhag: str) -> HaftorahResolved | None:
        hid, reason, extra = self._resolve_festival(shabbos, israel=israel)
        if hid:
            ent = self._catalog_entry(hid)
            if not ent:
                return None
            name, full, variants = self._pick_name(ent, minhag)
            # Check for additional pesukim (RC/MC)
            extra.update(self._check_additional_pesukim(shabbos, reason or "festival"))
            return HaftorahResolved(
                haftarah_id=str(hid),
                display_name=name,
                full_name=full,
                variants=variants,
                source_ref=ent.get("source") or "",
                reason=reason or "festival",
                notes=ent.get("notes"),
                extra=extra,
            )

        hid, reason, extra2 = self._resolve_special_shabbatot(shabbos, minhag=minhag)
        if hid:
            ent = self._catalog_entry(hid)
            if not ent:
                return None
            
            # Check if we have rich haftarah data from the holiday_haftarot section
            if extra2.get("haftarah_name"):
                # Use the minhag-specific haftarah name and source from extra
                display_name = extra2["haftarah_name"]
                source_ref = extra2.get("haftarah_source", "")
                # Build variants dict from the extracted data
                variants = extra2.get("haftarah_variants", {})
                full_name = extra2.get("special_shabbos", ent.get("name_hebrew", ""))
            else:
                # Fall back to catalog entry
                display_name, full_name, variants = self._pick_name(ent, minhag)
                source_ref = ent.get("source") or ""
            
            # Check for additional pesukim (RC/MC)
            extra2.update(self._check_additional_pesukim(shabbos, reason or "special_shabbos"))
            
            return HaftorahResolved(
                haftarah_id=str(hid),
                display_name=display_name,
                full_name=full_name,
                variants=variants,
                source_ref=source_ref,
                reason=reason or "special_shabbos",
                notes=ent.get("notes"),
                extra=extra2,
            )

        hid, reason, extra3 = self._resolve_rosh_machar(shabbos)
        if hid:
            ent = self._catalog_entry(hid)
            if not ent:
                return None
            name, full, variants = self._pick_name(ent, minhag)
            return HaftorahResolved(
                haftarah_id=str(hid),
                display_name=name,
                full_name=full,
                variants=variants,
                source_ref=ent.get("source") or "",
                reason=reason or "rosh_chodesh",
                notes=ent.get("notes"),
                extra=extra3,
            )

        hid, reason, extra4 = self._resolve_parsha(shabbos, israel=israel)
        if not hid:
            _LOGGER.warning("Haftorah resolve failed for %s (israel=%s): %s", shabbos, israel, extra4)
            return None

        ent = self._catalog_entry(hid)
        if not ent:
            return None

        name, full, variants = self._pick_name(ent, minhag)
        return HaftorahResolved(
            haftarah_id=str(hid),
            display_name=name,
            full_name=full,
            variants=variants,
            source_ref=ent.get("source") or "",
            reason=reason or "weekly",
            notes=ent.get("notes"),
            extra=extra4,
        )


_RESOLVER: HaftorahResolver | None = None


def _get_resolver() -> HaftorahResolver:
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = HaftorahResolver()
    return _RESOLVER


class HaftorahSensor(YidCalDisplayDevice, SensorEntity):
    _attr_name = "Haftorah"
    _attr_icon = "mdi:book-open-variant"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__()
        self.hass = hass
        self._attr_unique_id = "yidcal_haftorah"
        self.entity_id = "sensor.yidcal_haftorah"
        self._state: str | None = None
        self._attrs: dict[str, Any] = {}
        self._geo = None
        self._tz = ZoneInfo(self.hass.config.time_zone)
        self._unsub_interval = None
        self._unsub_midnight = None

    @property
    def native_value(self) -> str | None:
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._geo = await get_geo(self.hass)
        
        # Pre-load the compiled data in the executor to avoid blocking
        # This is belt-and-suspenders since we also load at module import
        await self.hass.async_add_executor_job(_get_compiled_data)
        
        await self.async_update()
        self._unsub_interval = async_track_time_interval(
            self.hass, self.async_update, timedelta(minutes=15)
        )
        # Force flip at midnight (instead of waiting up to 15 minutes)
        self._unsub_midnight = async_track_time_change(
            self.hass, self.async_update, hour=0, minute=0, second=5
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_interval:
            self._unsub_interval()
            self._unsub_interval = None
        if self._unsub_midnight:
            self._unsub_midnight()
            self._unsub_midnight = None

    async def async_update(self, now: datetime | None = None) -> None:
        if not self._geo:
            return

        cfg = (self.hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        is_in_israel = bool(cfg.get("is_in_israel", False))
        minhag = str(cfg.get(CONF_HAFTORAH_MINHAG, DEFAULT_HAFTORAH_MINHAG))

        candle_offset = int(cfg.get("candlelighting_offset", cfg.get("candle", 15)))
        havdalah_offset = int(cfg.get("havdalah_offset", cfg.get("havdala", 72)))

        now_local = (now or dt_util.now()).astimezone(self._tz)
        shabbos_date, in_window = _determine_shabbos_target(
            now_local,
            geo=self._geo,
            candle_offset=candle_offset,
            havdalah_offset=havdalah_offset,
        )

        resolved = _get_resolver().resolve(shabbos_date, israel=is_in_israel, minhag=minhag)

        if not resolved:
            self._state = None
            self._attrs = {
                "error": "Could not resolve haftarah",
                "shabbos_date": shabbos_date.isoformat(),
                "is_in_israel": is_in_israel,
                "minhag": minhag,
            }
            return

        def _variants_hebrew_keys(v: dict[str, str]) -> dict[str, str]:
            key_map = {
                "ashkenazi": "אשכנז",
                "ashkenaz": "אשכנז",
                "sephardi": "ספרד",
                "sefardi": "ספרד",
                "sephardic": "ספרד",
            }
            return {key_map.get(str(k).lower(), k): v[k] for k in v}

        extra = dict(resolved.extra or {})
        extra.pop("parsha_suffix", None)
        # Remove internal extraction keys that shouldn't be exposed as attributes
        extra.pop("haftarah_name", None)
        extra.pop("haftarah_source", None)
        extra.pop("haftarah_name_english", None)
        extra.pop("haftarah_source_english", None)
        extra.pop("haftarah_variants", None)
        extra.pop("add_pesukim_type", None)  # Internal use only
        
        # Extract add_pesukim before renaming for use in state
        add_pesukim_text = extra.get("add_pesukim", "")
        
        # Rename add_pesukim keys to Hebrew-friendly attribute names
        if "add_pesukim" in extra:
            extra["מוסיפים"] = extra.pop("add_pesukim")
        if "add_pesukim_source" in extra:
            extra["מקור_התוספת"] = extra.pop("add_pesukim_source")
        
        variants = _variants_hebrew_keys(resolved.variants or {})

        # Build the display state
        # If minhag-specific variant exists and includes source ref (in parentheses),
        # use it directly. Otherwise, append source_ref if available.
        display = resolved.display_name or ""
        
        # Check if the display name already has a source reference (contains parentheses)
        if display and "(" not in display and resolved.source_ref:
            # Append source reference if not already included
            display = f"{display} ({resolved.source_ref})"
        
        # Add the מוסיפים text to the state (without source)
        if add_pesukim_text:
            display = f"{display} - {add_pesukim_text}"
        
        # State: haftarah name with source, plus any additions
        self._state = display if display else None

        # Attributes: keep footnotes/alternates usable for later UI
        gd = _greg_from_pydate(shabbos_date)
        hd = gd.to_heb()

        self._attrs = {
            "Haftarah_ID": resolved.haftarah_id,
            #"display_name": resolved.display_name,
            "Full_Name": resolved.full_name,
            #"Minhag_Configured": minhag,
            "Variants": variants,
            "Notes": resolved.notes or "",
            "Source_Ref": resolved.source_ref,
            "Reason": resolved.reason,
            #"Shabbos_Date": shabbos_date.isoformat(),
            #"In_Shabbos_Window": in_window,
            #"Is_In_Israel": is_in_israel,
            #"hebrew_date": f"{hd.day} {hd.month_name(True)} {hd.year}",
            **extra,
        }
