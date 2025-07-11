import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import DOMAIN

# Default offsets (minutes)
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72
DEFAULT_TALLIS_TEFILIN_OFFSET = 22  # minutes after Alos
CONF_INCLUDE_DATE = "include_date"
DEFAULT_DAY_LABEL_LANGUAGE = "yiddish"



# New option key
CONF_INCLUDE_ATTR_SENSORS = "include_attribute_sensors"


class YidCalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YidCal."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """First step: ask the user for all the options."""
        # Abort if we already have an entry
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional("strip_nikud", default=False): bool,
                    vol.Optional(
                        "candlelighting_offset", default=DEFAULT_CANDLELIGHT_OFFSET
                    ): int,
                    vol.Optional(
                        "havdalah_offset", default=DEFAULT_HAVDALAH_OFFSET
                    ): int,
                    vol.Optional(
                        "tallis_tefilin_offset", default=DEFAULT_TALLIS_TEFILIN_OFFSET
                    ): int,
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
                    vol.Optional(CONF_INCLUDE_DATE, default=False): bool,
                    vol.Optional(CONF_INCLUDE_ATTR_SENSORS, default=True): bool,
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        # Once the user submits, store everything in config_entry.data
        data = {
            "strip_nikud": user_input["strip_nikud"],
            "candlelighting_offset": user_input["candlelighting_offset"],
            "havdalah_offset": user_input["havdalah_offset"],
            "tallis_tefilin_offset": user_input["tallis_tefilin_offset"],
            "day_label_language": user_input["day_label_language"],
            CONF_INCLUDE_DATE:        user_input[CONF_INCLUDE_DATE],
            CONF_INCLUDE_ATTR_SENSORS: user_input[CONF_INCLUDE_ATTR_SENSORS],
        }
        return self.async_create_entry(title="YidCal", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this integration (if user wants to change later)."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle YidCal options (after install)."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Show the form to adjust options after setup."""
        opts = self._config_entry.options or {}
        data = self._config_entry.data or {}

        strip_nikud_default = opts.get(
            "strip_nikud", data.get("strip_nikud", False)
        )
        candle_offset_default = opts.get(
            "candlelighting_offset",
            data.get("candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET),
        )
        havdala_offset_default = opts.get(
            "havdalah_offset",
            data.get("havdalah_offset", DEFAULT_HAVDALAH_OFFSET),
        )
        tallis_default = opts.get(
            "tallis_tefilin_offset",
            data.get("tallis_tefilin_offset", DEFAULT_TALLIS_TEFILIN_OFFSET),
        )
        day_label_default = opts.get(
            "day_label_language",
            data.get("day_label_language", DEFAULT_DAY_LABEL_LANGUAGE),
        )
        include_date_default = opts.get(
            CONF_INCLUDE_DATE,
            data.get(CONF_INCLUDE_DATE, False),
        )
        include_attrs_default = opts.get(
            CONF_INCLUDE_ATTR_SENSORS,
            data.get(CONF_INCLUDE_ATTR_SENSORS, True),
        )
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional("strip_nikud", default=strip_nikud_default): bool,
                    vol.Optional(
                        "candlelighting_offset", default=candle_offset_default
                    ): int,
                    vol.Optional("havdalah_offset", default=havdala_offset_default): int,
                    vol.Optional("tallis_tefilin_offset", default=tallis_default): int,
                    vol.Optional(
                        "day_label_language",
                        default=day_label_default,
                    ): selector({
                        "select": {
                            "options": [
                                {"value": "yiddish", "label": "זונטאג, מאנטאג"},
                                {"value": "hebrew",  "label": "יום א', יום ב"},
                            ]
                        }
                    }),
                    vol.Optional(
                        CONF_INCLUDE_DATE,
                        default=include_date_default,
                    ): bool,
                    vol.Optional(
                        CONF_INCLUDE_ATTR_SENSORS,
                        default=include_attrs_default,
                    ): bool,
                }
            )
            return self.async_show_form(step_id="init", data_schema=schema)

        # Save updated options into config_entry.options
        return self.async_create_entry(title="", data=user_input)
        
