import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN

# Default offsets (minutes)
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72

# New option key
CONF_INCLUDE_ATTR_SENSORS = "include_attribute_sensors"


class YidCalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for YidCal."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """First step: ask the user for all the options."""
        # ‹— Abort if we already have an entry
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
                    vol.Optional(CONF_INCLUDE_ATTR_SENSORS, default=True): bool,
                }
            )
            return self.async_show_form(step_id="user", data_schema=schema)

        # Once the user submits, store everything in config_entry.data
        data = {
            "strip_nikud": user_input["strip_nikud"],
            "candlelighting_offset": user_input["candlelighting_offset"],
            "havdalah_offset": user_input["havdalah_offset"],
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
        opts = self._config_entry.options
        data = self._config_entry.data

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
                    vol.Optional(
                        CONF_INCLUDE_ATTR_SENSORS,
                        default=include_attrs_default,
                    ): bool,
                }
            )
            return self.async_show_form(step_id="init", data_schema=schema)

        # Save updated options into config_entry.options
        return self.async_create_entry(title="", data=user_input)
