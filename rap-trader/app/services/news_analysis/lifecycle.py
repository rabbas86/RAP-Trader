"""Deterministic event lifecycle handling.

News events — and particularly scheduled events like earnings, central-bank
meetings, and product launches — can transition through lifecycle states:

* ``active``        — the event is ongoing or its outcome is pending.
* ``resolved``      — the event has occurred and its outcome is known.
* ``cancelled``     — a scheduled event was called off or postponed.
* ``superseded``    — a later revision of the same event replaces this one.
* ``archived``     — the event is historical and no longer actionable.

Lifecycle state is read deterministically from the observation's
``structured_payload`` or ``metadata`` dictionaries (keys: ``lifecycle_state``,
``status``, ``event_status``).  No LLM or network is used.

An event that is ``cancelled`` or ``superseded`` is **excluded** from the
synthesis score — its signal does not multiply evidence weight.  An event that
is ``resolved`` or ``archived`` still contributes its outcome but may carry a
lower confidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.news_analysis.observations import NewsObservation


class EventLifecycleState:
    """String constants for event lifecycle states."""

    ACTIVE: str = "active"
    RESOLVED: str = "resolved"
    CANCELLED: str = "cancelled"
    SUPERSEDED: str = "superseded"
    ARCHIVED: str = "archived"

    _ALL: frozenset[str] = frozenset({ACTIVE, RESOLVED, CANCELLED, SUPERSEDED, ARCHIVED})

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value.strip().lower() in cls._ALL


@dataclass(frozen=True)
class LifecycleResult:
    """The deterministic lifecycle assessment for a single news observation."""

    state: str
    is_final: bool
    exclude_from_synthesis: bool
    reason: str


# Keys that may carry the lifecycle state in structured_payload or metadata.
_LIFECYCLE_KEYS: tuple[str, ...] = ("lifecycle_state", "status", "event_status")

# String values that map to each canonical state.
_STATE_ALIASES: dict[str, str] = {
    "active": EventLifecycleState.ACTIVE,
    "ongoing": EventLifecycleState.ACTIVE,
    "pending": EventLifecycleState.ACTIVE,
    "proposed": EventLifecycleState.ACTIVE,
    "resolved": EventLifecycleState.RESOLVED,
    "occurred": EventLifecycleState.RESOLVED,
    "completed": EventLifecycleState.RESOLVED,
    "settled": EventLifecycleState.RESOLVED,
    "cancelled": EventLifecycleState.CANCELLED,
    "canceled": EventLifecycleState.CANCELLED,
    "called_off": EventLifecycleState.CANCELLED,
    "postponed": EventLifecycleState.CANCELLED,
    "deferred": EventLifecycleState.CANCELLED,
    "superseded": EventLifecycleState.SUPERSEDED,
    "replaced": EventLifecycleState.SUPERSEDED,
    "archived": EventLifecycleState.ARCHIVED,
    "historical": EventLifecycleState.ARCHIVED,
    "closed": EventLifecycleState.RESOLVED,
    "final": EventLifecycleState.RESOLVED,
}


def extract_lifecycle_state(payload: dict[str, Any] | None) -> str | None:
    """Read the lifecycle state from a structured payload or metadata dict."""
    if not payload:
        return None
    for key in _LIFECYCLE_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if EventLifecycleState.is_valid(normalized):
                return normalized
            # Try alias lookup for broader coverage.
            if normalized in _STATE_ALIASES:
                return _STATE_ALIASES[normalized]
    return None


def assess_lifecycle(observation: NewsObservation) -> LifecycleResult:
    """Deterministically assess the lifecycle state of a news observation.

    The state is read from the observation's ``structured_payload`` first, then
    ``metadata``.  If no lifecycle key is present, the state defaults to
    ``active`` (the event is assumed to be a one-off that occurred and is
    reportable).
    """
    state = extract_lifecycle_state(observation.structured_payload)
    if state is None:
        state = extract_lifecycle_state(observation.metadata)
    if state is None:
        state = EventLifecycleState.ACTIVE

    normalized = _STATE_ALIASES.get(state, state)

    # Cancelled and superseded events must not contribute to synthesis.
    exclude = normalized in (
        EventLifecycleState.CANCELLED,
        EventLifecycleState.SUPERSEDED,
    )
    # Resolved and archived are final states — the event outcome is known.
    is_final = normalized in (
        EventLifecycleState.RESOLVED,
        EventLifecycleState.ARCHIVED,
    )

    if exclude:
        reason = f"event lifecycle state is '{normalized}' — excluded from synthesis"
    elif is_final:
        reason = f"event lifecycle state is '{normalized}' — outcome is known"
    else:
        reason = f"event lifecycle state is '{normalized}'"

    return LifecycleResult(
        state=normalized,
        is_final=is_final,
        exclude_from_synthesis=exclude,
        reason=reason,
    )


class EventLifecycleService:
    """Service wrapper around :func:`assess_lifecycle`.

    Provided as a class so the service layer can hold a single instance,
    consistent with the other news-analysis services.
    """

    def assess(self, observation: NewsObservation) -> LifecycleResult:
        return assess_lifecycle(observation)
