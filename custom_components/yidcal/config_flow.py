import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import DOMAIN

# ============ Existing/general keys (unchanged) ============
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72
DEFAULT_TALLIS_TEFILIN_OFFSET = 22
CONF_INCLUDE_DATE = "include_date"
DEFAULT_DAY_LABEL_LANGUAGE = "yiddish"
CONF_INCLUDE_ATTR_SENSORS = "include_attribute_sensors"
CONF_ENABLE_WEEKLY_YURTZEIT = "enable_weekly_yurtzeit"  # keep key name as-is
CONF_SLICHOS_LABEL_ROLLOVER = "slichos_label_rollover"
DEFAULT_SLICHOS_LABEL_ROLLOVER = "havdalah"
CONF_UPCOMING_LOOKAHEAD_DAYS = "upcoming_lookahead_days"
DEFAULT_UPCOMING_LOOKAHEAD_DAYS = 2
CONF_IS_IN_ISRAEL = "is_in_israel"
DEFAULT_IS_IN_ISRAEL = False
CONF_TIME_FORMAT = "time_format"
DEFAULT_TIME_FORMAT = "12" 

# ============ New Yurtzeit keys ============
CONF_ENABLE_YURTZEIT_DAILY = "enable_yurtzeit_daily"
CONF_YURTZEIT_DATABASES = "yurtzeit_databases"
DEFAULT_YURTZEIT_DATABASES = ["standard"]

# ============ Legacy (for migration/back-compat only) ============
CONF_YAHRTZEIT_DATABASE = "yahrtzeit_database"  # old single-select spelling
DEFAULT_YAHRTZEIT_DATABASE = "standard"


class YidCalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YidCal."""
    VERSION = 1  # no explicit migration necessary

    async def async_step_user(self, user_input=None):
        """Step 1: General settings (first card)."""
        # Only one instance
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_IS_IN_ISRAEL, default=DEFAULT_IS_IN_ISRAEL): bool,
                    vol.Optional("strip_nikud", default=False): bool,
                    vol.Optional("candlelighting_offset", default=DEFAULT_CANDLELIGHT_OFFSET): int,
                    vol.Optional("havdalah_offset", default=DEFAULT_HAVDALAH_OFFSET): int,
                    vol.Optional("tallis_tefilin_offset", default=DEFAULT_TALLIS_TEFILIN_OFFSET): int,
                    vol.Optional(
                        "day_label_language",
                        default=DEFAULT_DAY_LABEL_LANGUAGE,
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "yiddish", "label": "זונטאג, מאנטאג"},
                                {"value": "hebrew",  "label": "יום א', יום ב"},
                            ]
                        }
                    }),
                    vol.Optional(
                        CONF_TIME_FORMAT,
                        default=DEFAULT_TIME_FORMAT,
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "12", "label": "12-hour (AM/PM)"},
                                {"value": "24", "label": "24-hour"},
                            ]
                        }
                    }),
                    vol.Optional(CONF_INCLUDE_DATE, default=False): bool,
                    vol.Optional(CONF_INCLUDE_ATTR_SENSORS, default=True): bool,
                    vol.Optional(
                        CONF_SLICHOS_LABEL_ROLLOVER,
                        default=DEFAULT_SLICHOS_LABEL_ROLLOVER,
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "havdalah", "label": "זמן הבדלה"},
                                {"value": "midnight", "label": "12 AM"},
                            ]
                        }
                    }),
                    vol.Optional(
                        CONF_UPCOMING_LOOKAHEAD_DAYS,
                        default=DEFAULT_UPCOMING_LOOKAHEAD_DAYS,
                    ): selector({
                        "number": {
                            "min": 1,
                            "max": 14,
                            "step": 1,
                            "mode": "slider",
                            "unit_of_measurement": "days",
                        }
                    }),
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        # Stash general config for the final entry
        self._general_data = dict(user_input)
        return await self.async_step_yurtzeit()

    async def async_step_yurtzeit(self, user_input=None):
        """Step 2: Yurtzeit settings (its own card)."""
        errors = {}

        # Defaults for first-time setup
        default_daily = True
        default_weekly = False
        default_dbs = DEFAULT_YURTZEIT_DATABASES

        if user_input is None:
            schema = vol.Schema({
                vol.Optional(CONF_ENABLE_YURTZEIT_DAILY, default=default_daily): bool,
                vol.Optional(CONF_ENABLE_WEEKLY_YURTZEIT, default=default_weekly): bool,
                vol.Optional(CONF_YURTZEIT_DATABASES, default=default_dbs): selector({
                    "select": {
                        "multiple": True,
                        "options": [
                            {"value": "standard", "label": "Standard"},
                            {"value": "satmar",   "label": "Satmar"},
                        ]
                    }
                }),
            })
            return self.async_show_form(step_id="yurtzeit", data_schema=schema)

        # Validation: if either toggle is on, must choose >=1 DB
        enable_daily = user_input.get(CONF_ENABLE_YURTZEIT_DAILY, False)
        enable_weekly = user_input.get(CONF_ENABLE_WEEKLY_YURTZEIT, False)
        dbs = user_input.get(CONF_YURTZEIT_DATABASES, [])

        if (enable_daily or enable_weekly) and not dbs:
            errors["base"] = "select_db_required"
            schema = vol.Schema({
                vol.Optional(CONF_ENABLE_YURTZEIT_DAILY, default=enable_daily): bool,
                vol.Optional(CONF_ENABLE_WEEKLY_YURTZEIT, default=enable_weekly): bool,
                vol.Optional(CONF_YURTZEIT_DATABASES, default=(dbs or DEFAULT_YURTZEIT_DATABASES)): selector({
                    "select": {
                        "multiple": True,
                        "options": [
                            {"value": "standard", "label": "Standard"},
                            {"value": "satmar",   "label": "Satmar"},
                        ]
                    }
                }),
            })
            return self.async_show_form(step_id="yurtzeit", data_schema=schema, errors=errors)

        # Merge and create entry
        data = {
            **getattr(self, "_general_data", {}),
            CONF_ENABLE_YURTZEIT_DAILY: enable_daily,
            CONF_ENABLE_WEEKLY_YURTZEIT: enable_weekly,
            CONF_YURTZEIT_DATABASES: dbs,
        }
        return self.async_create_entry(title="YidCal", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow with a simple menu to split General vs. Yurtzeit."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        # Pass step IDs only; labels are taken from translations:
        # options.step.init.menu_options.general / yurtzeit
        return self.async_show_menu(
            step_id="init",
            menu_options=["general", "yurtzeit"],
        )

    async def async_step_general(self, user_input=None):
        """Edit general settings."""
        data = self._config_entry.data or {}
        opts = self._config_entry.options or {}

        def get(k, default):
            return opts.get(k, data.get(k, default))

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_IS_IN_ISRAEL, default=get(CONF_IS_IN_ISRAEL, DEFAULT_IS_IN_ISRAEL)): bool,
                    vol.Optional("strip_nikud", default=get("strip_nikud", False)): bool,
                    vol.Optional("candlelighting_offset", default=get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET)): int,
                    vol.Optional("havdalah_offset", default=get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET)): int,
                    vol.Optional("tallis_tefilin_offset", default=get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET)): int,
                    vol.Optional(
                        "day_label_language",
                        default=get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE),
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "yiddish", "label": "זונטאג, מאנטאג"},
                                {"value": "hebrew",  "label": "יום א', יום ב"},
                            ]
                        }
                    }),
                    vol.Optional(
                        CONF_TIME_FORMAT,
                        default=get(CONF_TIME_FORMAT, DEFAULT_TIME_FORMAT),
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "12", "label": "12-hour (AM/PM)"},
                                {"value": "24", "label": "24-hour"},
                            ]
                        }
                    }),
                    vol.Optional(CONF_INCLUDE_DATE, default=get(CONF_INCLUDE_DATE, False)): bool,
                    vol.Optional(CONF_INCLUDE_ATTR_SENSORS, default=get(CONF_INCLUDE_ATTR_SENSORS, True)): bool,
                    vol.Optional(
                        CONF_SLICHOS_LABEL_ROLLOVER,
                        default=get(CONF_SLICHOS_LABEL_ROLLOVER, DEFAULT_SLICHOS_LABEL_ROLLOVER),
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "havdalah", "label": "זמן הבדלה"},
                                {"value": "midnight", "label": "12 AM"},
                            ]
                        }
                    }),
                    vol.Optional(
                        CONF_UPCOMING_LOOKAHEAD_DAYS,
                        default=get(CONF_UPCOMING_LOOKAHEAD_DAYS, DEFAULT_UPCOMING_LOOKAHEAD_DAYS),
                    ): selector({
                        "number": {
                            "min": 1,
                            "max": 14,
                            "step": 1,
                            "mode": "slider",
                            "unit_of_measurement": "days",
                        }
                    }),
                }
            )
            return self.async_show_form(step_id="general", data_schema=schema)

        # Merge these edits with existing options, preserving any Yurtzeit fields
        new_opts = {**self._config_entry.options, **user_input}
        return self.async_create_entry(title="", data=new_opts)

    async def async_step_yurtzeit(self, user_input=None):
        """Edit Yurtzeit-specific settings."""
        data = self._config_entry.data or {}
        opts = self._config_entry.options or {}

        # Back-compat defaults
        legacy_db = opts.get(CONF_YAHRTZEIT_DATABASE, data.get(CONF_YAHRTZEIT_DATABASE, DEFAULT_YAHRTZEIT_DATABASE))
        default_dbs = opts.get(
            CONF_YURTZEIT_DATABASES,
            data.get(CONF_YURTZEIT_DATABASES, [legacy_db] if legacy_db else DEFAULT_YURTZEIT_DATABASES),
        )
        default_daily = opts.get(CONF_ENABLE_YURTZEIT_DAILY, data.get(CONF_ENABLE_YURTZEIT_DAILY, True))
        default_weekly = opts.get(CONF_ENABLE_WEEKLY_YURTZEIT, data.get(CONF_ENABLE_WEEKLY_YURTZEIT, False))

        errors = {}
        if user_input is None:
            schema = vol.Schema({
                vol.Optional(CONF_ENABLE_YURTZEIT_DAILY, default=default_daily): bool,
                vol.Optional(CONF_ENABLE_WEEKLY_YURTZEIT, default=default_weekly): bool,
                vol.Optional(CONF_YURTZEIT_DATABASES, default=default_dbs): selector({
                    "select": {
                        "multiple": True,
                        "options": [
                            {"value": "standard", "label": "Standard"},
                            {"value": "satmar",   "label": "Satmar"},
                        ]
                    }
                }),
            })
            return self.async_show_form(step_id="yurtzeit", data_schema=schema)

        enable_daily = user_input.get(CONF_ENABLE_YURTZEIT_DAILY, False)
        enable_weekly = user_input.get(CONF_ENABLE_WEEKLY_YURTZEIT, False)
        dbs = user_input.get(CONF_YURTZEIT_DATABASES, [])

        if (enable_daily or enable_weekly) and not dbs:
            errors["base"] = "select_db_required"
            schema = vol.Schema({
                vol.Optional(CONF_ENABLE_YURTZEIT_DAILY, default=enable_daily): bool,
                vol.Optional(CONF_ENABLE_WEEKLY_YURTZEIT, default=enable_weekly): bool,
                vol.Optional(CONF_YURTZEIT_DATABASES, default=(dbs or DEFAULT_YURTZEIT_DATABASES)): selector({
                    "select": {
                        "multiple": True,
                        "options": [
                            {"value": "standard", "label": "Standard"},
                            {"value": "satmar",   "label": "Satmar"},
                        ]
                    }
                }),
            })
            return self.async_show_form(step_id="yurtzeit", data_schema=schema, errors=errors)

        new_opts = {**self._config_entry.options}
        new_opts[CONF_ENABLE_YURTZEIT_DAILY] = enable_daily
        new_opts[CONF_ENABLE_WEEKLY_YURTZEIT] = enable_weekly
        new_opts[CONF_YURTZEIT_DATABASES] = dbs

        return self.async_create_entry(title="", data=new_opts)
