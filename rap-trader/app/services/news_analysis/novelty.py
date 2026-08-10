"""Deterministic novelty detection for news events.

Identifies duplicate events, updates of known events, and distinguishes first
reports from follow-ups — using deterministic fingerprints only.  No LLM
semantic similarity, no embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.observations import NewsObservation


@dataclass(frozen=True)
class NoveltyResult:
    """The deterministic novelty assessment for an event."""

    fingerprint: str
    is_duplicate: bool
    is_first_report: bool
    is_follow_up: bool
    revision_number: int
    payload_changed: bool
    novelty_score: float  # 1.0 = genuinely new, 0.0 = exact duplicate
    revision_lineage: tuple[str, ...]


class NewsNoveltyService:
    """Track event novelty using deterministic fingerprints.

    The service maintains an internal registry of seen event fingerprints so
    that within a single analysis run, duplicates can be detected.  For
    cross-run novelty, callers should supply a ``known_fingerprints`` set.
    """

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()
        self._seen: set[str] = set()

    def fingerprint(self, observation: NewsObservation) -> str:
        """Compute a deterministic fingerprint for an observation.

        The fingerprint captures the entity, event type, event timestamp,
        and a hash of the structured payload — but NOT the title tokens,
        so that headlines reporting the same event produce the same fingerprint.
        """
        payload = observation.structured_payload or {}
        entity = observation.entity or ""
        event_type = observation.event_type or "other"
        occurred = observation.occurred_at.isoformat() if observation.occurred_at else ""
        # Payload fingerprint excludes volatile fields; we use source_fingerprint
        # to distinguish different source records.
        canonical = {
            "entity": entity.strip().lower(),
            "event_type": event_type.strip().lower(),
            "occurred_at": occurred,
            "payload": _stable_payload(payload),
        }
        return sha256_fingerprint(canonical)

    def assess(
        self,
        observation: NewsObservation,
        known_fingerprints: set[str] | None = None,
        prior_payload: dict[str, Any] | None = None,
    ) -> NoveltyResult:
        """Assess the novelty of an observation.

        Parameters
        ----------
        observation
            The news observation being evaluated.
        known_fingerprints
            Set of fingerprints already seen in this analysis run (or
            previously persisted).  If ``None``, the service's internal
            ``_seen`` set is used.
        prior_payload
            The structured payload of the previous report of the same event,
            if any.  Used to detect payload changes (revisions vs. duplicates).
        """
        registry = known_fingerprints if known_fingerprints is not None else self._seen
        seen_copy = set(registry)

        fingerprint = self.fingerprint(observation)
        is_duplicate = fingerprint in seen_copy
        is_first_report = not is_duplicate and not prior_payload
        is_follow_up = is_duplicate or (prior_payload is not None)

        payload_changed = False
        if prior_payload is not None:
            payload_changed = self._payload_changed(prior_payload, observation.structured_payload or {})

        if is_duplicate:
            novelty_score = 0.0
        elif is_first_report:
            novelty_score = 1.0
        elif payload_changed:
            novelty_score = 0.3  # revision carries new information
        else:
            novelty_score = 0.1  # follow-up without new information

        registry.add(fingerprint)
        if known_fingerprints is None:
            self._seen = registry

        lineage = tuple(sorted({fingerprint, *(fp for fp in seen_copy if self._same_lineage(fp, fingerprint))}))

        return NoveltyResult(
            fingerprint=fingerprint,
            is_duplicate=is_duplicate,
            is_first_report=is_first_report,
            is_follow_up=is_follow_up,
            revision_number=observation.revision_number,
            payload_changed=payload_changed,
            novelty_score=round(novelty_score, 6),
            revision_lineage=lineage,
        )

    def _payload_changed(self, prior: dict[str, Any], current: dict[str, Any]) -> bool:
        """Detect if the structured payload changed materially."""
        prior_stable = _stable_payload(prior)
        current_stable = _stable_payload(current)
        if prior_stable == current_stable:
            return False
        # Check for revision indicator keywords.
        text = " ".join(f"{k} {v}" for k, v in current_stable.items()).lower()
        for indicator in self.config.revision_indicators:
            if indicator in text:
                return True
        # If any numeric value changed by threshold.
        for key in set(prior_stable) & set(current_stable):
            prior_val = _extract_numeric(prior_stable.get(key))
            current_val = _extract_numeric(current_stable.get(key))
            if prior_val is not None and current_val is not None:
                if prior_val != 0:
                    if abs(current_val - prior_val) / abs(prior_val) >= self.config.payload_change_threshold:
                        return True
                elif current_val != prior_val:
                    return True
        return True

    def _same_lineage(self, fp: str, target: str) -> bool:
        """Heuristic: two fingerprints are the same logical event if they share
        a common base.  Since fingerprints are content hashes, we use the
        fingerprint itself as the lineage key — callers that need lineage
        should track it via EventCluster instead."""
        return fp == target

    def reset(self) -> None:
        """Clear the internal seen-fingerprints registry."""
        self._seen = set()


def _stable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce a stable, ordering-independent representation of the payload."""
    result: dict[str, Any] = {}
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, dict):
            result[key] = _stable_payload(value)
        elif isinstance(value, list):
            result[key] = sorted(value, key=str) if value else value
        else:
            result[key] = value
    return result


def _extract_numeric(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            inner = value.get("value")
            if isinstance(inner, (int, float)):
                return float(inner)
    return None
