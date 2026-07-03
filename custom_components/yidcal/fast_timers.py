# custom_components/yidcal/fast_timers.py
"""YidCal-shipped fast countdown TIMERS — no user-created helpers.

Creates two real Home Assistant ``timer`` entities:

    timer.yidcal_fast_starts_in   ("Fast Starts In")
    timer.yidcal_fast_ends_in     ("Fast Ends In")

Why timers: a timer's STATE is just ``idle`` / ``active`` — the
second-by-second countdown you see on a dashboard is rendered by the
frontend from the ``finishes_at`` attribute. So users get a LIVE
ticking countdown (per second, smoother than the old per-minute text)
while the recorder/logbook receive only ~2 rows per fast, with no
recorder-exclude configuration needed.

How they're shipped: the ``timer`` domain has no entity platform, so
this file instantiates Home Assistant's own ``Timer`` class via
``Timer.from_yaml()`` (pinned entity_id + unique_id, ``restore: True``
so an active countdown survives restarts, editable=False) and adds the
entities through the timer domain's ``EntityComponent`` — the same
mechanism integrations like Spook use. The component is reached via
``hass.data["entity_components"]["timer"]``, an internal-but-stable
structure; every step below is guarded so a future HA change degrades
to a clear log message instead of breaking YidCal's setup.

Driving: the holiday sensor exposes stable ``fast_starts_at`` /
``fast_ends_at`` ISO attributes (set once per fast window). A listener
on ``sensor.yidcal_holiday`` starts each timer for exactly
``target - now`` when a window appears, leaves it alone while the
target is unchanged (drift tolerance 2s), and cancels if the window
disappears early. When a timer hits zero it finishes by itself and
fires ``timer.finished``; automations can also use ``timer.started`` /
``timer.cancelled``.

The timers are HIDDEN in the entity registry by default — they are
the background engine (and the automation surface via timer.finished
etc.); the visible countdown entities are the two timestamp sensors
in fast_timer_sensors.py. Unhide the timers in the UI to get the
per-second display or manual controls.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
import homeassistant.util.dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

HOLIDAY_ENTITY = "sensor.yidcal_holiday"

# (object_id, name, icon, holiday attribute with the ISO target)
_SPECS = (
    ("yidcal_fast_starts_in", "Fast Starts In", "mdi:timer-sand", "fast_starts_at"),
    ("yidcal_fast_ends_in", "Fast Ends In", "mdi:timer-sand-complete", "fast_ends_at"),
)
_DRIFT_TOLERANCE = timedelta(seconds=2)


async def async_setup_fast_timers(hass: HomeAssistant):
    """Create + drive the two fast timers.

    Returns an unsubscribe callable suitable for
    ``entry.async_on_unload`` (also safe to call on reload).
    """
    # ── 1. Reach the timer component (guarded internal API) ──
    try:
        from homeassistant.components.timer import (
            CONF_DURATION,
            CONF_RESTORE,
            STATUS_ACTIVE,
            Timer,
        )
    except ImportError:
        _LOGGER.error(
            "YidCal fast timers: cannot import the 'timer' component — "
            "fast countdown timers disabled"
        )
        return lambda: None

    component = None
    try:
        # DATA_INSTANCES is a str-subclass HassKey ("entity_components")
        component = hass.data.get("entity_components", {}).get("timer")
    except Exception:  # noqa: BLE001 — internal structure changed
        component = None
    if component is None or not hasattr(component, "async_add_entities"):
        _LOGGER.error(
            "YidCal fast timers: timer EntityComponent not found "
            "(hass.data['entity_components']['timer']) — is 'timer' in "
            "the manifest dependencies? Fast countdown timers disabled"
        )
        return lambda: None

    # ── 2. Skip if already set up (integration reload) ──
    store = hass.data.setdefault(DOMAIN, {}).setdefault("fast_timers", {})
    if store.get("entities"):
        _LOGGER.debug("YidCal fast timers already set up — reusing")

    timers: dict[str, object] = store.get("entities") or {}
    if not timers:
        try:
            from homeassistant.const import CONF_ICON, CONF_ID, CONF_NAME

            entities = []
            for object_id, name, icon, attr_key in _SPECS:
                t = Timer.from_yaml(
                    {
                        CONF_ID: object_id,
                        CONF_NAME: name,
                        CONF_ICON: icon,
                        CONF_DURATION: "0:00:00",
                        CONF_RESTORE: True,
                    }
                )
                entities.append(t)
                timers[attr_key] = t
            await component.async_add_entities(entities)
            store["entities"] = timers
            _LOGGER.info(
                "YidCal fast timers created: %s",
                ", ".join(t.entity_id for t in entities),
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception(
                "YidCal fast timers: creating timer entities failed — "
                "fast countdown timers disabled"
            )
            return lambda: None

    # ── 3. Hide the timers from the UI (background engine) ──
    # The timers keep running, restore across restarts, fire
    # timer.started/finished events and can be unhidden by a user who
    # wants the per-second countdown or Start/Cancel controls — but by
    # default the VISIBLE countdown entities are the two sensors
    # (sensor.yidcal_fast_starts_in / _ends_in, see
    # fast_timer_sensors.py), so the device page shows no stray
    # "Controls" section for them.
    try:
        from homeassistant.helpers import entity_registry as er

        reg = er.async_get(hass)
        for t in timers.values():
            entry = reg.async_get(t.entity_id)
            if entry and entry.hidden_by is None:
                reg.async_update_entity(
                    t.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("YidCal fast timers: hiding timers skipped", exc_info=True)

    # One-time cleanup for installs that ran an earlier 0.7.8 build
    # which DID link the timers to the display device: unlink them.
    try:
        from homeassistant.helpers import entity_registry as er

        ent_reg = er.async_get(hass)
        for t in timers.values():
            entry = ent_reg.async_get(t.entity_id)
            if entry and entry.device_id is not None:
                ent_reg.async_update_entity(t.entity_id, device_id=None)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("YidCal fast timers: unlink skipped", exc_info=True)

    # ── 4. Deliberately NOT linked to any YidCal device ──
    # A hidden entity still shows up grayed-out on its device page;
    # leaving the timers device-less keeps the YidCal device pages
    # clean. They remain findable under Settings → Entities with the
    # "show hidden" filter for anyone who wants the per-second
    # countdown or the Start/Cancel controls.

    # ── 5. Drive the timers from the holiday sensor ──
    @callback
    def _sync(*_args) -> None:
        holiday = hass.states.get(HOLIDAY_ENTITY)
        for attr_key, t in timers.items():
            raw = (holiday.attributes.get(attr_key) or "") if holiday else ""
            target = dt_util.parse_datetime(raw) if raw else None
            t_state = hass.states.get(t.entity_id)
            is_active = bool(t_state and t_state.state == STATUS_ACTIVE)
            try:
                if target is not None:
                    remaining = target - dt_util.utcnow()
                    if remaining <= timedelta(0):
                        continue  # past target: let an active timer self-finish
                    if is_active:
                        cur = (
                            dt_util.parse_datetime(
                                t_state.attributes.get("finishes_at") or ""
                            )
                            if t_state
                            else None
                        )
                        if cur is not None and abs(cur - target) <= _DRIFT_TOLERANCE:
                            continue  # already running toward this target
                    # round to whole seconds so duration displays cleanly
                    t.async_start(timedelta(seconds=round(remaining.total_seconds())))
                elif is_active:
                    t.async_cancel()
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "YidCal fast timers: syncing %s failed", t.entity_id
                )

    unsub_state = async_track_state_change_event(hass, [HOLIDAY_ENTITY], _sync)
    _sync()  # initial alignment (also re-aligns a restored timer after reboot)

    def _unload() -> None:
        unsub_state()
        ents = hass.data.get(DOMAIN, {}).pop("fast_timers", {}).get("entities") or {}
        for t in ents.values():
            try:
                hass.async_create_task(t.async_remove())
            except Exception:  # noqa: BLE001
                pass

    return _unload
