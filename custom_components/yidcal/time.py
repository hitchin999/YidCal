# custom_components/yidcal/time.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time as dtime
from typing import Final

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN
from .config_flow import (
    CONF_ENABLE_EARLY_SHABBOS,
    DEFAULT_ENABLE_EARLY_SHABBOS,
    CONF_EARLY_SHABBOS_FIXED_TIME,
    DEFAULT_EARLY_SHABBOS_FIXED_TIME,
    CONF_ENABLE_EARLY_YOMTOV,
    DEFAULT_ENABLE_EARLY_YOMTOV,
    CONF_EARLY_YOMTOV_FIXED_TIME,
    DEFAULT_EARLY_YOMTOV_FIXED_TIME,
)
from .device import YidCalEarlyDevice

_LOGGER = logging.getLogger(__name__)

# hass.data runtime keys (mirror select.py)
RUNTIME_DOMAIN_KEY = "runtime"
RUNTIME_FIXED_TIME_KEY = "early_fixed_time"


def _parse_time(s: str, default: str = "19:00:00") -> dtime:
    """Parse 'HH:MM[:SS]' into a time; fall back to default on error."""
    for candidate in (s, default):
        try:
            parts = str(candidate).split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            sec = int(parts[2]) if len(parts) > 2 else 0
            return dtime(hour=h, minute=m, second=sec)
        except Exception:
            continue
    return dtime(19, 0, 0)


@dataclass(frozen=True, kw_only=True)
class EarlyFixedTimeDescription(TimeEntityDescription):
    """Description for the Early fixed-time pickers."""
    runtime_key: str           # "early_shabbos" | "early_yomtov"
    enable_key: str
    enable_default: bool
    config_key: str            # CONF_*_FIXED_TIME (seed value)
    config_default: str


DESCRIPTIONS: Final[list[EarlyFixedTimeDescription]] = [
    EarlyFixedTimeDescription(
        key="early_shabbos_fixed_time",
        translation_key="early_shabbos_fixed_time",
        name="Early Shabbos Fixed Time",
        icon="mdi:clock-edit-outline",
        runtime_key="early_shabbos",
        enable_key=CONF_ENABLE_EARLY_SHABBOS,
        enable_default=DEFAULT_ENABLE_EARLY_SHABBOS,
        config_key=CONF_EARLY_SHABBOS_FIXED_TIME,
        config_default=DEFAULT_EARLY_SHABBOS_FIXED_TIME,
    ),
    EarlyFixedTimeDescription(
        key="early_yomtov_fixed_time",
        translation_key="early_yomtov_fixed_time",
        name="Early Yom Tov Fixed Time",
        icon="mdi:clock-edit",
        runtime_key="early_yomtov",
        enable_key=CONF_ENABLE_EARLY_YOMTOV,
        enable_default=DEFAULT_ENABLE_EARLY_YOMTOV,
        config_key=CONF_EARLY_YOMTOV_FIXED_TIME,
        config_default=DEFAULT_EARLY_YOMTOV_FIXED_TIME,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Early fixed-time pickers."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(RUNTIME_DOMAIN_KEY, {})
    hass.data[DOMAIN][RUNTIME_DOMAIN_KEY].setdefault(RUNTIME_FIXED_TIME_KEY, {})

    opts = entry.options or {}
    enable_es = opts.get(CONF_ENABLE_EARLY_SHABBOS, DEFAULT_ENABLE_EARLY_SHABBOS)
    enable_ey = opts.get(CONF_ENABLE_EARLY_YOMTOV, DEFAULT_ENABLE_EARLY_YOMTOV)

    entities: list[TimeEntity] = []
    for desc in DESCRIPTIONS:
        if desc.runtime_key == "early_shabbos" and enable_es:
            entities.append(EarlyFixedTime(hass, entry, desc))
        if desc.runtime_key == "early_yomtov" and enable_ey:
            entities.append(EarlyFixedTime(hass, entry, desc))

    async_add_entities(entities)


class EarlyFixedTime(YidCalEarlyDevice, RestoreEntity, TimeEntity):
    """Runtime clock-time picker for the early Shabbos / Yom Tov 'fixed' mode.

    The chosen time is used by the Early Start-Time sensor whenever the
    effective method is 'fixed' (configured mode == fixed, or the matching
    'Method' select is set to Force Fixed).
    """

    entity_description: EarlyFixedTimeDescription

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        description: EarlyFixedTimeDescription,
    ) -> None:
        super().__init__()
        self.hass = hass
        self._entry = entry
        self.entity_description = description

        slug = description.key
        self._attr_unique_id = f"yidcal_{slug}"
        self.entity_id = f"time.yidcal_{slug}"

        self._attr_native_value = self._seed_time()

    # ---------------- helpers ----------------

    def _get_cfg(self) -> dict:
        base = self.hass.data.get(DOMAIN, {}).get("config", {}) or {}
        data = getattr(self._entry, "data", None) or {}
        opts = getattr(self._entry, "options", None) or {}
        return {**base, **data, **opts}

    def _seed_time(self) -> dtime:
        cfg = self._get_cfg()
        raw = cfg.get(
            self.entity_description.config_key,
            self.entity_description.config_default,
        )
        return _parse_time(raw, self.entity_description.config_default)

    def _write_runtime(self) -> None:
        store = (
            self.hass.data[DOMAIN]
            .setdefault(RUNTIME_DOMAIN_KEY, {})
            .setdefault(RUNTIME_FIXED_TIME_KEY, {})
        )
        v = self._attr_native_value or self._seed_time()
        store[self.entity_description.runtime_key] = v.strftime("%H:%M:%S")

    # ---------------- entity ----------------

    @property
    def available(self) -> bool:
        opts = getattr(self._entry, "options", {}) or {}
        cfg = (self.hass.data.get(DOMAIN, {}) or {}).get("config", {}) or {}
        key = self.entity_description.enable_key
        default = self.entity_description.enable_default
        if key in opts:
            return bool(opts.get(key, default))
        return bool(cfg.get(key, default))

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in (None, "", "unknown", "unavailable"):
            try:
                self._attr_native_value = _parse_time(
                    last.state, self.entity_description.config_default
                )
            except Exception:
                self._attr_native_value = self._seed_time()
        else:
            self._attr_native_value = self._seed_time()

        self._write_runtime()
        self.async_write_ha_state()

    async def async_set_value(self, value: dtime) -> None:
        self._attr_native_value = value.replace(microsecond=0)
        self._write_runtime()
        self.async_write_ha_state()
