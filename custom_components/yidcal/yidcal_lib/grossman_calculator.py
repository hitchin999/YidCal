"""
Grossman astronomical calculator for python-zmanim.

Implements the sunrise/sunset algorithm published by Rabbi Yissocher Dov
Grossmann of Antwerpen in his booklet קונטרס קו לקו (Kuntras Kav Le-Kav),
Chapter 4 (latest edition Menachem Av 5784).

This is the algorithm used by Grossmann's "Zmanim" Windows software,
which is the computational basis for many published Chassidic luachs
in the United States, Europe, and Israel — including the Kiryas Joel
(Monroe, NY) luach.

ALGORITHM
=========
Mathematically equivalent to the NOAA/Meeus algorithm but with two
deliberate simplifications:

1. Truncated equation of center (2 terms with constant coefficients):
     A = 0.02·sin(2D) − 1.915·sin(D)
   NOAA uses 3 terms with time-varying coefficients:
     sin(M)·(1.914602 − T·...) + sin(2M)·(0.019993 − ...) + sin(3M)·0.000289

2. Constant obliquity of 23.435° (NOAA computes a polynomial in T).

Additionally, the iteration uses exactly ONE refinement step (not iterate-
until-convergence). This matches the behavior of Grossmann's compiled
binary precisely.

VALIDATION
==========
Validated against the published Kiryas Joel luach for 5786 (data supplied
by the publisher with seconds-precision sunrise, sunset, candle, and
motzei): 100% match at minute precision across all 354 days for every
zman category.

INTERFACE
=========
Drop-in replacement for zmanim.util.noaa_calculator.NOAACalculator.
Returns UTC hours, supports custom zeniths for degree-based zmanim
(alos, tzeis), and respects elevation adjustment via the inherited
adjusted_zenith() machinery.

USAGE
=====
    from yidcal.grossman_calculator import GrossmanCalculator
    from zmanim.zmanim_calendar import ZmanimCalendar

    cal = ZmanimCalendar(geo_location=geo, date=d)
    cal.astronomical_calculator = GrossmanCalculator()
    sunset = cal.sunset()  # uses Grossman's algorithm
"""

import math
from datetime import datetime, date, time
from typing import Optional

from zmanim.util.noaa_calculator import NOAACalculator
from zmanim.util.geo_location import GeoLocation


class GrossmanCalculator(NOAACalculator):
    """Astronomical calculator implementing Rabbi Y.D. Grossmann's algorithm.

    Inherits from NOAACalculator for the elevation-adjustment machinery,
    degree-based zman support, and zenith semantics. Overrides only
    utc_sunrise() and utc_sunset() to use Grossmann's published formulas.

    See module docstring for background and validation results.
    """

    # ---- Constants from קונטרס קו לקו, ch. 4, page 29 ----
    # Epoch: 1 January 2000, 12:00 UT (= Saturday 23 Tevet 5760)
    EPOCH_DATETIME = datetime(2000, 1, 1, 12, 0, 0)

    # Sun's mean longitude at epoch (degrees)
    SUN_MEAN_LON_AT_EPOCH = 280.46572
    # Sun's apsidal longitude (perigee/aphelion ref) at epoch (degrees)
    SUN_APSIDAL_LON_AT_EPOCH = 102.93735
    # Daily increment of mean longitude (deg/day)
    # NOTE: Booklet's constants table prints 0.9856743 but its formula table and
    # worked example use 0.9856473. The latter is correct (matches NOAA's
    # 36000.76983/36525 = 0.98564736...). Confirmed by reproducing the booklet's
    # worked example (Mar 21 2019 Antwerpen sunset) to the exact second.
    SUN_MEAN_LON_DAILY = 0.9856473
    # Daily drift of apsidal longitude (deg/day)
    APSIDAL_DAILY = 0.00004707
    # Obliquity of the ecliptic (degrees) — CONSTANT in Grossmann's algorithm
    OBLIQUITY = 23.435

    @staticmethod
    def name() -> str:
        return "Grossmann Antwerpen Algorithm (Kav Le-Kav)"

    # ---- Overrides of python-zmanim's NOAACalculator ----

    def adjusted_zenith(self, zenith: float, elevation: float) -> float:
        """Adjust zenith for solar radius + refraction (and elevation).

        Override so that for STANDARD sunset/sunrise (zenith == 90°), the
        adjusted zenith is exactly 90.8333° — Grossmann's printed value
        for sun depth R = -0.8333° (κונטרס קו לקו, ch. 4, pg 30 table).

        The base class would use solar_radius (16/60) + refraction (34/60) =
        50/60 ≈ 0.833333…, differing from Grossmann by 0.013s of time at
        mid-latitudes. That's enough to cross the rounding boundary on
        ~1 date per year. Using 0.8333 exactly gives 100% match.

        For non-standard zeniths (custom depth, degree-based alos/tzeis),
        the input zenith is used unchanged — Grossmann's booklet specifies
        those as exact values (e.g., -16.1° for alos).
        """
        if zenith == self.GEOMETRIC_ZENITH:
            return 90.8333 + self.elevation_adjustment(elevation)
        return zenith

    def utc_sunrise(
        self,
        target_date,
        geo_location: GeoLocation,
        zenith: float,
        adjust_for_elevation: bool = False,
    ) -> Optional[float]:
        return self._grossman_event(
            target_date, geo_location, zenith, adjust_for_elevation, mode="sunrise"
        )

    def utc_sunset(
        self,
        target_date,
        geo_location: GeoLocation,
        zenith: float,
        adjust_for_elevation: bool = False,
    ) -> Optional[float]:
        return self._grossman_event(
            target_date, geo_location, zenith, adjust_for_elevation, mode="sunset"
        )

    # ---- Grossman algorithm internals ----

    def _grossman_event(
        self,
        target_date,
        geo_location: GeoLocation,
        zenith: float,
        adjust_for_elevation: bool,
        mode: str,
    ) -> Optional[float]:
        """Compute sunrise or sunset (UTC hours) using Grossmann's algorithm.

        Algorithm: start with T = days-since-epoch at noon UT of target_date,
        compute approximate event time, refine T to that estimate, recompute
        once more. (Single refinement matches Grossmann's binary exactly.)

        Output is rounded to whole seconds (KJ-style) so that python-zmanim's
        downstream microsecond truncation produces values matching the
        published Kiryas Joel luach exactly.
        """
        # Elevation adjustment and zenith adjustment (reuse NOAA machinery)
        elevation = geo_location.elevation if adjust_for_elevation else 0.0
        adjusted_zenith = self.adjusted_zenith(zenith, elevation)
        # Convert effective zenith to depth below horizon (Grossmann's R)
        # zenith 90.8333° (standard sunset) → depth -0.8333°
        # zenith 106.1° (alos 16.1°) → depth -16.1°
        depth = 90.0 - adjusted_zenith

        lat = geo_location.latitude
        # Note: python-zmanim uses east-positive longitude; Grossmann's booklet
        # uses west-positive. Conversion is handled inside _grossman_step.
        lon = geo_location.longitude

        # T₀ = days since J2000 epoch, at noon UT of target date
        if isinstance(target_date, datetime):
            base_dt = target_date.replace(hour=12, minute=0, second=0, microsecond=0)
        else:
            base_dt = datetime.combine(target_date, time(hour=12))
        T0 = (base_dt - self.EPOCH_DATETIME).total_seconds() / 86400.0

        # Initial pass
        utc_hours = self._grossman_step(T0, lat, lon, depth, mode)
        if utc_hours is None:
            return None
        # Single refinement using the event time as T
        T_refined = T0 + (utc_hours - 12.0) / 24.0
        utc_hours = self._grossman_step(T_refined, lat, lon, depth, mode)
        if utc_hours is None:
            return None

        # Round to whole seconds (KJ display rule), implemented as a +0.5 sec
        # offset so that python-zmanim's downstream truncation (in
        # _date_time_from_time_of_day) acts as round-half-up. The Kiryas Joel
        # luach stores values rounded to the nearest integer second; without
        # this offset, fractional seconds in [0.5, 1.0) would truncate down
        # by 1 second and cross the minute-rounding boundary the wrong way
        # on a handful of dates per year.
        #
        # The offset propagates correctly through any subsequent timedelta
        # arithmetic (e.g., candle = sunset − 15 min preserves the offset),
        # so all derived zmanim also match KJ's display rule.
        return ((utc_hours * 3600.0 + 0.5) / 3600.0) % 24

    def _grossman_step(
        self, T: float, lat: float, lon: float, depth: float, mode: str
    ) -> Optional[float]:
        """One iteration step of Grossmann's algorithm.

        T: days since J2000 epoch (fractional)
        lat: latitude in degrees (positive = north)
        lon: longitude in degrees (positive = east)
        depth: sun's required depth below horizon (negative degrees;
               e.g., -0.8333 for standard sunset, -16.1 for alos 16.1°)
        mode: 'sunrise' or 'sunset'

        Returns UTC hours of the event (may exceed [0,24); caller %24's).
        """
        # Step 1: B = sun's mean longitude
        B = (self.SUN_MEAN_LON_AT_EPOCH + self.SUN_MEAN_LON_DAILY * T) % 360

        # Step 2: C = sun's apsidal (perigee) longitude
        C = (self.SUN_APSIDAL_LON_AT_EPOCH + self.APSIDAL_DAILY * T) % 360

        # Step 3: D = mean anomaly (B − C, mod 360)
        D = (B - C) % 360

        # Step 4: A = equation of center (Grossmann: only 2 terms, constant coefs)
        Dr = math.radians(D)
        A = 0.02 * math.sin(2 * Dr) - 1.915 * math.sin(Dr)

        # Step 5: V = true solar longitude
        V = (B + A) % 360
        Vr = math.radians(V)

        # Step 6: N = solar declination (using CONSTANT obliquity)
        eps_r = math.radians(self.OBLIQUITY)
        N = math.degrees(math.asin(math.sin(eps_r) * math.sin(Vr)))
        Nr = math.radians(N)

        # Step 7: E = right ascension component
        # Acos returns [0, 180]; quadrant flip handled via the Acos(cos V) trick
        # in step 8 (sign flip when V > 180, matching the booklet's note about
        # winter sign reversal).
        E_raw = math.degrees(math.acos(math.cos(Vr) / math.cos(Nr)))
        V_acos = math.degrees(math.acos(math.cos(Vr)))  # ramp: V or 360-V

        # Step 8: F = RA − V (with quadrant handling)
        if V > 180:
            F = -(E_raw - V_acos)
        else:
            F = E_raw - V_acos

        # Step 9: O = equation of time (hours)
        O = (F + A) / 15.0

        # Step 10: solar noon, UT hours
        # Booklet uses west-positive longitude (Z = −lon in east-positive),
        # so H_local = 12 + GMT + Z/15 + O.
        # Converted to UTC: H_utc = 12 − lon_east/15 + O.
        H_utc = 12.0 - lon / 15.0 + O

        # Step 11: L = half-day length (hours)
        cos_HA = (
            math.sin(math.radians(depth)) - math.sin(math.radians(lat)) * math.sin(Nr)
        ) / (math.cos(math.radians(lat)) * math.cos(Nr))
        if abs(cos_HA) > 1.0:
            return None  # No sunrise/sunset at this latitude/depth (polar)
        L = math.degrees(math.acos(cos_HA)) / 15.0

        # Step 12: event time
        return H_utc + L if mode == "sunset" else H_utc - L
