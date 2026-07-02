# Constants for the YidCal integration
DOMAIN = "yidcal"

# ─── Weekly luach feature flag ──────────────────────────────────────
# The weekly-card luach style (one card per Sun→Shabbos week) ships
# DISABLED in this release. To enable it: change False to True below,
# restart Home Assistant, then call the yidcal.generate_luach service
# — the "Weekly (Single Card)" option reappears in the Style
# dropdown with its full descriptions, and the style works again.
# Everything else about the service is unaffected by this flag.
WEEKLY_LUACH_ENABLED: bool = False
