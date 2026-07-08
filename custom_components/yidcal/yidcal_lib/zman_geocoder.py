"""
custom_components/yidcal/yidcal_lib/zman_geocoder.py

Small helper for geocoding a free-form location string (ZIP code, city,
landmark, etc.) into the (lat, lon, tzname, display_name) tuple needed
by the Zmanim Lookup service when the user passes a ``location``
parameter.

Reuses the same Nominatim/geopy + timezonefinder stack the integration
already uses at config-flow time. Results are cached per-process under
``hass.data[DOMAIN][CACHE_KEY]`` so repeated lookups for the same
location don't re-hit the geocoder.

Pure-async-friendly: the blocking parts (geopy + timezonefinder) run on
the executor.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CACHE_KEY = "_zmanim_lookup_geo_cache"


@dataclass(frozen=True)
class ResolvedLocation:
    latitude: float
    longitude: float
    tzname: str
    display_name: str  # human-readable result, e.g. "Lakewood, Ocean County, NJ, USA"


def _normalize(query: str) -> str:
    """Trim and collapse whitespace so 'Lakewood, NJ' and '  Lakewood ,NJ '
    share a cache entry."""
    return " ".join(query.split()).strip()


async def resolve_location(hass: HomeAssistant, raw_query: str) -> ResolvedLocation:
    """Geocode a free-form location string.

    Disambiguates regionally by biasing the geocoder toward the user's
    HA-configured location:
      • A ~5°-radius ``viewbox`` is built around HA's latitude/longitude
        (a soft bias — global matches still win when they're unambiguous,
        e.g. "Jerusalem" / "London").
      • When ``hass.config.country`` is set (e.g. "US"), it's passed as
        a primary country filter; if that returns nothing, we retry
        without the filter so genuinely-foreign queries still resolve.

    The combination prevents the classic ambiguous-ZIP problem (e.g.
    a US user typing "12733" silently getting a Ukrainian postal code)
    without locking out international queries.

    Raises ``ServiceValidationError`` on empty input, no geocoder result,
    timeout, or any other geopy failure — silent fallback to the
    configured location is intentionally NOT done here, since giving
    wrong-location zmanim could be halachically misleading.
    """
    if not raw_query:
        raise ServiceValidationError("Location must be a non-empty string.")
    query = _normalize(raw_query)
    if not query:
        raise ServiceValidationError("Location must be a non-empty string.")

    cache: dict[str, ResolvedLocation] = (
        hass.data.setdefault(DOMAIN, {}).setdefault(CACHE_KEY, {})
    )
    if query in cache:
        return cache[query]

    # Pull the user's home location for biasing. Falls back gracefully
    # when any of these aren't set (e.g. a fresh HA instance with no
    # location configured — bias just gets skipped in that case).
    home_lat = getattr(hass.config, "latitude", None)
    home_lon = getattr(hass.config, "longitude", None)
    home_country = getattr(hass.config, "country", None)
    home_country_code = (str(home_country).lower() or None) if home_country else None

    def _blocking_resolve() -> ResolvedLocation:
        # Imports inside the executor function so they don't slow module
        # import time and so any import failures surface as a clean error.
        from geopy.geocoders import Nominatim
        from geopy.exc import GeopyError
        from timezonefinder import TimezoneFinder

        # Build a wide viewbox around the user's home — ~5° in each
        # direction (~550 km north-south). This is intentionally
        # generous so it covers a typical country/region.
        viewbox = None
        if home_lat is not None and home_lon is not None:
            BOX = 5.0
            viewbox = [
                (home_lat + BOX, home_lon - BOX),  # NW corner
                (home_lat - BOX, home_lon + BOX),  # SE corner
            ]

        def _do_geocode(*, with_country: bool):
            kwargs = {"exactly_one": True, "timeout": 10}
            if viewbox is not None:
                # bounded=False → bias only, don't restrict.
                kwargs["viewbox"] = viewbox
                kwargs["bounded"] = False
            if with_country and home_country_code:
                kwargs["country_codes"] = home_country_code
            return Nominatim(user_agent="yidcal").geocode(query, **kwargs)

        try:
            # Pass 1: with country filter (when we have one) + viewbox bias.
            loc = _do_geocode(with_country=True)
            # Pass 2: if the country filter returned nothing, retry without
            # so international queries (e.g. a US user looking up
            # "Jerusalem") still resolve.
            if loc is None and home_country_code:
                loc = _do_geocode(with_country=False)
        except GeopyError as exc:
            raise ServiceValidationError(
                f"Geocoder error while resolving location {query!r}: {exc}. "
                f"Please try again in a moment."
            ) from exc

        if loc is None:
            raise ServiceValidationError(
                f"Could not find a location for {query!r}. Try a more "
                f"specific input (e.g. 'Lakewood, NJ' or a postal code)."
            )

        try:
            tzname = TimezoneFinder().timezone_at(lng=loc.longitude, lat=loc.latitude)
        except Exception:
            tzname = None
        if not tzname:
            raise ServiceValidationError(
                f"Could not determine timezone for {query!r} "
                f"(lat={loc.latitude}, lon={loc.longitude})."
            )

        return ResolvedLocation(
            latitude=loc.latitude,
            longitude=loc.longitude,
            tzname=tzname,
            display_name=str(loc.address) if getattr(loc, "address", None) else query,
        )

    resolved_raw = await hass.async_add_executor_job(_blocking_resolve)

    # Snap the geocoded coords to the canonical city centroid, the
    # same way HA setup snaps the user's configured home coords. So a
    # ZIP entered in the service produces identical zmanim to a user
    # who configured that ZIP as HA's home location. The snap first
    # checks the curated community-centroid list (``places.py``); on
    # miss it falls back to a reverse→forward Nominatim round-trip
    # that normalizes ZIP-centroid or street-specific coords to the
    # city-level centroid appropriate for zmanim. Display name gets
    # refined to "City, State" when the snap returns a clean
    # city/state pair — e.g. "10952, Town of Ramapo, Rockland
    # County, …" becomes "Monsey, NY".
    #
    # The snap helper lives in the package __init__ in this release;
    # import it lazily here (inside the function, at call time) to
    # avoid a circular import — __init__ imports this module at load
    # time. A lazy import is the standard way to break that cycle and
    # adds no runtime cost beyond the first call.
    try:
        from .. import resolve_location_from_coordinates

        city, state, snap_lat, snap_lon, snap_tz = (
            await resolve_location_from_coordinates(
                hass, resolved_raw.latitude, resolved_raw.longitude,
            )
        )
        if city and state:
            final_display = f"{city}, {state}"
        else:
            final_display = resolved_raw.display_name
        resolved = ResolvedLocation(
            latitude=snap_lat,
            longitude=snap_lon,
            tzname=snap_tz or resolved_raw.tzname,
            display_name=final_display,
        )
        _LOGGER.debug(
            "YidCal: geocoded %r → (%s, %s) tz=%s [snapped]",
            query, resolved.latitude, resolved.longitude, resolved.tzname,
        )
    except Exception as exc:  # pragma: no cover - defensive
        # If the snap fails for any reason, fall back to the raw
        # geocode result rather than failing the whole lookup. Wrong-
        # but-close coords beat a hard error here, since the raw
        # Nominatim result is still a valid location for the query.
        _LOGGER.warning(
            "YidCal: location snap failed for %r (%s); using raw "
            "geocode result", query, exc,
        )
        resolved = resolved_raw

    cache[query] = resolved
    return resolved


# ── Coordinate-based snap (used by __init__ at setup time) ─────────────
# v0.7.8 removed the snapping step from resolve_full (manual location
# strings now keep the user's coords instead of normalizing to a city
# centroid), but the AUTO home-location path in __init__ still snaps via
# this function — kept here so __init__'s import keeps working.
async def resolve_location_from_coordinates(
    hass: HomeAssistant,
    latitude: float,
    longitude: float,
) -> tuple[str, str, float, float, str]:
    """Snap raw coords to the canonical city centroid.

    Returns ``(city, state, lat, lon, tzname)``. The snap covers:
      • **Curated community-centroid list** (``places.py``) — first the
        wide custom bbox for Kiryas Joel, then the nearest place within
        ``DEFAULT_RADIUS_KM`` of the input coords. Coordinates here are
        verified against published luachs (KJ cross-verified against
        the South Fallsburg 5786 printed luach to the minute).
      • **Everywhere else** → reverse-geocode the input coords through
        Nominatim to derive city/state, then forward-geocode "City,
        State" to obtain that city's official centroid. This normalizes
        ZIP-centroid or address-specific coords to the city-level
        centroid that's appropriate for zmanim.

    Timezone is looked up from the final (snapped) coords via
    TimezoneFinder, with HA's configured ``time_zone`` as a fallback.

    On geocoding failure, returns the input coords unchanged with
    empty city/state — the caller decides whether that's acceptable.

    Used at integration setup time as well as by the string-based
    ``resolve_location`` so the service's location snapping matches
    HA setup's home-location snapping exactly.
    """
    # 1) Try the curated community-centroid list first. Entries here have
    # coordinates verified against published luachs, so when matched the
    # zmanim are guaranteed to align with the printed luach for that
    # community. Falls through to Nominatim geocoding (below) on miss.
    from .places import find_place
    snap = find_place(latitude, longitude)
    if snap is not None:
        name, state, snap_lat, snap_lon = snap
        return name, state, snap_lat, snap_lon, hass.config.time_zone

    city = ""
    state = ""
    lat = latitude
    lon = longitude
    try:
        # 1) Reverse-lookup → city / state.
        def blocking_lookup():
            from geopy.geocoders import Nominatim
            geolocator = Nominatim(user_agent="yidcal")
            loc = geolocator.reverse(
                (latitude, longitude), language="en", timeout=10,
            )
            addr = loc.raw.get("address", {}) if loc else {}
            city_local = (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("hamlet")
                or ""
            )
            borough = (
                addr.get("city_district")
                or addr.get("borough")
                or addr.get("suburb")
                or addr.get("neighbourhood")
            )
            if city_local == "New York" and borough:
                city_local = borough
            state_local = addr.get("state", "")
            return city_local, state_local

        city, state = await hass.async_add_executor_job(blocking_lookup)

        if not city:
            # Without a city name, the forward query below degrades to just
            # ", <State>" — Nominatim can still "succeed" on that and hand
            # back the STATE centroid, silently relocating the user's zmanim.
            # Treat no-city as a failed snap instead so the except-branch
            # below keeps the raw input coords.
            raise ValueError("reverse geocode returned no city name")

        # 2) Forward-geocode "City, State" → official centroid.
        def blocking_forward():
            from geopy.geocoders import Nominatim
            geolocator = Nominatim(user_agent="yidcal")
            q = f"{city}, {state}"
            loc = geolocator.geocode(q, exactly_one=True, timeout=10)
            if not loc:
                raise ValueError(f"Could not geocode {q!r}")
            return loc.latitude, loc.longitude

        lat, lon = await hass.async_add_executor_job(blocking_forward)
    except Exception as e:
        _LOGGER.warning(
            "YidCal: snap geocoding failed (%s), keeping input coords", e,
        )
        city, state = "", ""
        lat, lon = latitude, longitude

    # 3) Timezone lookup from the (possibly snapped) coords.
    def get_tzname(lat_v: float, lon_v: float) -> str:
        from timezonefinder import TimezoneFinder
        return TimezoneFinder().timezone_at(lng=lon_v, lat=lat_v) or "UTC"

    try:
        tzname = await hass.async_add_executor_job(get_tzname, lat, lon)
    except Exception:
        _LOGGER.warning(
            "YidCal: timezone lookup failed, falling back to HA time_zone",
        )
        tzname = hass.config.time_zone

    return city, state, lat, lon, tzname or "UTC"
