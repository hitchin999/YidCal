# custom_components/yidcal/quiet_recorder.py
"""Keep chatty YidCal entities out of the recorder — no user config.

Home Assistant offers no supported way for an integration to exclude
its own entities' STATES from recording (only attributes, via
``_unrecorded_attributes``). For the fast countdown sensors — whose
whole point is a state that ticks every minute — that means a database
row + logbook line per minute, unless every user hand-edits
``recorder:`` in configuration.yaml.

This module does it for them, with a narrowly-scoped, heavily-guarded
runtime wrapper:

  • The recorder instance processes each queued ``state_changed``
    event via ``self._process_state_changed_event_into_session`` —
    looked up on the instance AT CALL TIME (verified against current
    core). We assign an instance-level wrapper that silently drops
    events for the listed entity_ids and delegates everything else
    unchanged. No states rows → nothing in History, nothing in the
    DB-backed Logbook, no database growth.

  • The wrapper is thread-safe (runs on the recorder worker thread,
    does a set lookup + delegate), idempotent across reloads, and
    fully reversible (unload restores the original bound method).

  • If ANY step fails (recorder missing, method renamed in a future
    HA release), we log ONE warning and leave everything untouched —
    the entities then simply record like ordinary sensors (the
    pre-0.7.8 behavior), and YidCal setup is never broken.

The live Logbook stream is handled separately: the countdown sensors
carry a ``unit_of_measurement`` attribute, which the logbook treats as
"continuous sensor" and filters out (see fast_timer_sensors.py).
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_SENTINEL = "_yidcal_quiet_wrapper"


def async_silence_entities(hass: HomeAssistant, entity_ids: set[str]):
    """Drop the given entity_ids from recorder processing.

    Returns an unload callable (safe to call repeatedly)."""
    try:
        from homeassistant.components.recorder import get_instance

        rec = get_instance(hass)
    except Exception:  # noqa: BLE001 — recorder not set up / API moved
        _LOGGER.warning(
            "YidCal: recorder instance not available — fast countdown "
            "sensors will be recorded like normal sensors"
        )
        return lambda: None

    orig = getattr(rec, "_process_state_changed_event_into_session", None)
    if orig is None or not callable(orig):
        _LOGGER.warning(
            "YidCal: recorder internals changed "
            "(_process_state_changed_event_into_session not found) — "
            "fast countdown sensors will be recorded like normal sensors"
        )
        return lambda: None

    if getattr(orig, _SENTINEL, False):
        # Already wrapped (integration reload) — extend the id set.
        orig._yidcal_ids.update(entity_ids)  # type: ignore[attr-defined]

        def _noop() -> None:
            return

        return _noop

    silenced = set(entity_ids)

    def _wrapper(event):  # runs on the recorder worker thread
        try:
            if event.data.get("entity_id") in silenced:
                return
        except Exception:  # noqa: BLE001 — never break the recorder
            pass
        orig(event)

    _wrapper._yidcal_ids = silenced  # type: ignore[attr-defined]
    setattr(_wrapper, _SENTINEL, True)

    try:
        rec._process_state_changed_event_into_session = _wrapper  # noqa: SLF001
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "YidCal: could not attach the recorder filter — fast "
            "countdown sensors will be recorded like normal sensors"
        )
        return lambda: None

    _LOGGER.debug("YidCal: recorder silencing active for %s", silenced)

    def _unload() -> None:
        try:
            cur = getattr(
                rec, "_process_state_changed_event_into_session", None
            )
            if cur is _wrapper:
                # restore the original bound method cleanly
                del rec._process_state_changed_event_into_session  # noqa: SLF001
            else:
                # someone wrapped after us — just neutralize ours
                silenced.clear()
        except Exception:  # noqa: BLE001
            silenced.clear()

    return _unload
