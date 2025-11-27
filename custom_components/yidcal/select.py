# custom_components/yidcal/select.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .config_flow import (
    CONF_ENABLE_EARLY_SHABBOS,
    DEFAULT_ENABLE_EARLY_SHABBOS,
    CONF_ENABLE_EARLY_YOMTOV,
    DEFAULT_ENABLE_EARLY_YOMTOV,
)
from .device import YidCalEarlyDevice

_LOGGER = logging.getLogger(__name__)

# Internal option values (stable keys)
OVERRIDE_AUTO: Final = "auto"
OVERRIDE_FORCE_EARLY: Final = "force_early"
OVERRIDE_FORCE_REGULAR: Final = "force_regular"

OVERRIDE_OPTIONS: Final[list[str]] = [
    OVERRIDE_AUTO,
    OVERRIDE_FORCE_EARLY,
    OVERRIDE_FORCE_REGULAR,
]

# hass.data runtime keys
RUNTIME_DOMAIN_KEY = "runtime"
RUNTIME_OVERRIDES_KEY = "early_overrides"


@dataclass(frozen=True, kw_only=True)
class EarlyOverrideSelectDescription(SelectEntityDescription):
    """Description for Early override selects."""
    runtime_key: str  # e.g. "early_shabbos" or "early_yomtov"


DESCRIPTIONS: Final[list[EarlyOverrideSelectDescription]] = [
    EarlyOverrideSelectDescription(
        key="early_shabbos_override",
        translation_key="early_shabbos_override",  # <— add this
        name="Early Shabbos Override",
        icon="mdi:calendar-clock",
        options=OVERRIDE_OPTIONS,
        runtime_key="early_shabbos",
    ),
    EarlyOverrideSelectDescription(
        key="early_yomtov_override",
        translation_key="early_yomtov_override",  # <— add this
        name="Early Yom Tov Override",
        icon="mdi:calendar-clock-outline",
        options=OVERRIDE_OPTIONS,
        runtime_key="early_yomtov",
    ),
]

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Early override selects."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(RUNTIME_DOMAIN_KEY, {})
    hass.data[DOMAIN][RUNTIME_DOMAIN_KEY].setdefault(RUNTIME_OVERRIDES_KEY, {})

    opts = entry.options or {}
    enable_es = opts.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS)
    enable_ey = opts.get(CONF_ENABLE_EARLY_YOMTOV,  DEFAULT_ENABLE_EARLY_YOMTOV)

    enabled_descs: list[EarlyOverrideSelectDescription] = []
    for desc in DESCRIPTIONS:
        if desc.runtime_key == "early_shabbos" and enable_es:
            enabled_descs.append(desc)
        if desc.runtime_key == "early_yomtov" and enable_ey:
            enabled_descs.append(desc)

    entities: list[SelectEntity] = [
        EarlyOverrideSelect(hass, entry, desc) for desc in enabled_descs
    ]
    async_add_entities(entities)


class EarlyOverrideSelect(YidCalEarlyDevice, RestoreEntity, SelectEntity):
    """Select that overrides early-entry behavior at runtime."""

    entity_description: EarlyOverrideSelectDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: EarlyOverrideSelectDescription,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self.entity_description = description

        slug = description.key
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"select.yidcal_{slug}"

        # default option
        self._attr_current_option = OVERRIDE_AUTO

    @property
    def options(self) -> list[str]:
        return list(self.entity_description.options)

    @property
    def current_option(self) -> str | None:
        return self._attr_current_option
        
    @property
    def available(self) -> bool:
        """Hide/disable the select when its feature is disabled in options."""
        # Prefer config entry options (that’s what the Options UI edits)
        opts = getattr(self._entry, "options", {}) or {}

        # Fallback to hass.data config if you also mirror options there
        cfg = (self.hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}

        def _get_bool(key: str, default: bool) -> bool:
            if key in opts:
                return bool(opts.get(key, default))
            return bool(cfg.get(key, default))

        if self.entity_description.runtime_key == "early_shabbos":
            return _get_bool(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS)

        if self.entity_description.runtime_key == "early_yomtov":
            return _get_bool(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV)

        # If we ever add more selects, don’t accidentally hide them
        return True

    async def async_added_to_hass(self) -> None:
        """Restore previous state if present."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in OVERRIDE_OPTIONS:
            self._attr_current_option = last.state
        else:
            self._attr_current_option = OVERRIDE_AUTO

        # stash into hass.data for sensors to read
        self._write_runtime_override()

        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """User changed the override."""
        if option not in OVERRIDE_OPTIONS:
            _LOGGER.warning("Invalid override option: %s", option)
            return

        self._attr_current_option = option
        self._write_runtime_override()
        self.async_write_ha_state()

    def _write_runtime_override(self) -> None:
        """Persist current override in hass.data."""
        overrides = (
            self.hass.data[DOMAIN]
            .setdefault(RUNTIME_DOMAIN_KEY, {})
            .setdefault(RUNTIME_OVERRIDES_KEY, {})
        )
        overrides[self.entity_description.runtime_key] = self._attr_current_option

    # Convenience helper for other code
    def get_override(self) -> str:
        return self._attr_current_option or OVERRIDE_AUTO
