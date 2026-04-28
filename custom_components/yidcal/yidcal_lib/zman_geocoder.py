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

    resolved = await hass.async_add_executor_job(_blocking_resolve)
    cache[query] = resolved
    _LOGGER.debug(
        "YidCal: geocoded %r → (%s, %s) tz=%s",
        query, resolved.latitude, resolved.longitude, resolved.tzname,
    )
    return resolved
