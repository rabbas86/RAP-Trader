"""Deterministic event grouping into clusters.

Multiple records describing one logical event form an ``EventCluster``.
Never deletes individual records — preserves provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import (
    ConfirmationStatus,
    SourceQuality,
)
from app.services.news_analysis.novelty import NewsNoveltyService
from app.services.news_analysis.observations import NewsObservation


@dataclass
class EventCluster:
    """A deterministic group of observations describing one logical event."""

    cluster_id: str
    entity: str | None
    event_type: str
    earliest_available_at: datetime
    latest_available_at: datetime
    records: list[NewsObservation] = field(default_factory=list)
    confirmation_status: ConfirmationStatus = ConfirmationStatus.UNVERIFIED
    aggregate_source_quality: SourceQuality = SourceQuality.UNKNOWN
    contradictions: list[str] = field(default_factory=list)
    canonical_fingerprint: str = ""
    novelty_score: float = 1.0
    is_duplicate: bool = False
    confidence_penalty: float = 0.0


class EventGroupingService:
    """Group observations into deterministic clusters.

    Clustering key: (entity, normalized event_type, day-bucket of occurred_at,
    canonical fingerprint ignoring revision/source).
    """

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()
        self.novelty_service = NewsNoveltyService(config)

    def group(
        self,
        observations: list[NewsObservation],
        as_of: datetime,
    ) -> list[EventCluster]:
        """Group observations into clusters, respecting point-in-time safety.

        Only observations with ``available_at <= as_of`` are included.
        """
        # Point-in-time filter: exclude future events.
        eligible = [obs for obs in observations if obs.available_at <= as_of]
        eligible.sort(key=lambda obs: (obs.available_at, obs.event_id))

        # Group by logical event key.
        groups: dict[tuple[str, ...], list[NewsObservation]] = {}
        for obs in eligible:
            key = self._cluster_key(obs)
            groups.setdefault(key, []).append(obs)

        clusters: list[EventCluster] = []
        for key, group_obs in groups.items():
            cluster = self._build_cluster(key, group_obs, as_of)
            clusters.append(cluster)

        # Sort chronologically by earliest available_at, then cluster_id.
        clusters.sort(key=lambda c: (c.earliest_available_at, c.cluster_id))
        return clusters

    def _cluster_key(self, observation: NewsObservation) -> tuple[str, ...]:
        """Compute the deterministic cluster key for an observation."""
        entity = (observation.entity or "").strip().lower()
        event_type = (observation.event_type or "other").strip().lower()
        # Day-bucket of occurred_at to group same-day reports of the same event.
        occurred = observation.occurred_at or observation.available_at
        day_bucket = occurred.date().isoformat()
        # Canonical fingerprint ignores revision number and source.
        canonical_fp = self._canonical_fingerprint(observation)
        return (entity, event_type, day_bucket, canonical_fp)

    def _canonical_fingerprint(self, observation: NewsObservation) -> str:
        """Fingerprint ignoring revision number and source — identifies the
        logical event itself."""
        payload = _stable_payload(observation.structured_payload or {})
        return sha256_fingerprint(
            {
                "entity": (observation.entity or "").strip().lower(),
                "event_type": (observation.event_type or "other").strip().lower(),
                "occurred_at": (observation.occurred_at.date().isoformat() if observation.occurred_at else "unknown"),
                "payload": payload,
            }
        )

    def _build_cluster(
        self,
        key: tuple[str, ...],
        observations: list[NewsObservation],
        as_of: datetime,
    ) -> EventCluster:
        """Build a single cluster from grouped observations."""
        # Sort observations chronologically.
        sorted_obs = sorted(observations, key=lambda obs: (obs.available_at, obs.event_id))

        cluster_id = str(uuid5(NAMESPACE_URL, f"news-cluster:{':'.join(key)}:{as_of.isoformat()}"))
        canonical_fp = self._canonical_fingerprint(sorted_obs[0])

        # Assess novelty against prior observations in the group.
        known: set[str] = set()
        novelty_results = []
        prior_payload: dict[str, Any] | None = None
        for obs in sorted_obs:
            result = self.novelty_service.assess(obs, known_fingerprints=known, prior_payload=prior_payload)
            novelty_results.append(result)
            if result.is_duplicate:
                prior_payload = obs.structured_payload or {}
            else:
                prior_payload = None

        is_duplicate = any(r.is_duplicate for r in novelty_results)
        novelty_score = sum(r.novelty_score for r in novelty_results) / len(novelty_results) if novelty_results else 0.0

        earliest = min(obs.available_at for obs in sorted_obs)
        latest = max(obs.available_at for obs in sorted_obs)

        # Aggregate source quality — take the best among all records.
        # Observations may already have source_quality set by the service;
        # if not, leave as UNKNOWN.
        quality_order = [
            SourceQuality.UNKNOWN,
            SourceQuality.UNVERIFIED,
            SourceQuality.SECONDARY,
            SourceQuality.HIGH_QUALITY_SECONDARY,
            SourceQuality.PRIMARY,
            SourceQuality.AUTHORITATIVE,
            SourceQuality.CONFLICTING,
        ]
        best_quality = SourceQuality.UNKNOWN
        for obs in sorted_obs:
            sq = getattr(obs, "source_quality", None) or SourceQuality.UNKNOWN
            try:
                if quality_order.index(sq) > quality_order.index(best_quality):
                    best_quality = sq
            except ValueError:
                pass

        return EventCluster(
            cluster_id=cluster_id,
            entity=sorted_obs[0].entity,
            event_type=(sorted_obs[0].event_type or "other").lower(),
            earliest_available_at=earliest,
            latest_available_at=latest,
            records=sorted_obs,
            confirmation_status=self._aggregate_confirmation(sorted_obs),
            aggregate_source_quality=best_quality,
            contradictions=[],
            canonical_fingerprint=canonical_fp,
            novelty_score=round(novelty_score, 6),
            is_duplicate=is_duplicate,
            confidence_penalty=self._compute_confidence_penalty(sorted_obs, is_duplicate),
        )

    @staticmethod
    def _aggregate_confirmation(observations: list[NewsObservation]) -> ConfirmationStatus:
        """Aggregate confirmation status across a cluster's observations."""
        if not observations:
            return ConfirmationStatus.CONFLICTING
        if len(observations) == 1:
            return ConfirmationStatus.UNVERIFIED
        sources = {obs.source for obs in observations}
        if len(sources) == 1:
            return ConfirmationStatus.UNVERIFIED
        # Multiple sources — assume confirmed unless we detect conflicts.
        # Detailed conflict detection is handled by NewsConfirmationService.
        return ConfirmationStatus.CONFIRMED

    @staticmethod
    def _compute_confidence_penalty(observations: list[NewsObservation], is_duplicate: bool) -> float:
        """Compute a confidence penalty [0, 1] for the cluster.

        Penalties are applied for:
        - Single-source events (no independent confirmation).
        - Duplicate events (same headline repeated without new info).
        - Unverified source quality.
        """
        penalty = 0.0
        if len(observations) <= 1:
            penalty += 0.2
        sources = {obs.source for obs in observations}
        if len(sources) == 1 and len(observations) > 1:
            penalty += 0.15
        if is_duplicate:
            penalty += 0.3
        # Check source quality on observations if available.
        for obs in observations:
            sq = getattr(obs, "source_quality", None)
            if sq is SourceQuality.UNVERIFIED or sq is SourceQuality.UNKNOWN:
                penalty += 0.1
                break
        return min(penalty, 1.0)


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


def _minimal_source(source_str: str) -> Any:
    """Create a minimal DataSourceIdentity-like object from a source string."""
    # The observation doesn't carry a full DataSourceIdentity, so this is
    # only used as a fallback.  The classification pipeline sets source_quality
    # on the observation directly when a full source is available.
    return source_str
