# /config/custom_components/yiddish_cal/config_flow.py

"""Config flow for Yiddish Cal."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN

# Default offsets (minutes)
DEFAULT_CANDLELIGHT_OFFSET = 15
DEFAULT_HAVDALAH_OFFSET = 72


class YiddishCalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Yiddish Cal."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    async def async_step_user(self, user_input=None):
        """Initial configuration has no options."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=vol.Schema({})
            )

        return self.async_create_entry(title="Yiddish Cal", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this integration."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Yiddish Cal options."""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Show the form for integration options."""
        if user_input is None:
            schema = vol.Schema(
                {
                    vol.Optional(
                        "strip_nikud", 
                        default=self._config_entry.options.get("strip_nikud", False)
                    ): bool,
                    vol.Optional(
                        "candlelighting_offset",
                        default=self._config_entry.options.get(
                            "candlelighting_offset", DEFAULT_CANDLELIGHT_OFFSET
                        ),
                    ): int,
                    vol.Optional(
                        "havdalah_offset",
                        default=self._config_entry.options.get(
                            "havdalah_offset", DEFAULT_HAVDALAH_OFFSET
                        ),
                    ): int,
                }
            )
            return self.async_show_form(step_id="init", data_schema=schema)

        # User submitted the form: save options
        return self.async_create_entry(title="", data=user_input)
