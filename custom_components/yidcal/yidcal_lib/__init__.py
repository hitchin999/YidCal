"""Vendored YidCal helper library.

Importing this package applies a one-time monkey-patch to python-zmanim's
``AstronomicalCalendar.__init__`` so that every ``ZmanimCalendar(...)`` (and
any other ``AstronomicalCalendar`` subclass) instantiated anywhere in YidCal
— or in any code that runs in the same process — uses YidCal's
``GrossmanCalculator`` as the default astronomical calculator instead of
python-zmanim's stock ``NOAACalculator``.

WHY A MONKEY-PATCH AND NOT EDITS AT EVERY CALL SITE
---------------------------------------------------
YidCal instantiates ``ZmanimCalendar(...)`` in well over 100 places across
60+ files. Editing each one individually risks a silent regression: a single
missed site would quietly fall back to NOAACalculator and produce times that
don't match the Kiryas Joel printed luach. A single, idempotent class-level
patch keeps the default behavior consistent for every current and future
call site.

If a future contributor needs the unpatched (NOAA) behavior in one specific
place, they can still pass an explicit ``calculator=NOAACalculator()`` —
the patch only fills in the default when ``calculator is None``.

If you are touching anything in this monkey-patch or in
``grossman_calculator.py``, please run the full-year Kiryas Joel regression
test before opening a PR. The 100% minute-precision match is the contract
this module exists to preserve.
"""
from zmanim.astronomical_calendar import AstronomicalCalendar

from .grossman_calculator import GrossmanCalculator
from .helper import YidCalHelper

# Apply the patch exactly once, even if this package is imported repeatedly
# (Python caches modules in sys.modules, but defensive coding doesn't hurt
# when integration reloads come into the picture).
if not getattr(AstronomicalCalendar, "_yidcal_grossman_patched", False):
    _original_init = AstronomicalCalendar.__init__

    def _patched_init(self, *args, **kwargs):
        # AstronomicalCalendar.__init__ signature:
        #     (self, geo_location=None, date=None, calculator=None)
        # ZmanimCalendar.__init__ forwards *args, **kwargs to super.
        # If the caller didn't supply a calculator, substitute GrossmanCalculator.
        if kwargs.get("calculator") is None and not (len(args) >= 3 and args[2] is not None):
            kwargs["calculator"] = GrossmanCalculator()
        _original_init(self, *args, **kwargs)

    AstronomicalCalendar.__init__ = _patched_init
    AstronomicalCalendar._yidcal_grossman_patched = True


__all__ = ["YidCalHelper", "GrossmanCalculator"]
