"""
custom_components/yidcal/yidcal_lib/places.py

Curated community-centroid database for snapping HA-configured coordinates to
verified luach-aligned coordinates. When a user's lat/lon is near a community
in this list, that community's centroid is used for all zmanim calculations.

Coordinate source: published Zmanim program place database (``placelist``,
9232 rows). The source stores longitude WEST-POSITIVE; every row was negated
on import. Kiryas Joel was cross-verified against the South Fallsburg 5786
printed luach (matches all sunset and candle-lighting times to the minute).

The 408 hand-curated entries that previously lived in this file are still
here -- each was matched to its source row by coordinate (all within 50 m)
and its display name and state kept, so ``Tzfas`` stays ``Tzfas`` rather than
becoming ``Tsfat``. The source spelling is retained as a searchable alias, so
both ``Vienna`` and ``Wien`` resolve to the same entry.

US state codes were derived at build time from county boundary polygons.
That pass corrected two long-standing errors: ``Norfolk`` was stored at
Nebraska coordinates but labelled ``VA``, and ``Hollywood`` at Florida
coordinates but labelled ``CA``.

Candle-lighting minutes (``Neros``) and elevation (``Height``) exist in the
source database but are deliberately NOT imported -- candle offset is
configured in the YidCal config flow or overridden per-call by the PDF
generator, and a third source of truth here would be a silent-wrong-time
waiting to happen.

Matching strategy:
  1) ``find_place_by_name`` resolves a typed place name with no network call.
  2) ``find_place`` snaps coordinates: CUSTOM_BBOX places (currently only
     Kiryas Joel) match anywhere inside their explicit bounding box;
     otherwise the NEAREST place within ``DEFAULT_RADIUS_KM`` wins.
     Distance-based matching avoids the bbox-overlap problem in dense areas
     like Brooklyn, Gush Dan, and the Catskills.

Both fall through to None when nothing matches, so callers can use a
reverse-geocoding fallback (Nominatim).
"""
from __future__ import annotations

import json
import math
import os

# Default snap radius. A user within this many km of a place's centroid will
# match that place. Tuned so that close-but-distinct communities (e.g. Monsey
# vs Spring Valley, BB vs RG) each match their own centroid when the user is
# actually in that community, while still tolerating GPS imprecision.
#
# Verified against the expanded dataset: across a sample of real destinations,
# every non-match fell through cleanly rather than snapping to a town tens of
# km away (Myrtle Beach did not grab Florence SC; Jackson WY did not grab
# Idaho Falls). Widening this to 10 km buys a handful of suburbs and gives up
# that safety margin everywhere, so it stays at 5.
DEFAULT_RADIUS_KM: float = 5.0

# Custom bounding boxes for places that need wider, explicit coverage.
# Key: display name (must match the entry in PLACES exactly).
# Value: (lat_min, lat_max, lon_min, lon_max).
# Inside any custom box, that place is returned immediately (radius is ignored).
CUSTOM_BBOX: dict[str, tuple[float, float, float, float]] = {
    # Kiryas Joel: wide box covers the entire KJ-zone in Orange County, NY,
    # so users configured to surrounding addresses (Forest Glen, Larkin Drive,
    # Watergap, etc.) snap to KJ's luach-aligned centroid.
    "Kiryas Joel": (41.20, 41.45, -74.30, -74.00),
    # South Fallsburg: deliberately TIGHT. It covers South Fallsburg itself
    # and the Fallsburgh hamlet 2.7 km northeast, and nothing else. A box
    # drawn around the whole Town of Fallsburg would swallow Woodbourne,
    # Loch Sheldrake, Hurleyville, Woodridge, Mountain Dale, Dairyland and
    # Harris -- all curated communities that keep their own luach.
    # Clearances to the nearest excluded neighbour are roughly 400-550 m,
    # so widening this by even a hundredth of a degree starts eating them.
    "South Fallsburg": (41.705, 41.745, -74.645, -74.575),
}

_DATA_FILE = os.path.join(os.path.dirname(__file__), "places_data.json")


def _normalize_key(text: str) -> str:
    """Fold a name for lookup: collapse whitespace, strip, casefold."""
    return " ".join(str(text).split()).strip().casefold()


def _load_places() -> tuple[
    list[tuple[str, str, float, float]],
    list[tuple[str, str, float, float, str, str, bool]],
    dict[str, str],
    dict[str, list[int]],
]:
    """Read the packaged dataset once at import time.

    Returns (PLACES, records, hebrew_names, name_index) where ``records``
    carries the extra Hebrew-name and country fields that ``PLACES`` -- kept
    as 4-tuples for backward compatibility -- does not.
    """
    with open(_DATA_FILE, encoding="utf-8") as handle:
        payload = json.load(handle)

    places: list[tuple[str, str, float, float]] = []
    records: list[tuple[str, str, float, float, str, str, bool]] = []
    hebrew: dict[str, str] = {}
    index: dict[str, list[int]] = {}

    for position, row in enumerate(payload["places"]):
        name, state, lat, lon, heb, country, aliases, curated = row
        places.append((name, state, lat, lon))
        records.append((name, state, lat, lon, heb, country, bool(curated)))
        # A name can repeat across countries (London UK / Ontario / Kiribati),
        # so only the row that carries a Hebrew name claims the mapping.
        if heb and name not in hebrew:
            hebrew[name] = heb
        # A row can repeat a string across fields (a place whose alias
        # equals its name); indexing it twice would make an unambiguous
        # name look like a two-way collision and resolve to None.
        for key in [name, heb, *aliases]:
            if not key:
                continue
            bucket = index.setdefault(_normalize_key(key), [])
            if position not in bucket:
                bucket.append(position)

    return places, records, hebrew, index


# (display_name, state_or_province, latitude, longitude)
PLACES: list[tuple[str, str, float, float]]
_RECORDS: list[tuple[str, str, float, float, str, str, bool]]
HEBREW_NAMES: dict[str, str]
_NAME_INDEX: dict[str, list[int]]

PLACES, _RECORDS, HEBREW_NAMES, _NAME_INDEX = _load_places()


def get_hebrew_name(name: str) -> str | None:
    """Return the Hebrew/Yiddish form for a place, or None if none is on file."""
    return HEBREW_NAMES.get(name)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def _bbox_place(
    latitude: float, longitude: float,
) -> tuple[str, str, float, float] | None:
    """Return the CUSTOM_BBOX place covering these coordinates, or None.

    Shared by both lookups so a typed name and a configured coordinate
    inside the same zone always resolve to the same centroid.
    """
    for name, state, p_lat, p_lon in PLACES:
        bbox = CUSTOM_BBOX.get(name)
        if bbox is None:
            continue
        lat_min, lat_max, lon_min, lon_max = bbox
        if lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max:
            return name, state, p_lat, p_lon
    return None


def find_place(latitude: float, longitude: float) -> tuple[str, str, float, float] | None:
    """Snap (lat, lon) to a curated community centroid.

    1) Custom-bbox places (KJ) match if (lat, lon) is inside the explicit box.
    2) Otherwise, the nearest entry in PLACES within DEFAULT_RADIUS_KM wins.

    Returns (name, state, snapped_lat, snapped_lon) on match, or None.
    """
    # Phase 1: custom bbox places (wide catch-all coverage)
    boxed = _bbox_place(latitude, longitude)
    if boxed is not None:
        return boxed

    # Phase 2: nearest non-custom place within DEFAULT_RADIUS_KM.
    # A degree of latitude is ~111 km everywhere, so anything further than
    # the radius in latitude alone cannot possibly match -- skipping those
    # before computing a haversine keeps this fast across 9000+ rows.
    lat_window = DEFAULT_RADIUS_KM / 111.0
    best: tuple[str, str, float, float] | None = None
    best_dist = DEFAULT_RADIUS_KM
    for name, state, p_lat, p_lon in PLACES:
        if abs(p_lat - latitude) > lat_window:
            continue
        if name in CUSTOM_BBOX:
            continue
        dist = _haversine_km(latitude, longitude, p_lat, p_lon)
        if dist <= best_dist:
            best_dist = dist
            best = (name, state, p_lat, p_lon)
    return best


def find_place_by_name(query: str) -> tuple[str, str, float, float] | None:
    """Resolve a typed place name against the curated list, with no network call.

    Matching is case- and whitespace-insensitive and covers the display name,
    the source database's own spelling, and the Hebrew name -- so ``Vienna``,
    ``Wien`` and ``וויען`` all resolve to the same entry.

    Disambiguation, in order:
      • An explicit qualifier filters first: ``"London, Canada"`` and
        ``"Monroe, NY"`` match on country or state respectively.
      • A name unique across the dataset wins outright. That covers the vast
        majority -- only 163 of 9038 names appear in more than one country.
      • A hand-curated entry beats an imported one, so Toledo resolves to
        Ohio rather than Spain and Athens to Greece rather than Ohio.
      • Still tied, the entry carrying a Hebrew name wins. That reliably
        marks the community a luach would be generated for: London UK over
        London Ontario, Berlin over Berlin NH, Hebron over Hebron Canada.
      • If that still doesn't decide it, returns None rather than guessing, so
        the caller falls through to the geocoder.

    Returns (name, state, latitude, longitude) or None.
    """
    if not query:
        return None
    raw = " ".join(str(query).split()).strip()
    if not raw:
        return None

    candidates = _NAME_INDEX.get(_normalize_key(raw), [])

    # No whole-string hit: try "<place>, <qualifier>".
    if not candidates and "," in raw:
        base, _, qualifier = raw.rpartition(",")
        base_key = _normalize_key(base)
        qual_key = _normalize_key(qualifier)
        pool = _NAME_INDEX.get(base_key, [])
        filtered = [
            i for i in pool
            if _normalize_key(_RECORDS[i][1]) == qual_key
            or _normalize_key(_RECORDS[i][5]) == qual_key
        ]
        # An unrecognised qualifier ("Lakewood, USA" when the row carries a
        # state rather than a country) must not silently widen the search
        # back to every Lakewood on earth, so a filtered-to-nothing pool
        # only falls back when the base name is itself unambiguous.
        candidates = filtered or (pool if len(pool) == 1 else [])

    if not candidates:
        return None

    # Hand-curated entries outrank imported ones. Those 408 were picked
    # because they are places a luach actually gets generated for, so a
    # collision with a foreign namesake resolves to the curated side:
    # Toledo OH over Toledo Spain, Los Angeles over Los Angeles Chile,
    # Athens Greece over Athens Ohio.
    curated = [i for i in candidates if _RECORDS[i][6]]
    if curated:
        candidates = curated
    if len(candidates) == 1:
        return _resolved(candidates[0])

    # Still tied: the Hebrew name marks the community with Jewish presence.
    with_hebrew = [i for i in candidates if _RECORDS[i][4]]
    if len(with_hebrew) == 1:
        return _resolved(with_hebrew[0])

    return None


def _resolved(position: int) -> tuple[str, str, float, float]:
    """Return a record as (name, state, lat, lon), applying any CUSTOM_BBOX.

    A typed name inside a custom box resolves to that box's centroid, the
    same as a coordinate would. Without this, "Monroe" returned Monroe's
    own centroid while an HA install configured at those coordinates
    snapped to Kiryas Joel -- two different luachs for one place.
    """
    name, state, lat, lon, _heb, _country, _cur = _RECORDS[position]
    boxed = _bbox_place(lat, lon)
    if boxed is not None:
        return boxed
    return name, state, lat, lon


# ── State / province abbreviation (display only) ───────────────────────
# Nominatim returns full names ("New York"); the curated entries carry
# two-letter codes ("NY"). Folding one into the other keeps the geocoded
# fallback label consistent with a curated hit: "Lumberland, NY" reads the
# same way "Monsey, NY" does.
_STATE_ABBR: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "district of columbia": "DC", "florida": "FL",
    "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY",
    "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "puerto rico": "PR", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB",
    "new brunswick": "NB", "newfoundland and labrador": "NL",
    "nova scotia": "NS", "ontario": "ON", "prince edward island": "PE",
    "quebec": "QC", "québec": "QC", "saskatchewan": "SK",
}


def abbreviate_state(state: str) -> str:
    """Return the two-letter code for a US state or Canadian province.

    Anything unrecognised -- foreign regions, empty input, an already
    abbreviated code -- comes back unchanged, so this is safe to apply
    to whatever the geocoder happens to return.
    """
    if not state:
        return ""
    return _STATE_ABBR.get(_normalize_key(state), state)
