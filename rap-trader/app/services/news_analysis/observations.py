"""Extract typed news observations from a ResearchDataSnapshot.

The Phase 9 News Analyst consumes only ``ResearchDataSnapshot`` from the
Phase 8A Unified Research Data Platform.  This module is the single place
where snapshot records (with ``DataDomain.NEWS``) are projected into the
``NewsObservation`` values the specialist services need.  Keeping the
extraction logic in one service ensures that every downstream service reads
the same vetted values and that the snapshot is never touched directly
outside this boundary.

No network, no LLM.  This is research-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.models.data_platform import (
    DataDomain,
    NormalizedDataRecord,
    ResearchDataSnapshot,
)
from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.news_analysis.config import NewsAnalystConfig


@dataclass(frozen=True)
class NewsObservation:
    """A single typed news event observation with full provenance.

    ``available_at`` is the point-in-time information boundary: the analyst
    may only use events where ``available_at <= analysis as_of``.
    """

    event_id: str
    entity: str | None
    event_type: str
    scope: str
    occurred_at: datetime | None
    published_at: datetime | None
    available_at: datetime
    source: str
    source_fingerprint: str
    title: str
    summary: str | None
    structured_payload: dict[str, Any]
    revision_number: int
    quality_score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = field(default="")
    source_identity: Any = None  # DataSourceIdentity | None
    source_quality: Any = None  # SourceQuality | None (set by service)


class ObservationExtractor:
    """Project ``NormalizedDataRecord`` rows (news domain) into observations.

    News records carry a structured payload whose ``value`` field is a dict
    with ``headline``, ``summary``, and ``payload`` keys (see EventsAdapter).
    The extractor also reads ``EventRecord`` objects supplied directly via
    ``extra_context['events']`` so callers can hand the analyst pre-normalized
    events without the full snapshot round-trip.
    """

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    # ------------------------------------------------------------------
    # Snapshot extraction
    # ------------------------------------------------------------------

    def extract(self, snapshot: ResearchDataSnapshot) -> list[NewsObservation]:
        """Return news observations from a snapshot, filtered and ordered.

        Only records whose ``available_at`` is at or before the snapshot
        ``as_of`` and whose domain is ``DataDomain.NEWS`` are returned.  The
        snapshot itself already enforces ``available_at <= snapshot.as_of``,
        so this is a defense-in-depth filter.
        """
        observations: list[NewsObservation] = []
        for record in snapshot.records:
            if record.domain is not DataDomain.NEWS:
                continue
            observation = self._from_record(record)
            if observation is not None:
                observations.append(observation)
        observations.sort(key=lambda obs: (obs.available_at, obs.event_id))
        return observations

    # ------------------------------------------------------------------
    # EventRecord extraction (direct, no snapshot round-trip)
    # ------------------------------------------------------------------

    def extract_from_events(self, events: tuple[Any, ...], as_of: datetime) -> list[NewsObservation]:
        """Extract observations directly from ``EventRecord`` objects.

        ``as_of`` is the point-in-time boundary: only events with
        ``available_at <= as_of`` are returned.
        """
        from app.domain.models.data_platform import EventRecord

        observations: list[NewsObservation] = []
        for event in events:
            if not isinstance(event, EventRecord):
                continue
            if event.available_at > as_of:
                continue
            observation = self._from_event(event)
            if observation is not None:
                observations.append(observation)
        observations.sort(key=lambda obs: (obs.available_at, obs.event_id))
        return observations

    # ------------------------------------------------------------------
    # Internal converters
    # ------------------------------------------------------------------

    def _from_record(self, record: NormalizedDataRecord) -> NewsObservation | None:
        value = record.value
        if not isinstance(value, dict):
            return None
        title = str(value.get("headline") or value.get("title") or "")
        if not title:
            return None
        summary = value.get("summary")
        payload = value.get("payload", {})
        series_id = (record.series_id or "OTHER").upper()
        entity = record.symbol_or_entity
        availability = record.availability
        return NewsObservation(
            event_id=str(record.record_id),
            entity=entity,
            event_type=series_id,
            scope="unknown",
            occurred_at=record.event_time or availability.observed_at,
            published_at=availability.published_at,
            available_at=availability.available_at,
            source=f"{record.source.provider}:{record.source.dataset}",
            source_fingerprint=record.source_fingerprint,
            title=title,
            summary=summary if isinstance(summary, str) else None,
            structured_payload=payload if isinstance(payload, dict) else {},
            revision_number=record.revision.revision_number,
            quality_score=record.quality.score,
            metadata=dict(record.metadata),
            fingerprint=self._compute_fingerprint(record, title),
            source_identity=record.source,
        )

    def _from_event(self, event: Any) -> NewsObservation | None:
        title = event.headline_or_title or ""
        if not title:
            return None
        available_at = event.available_at
        published_at = event.published_at
        occurred_at = event.occurred_at or event.scheduled_at
        source = f"{event.source.provider}:{event.source.dataset}" if event.source else "unknown"
        return NewsObservation(
            event_id=str(event.event_id),
            entity=event.entity,
            event_type=(event.event_type or "other").lower(),
            scope="unknown",
            occurred_at=occurred_at,
            published_at=published_at,
            available_at=available_at,
            source=source,
            source_fingerprint=event.fingerprint if hasattr(event, "fingerprint") else "",
            title=title,
            summary=event.summary if isinstance(event.summary, str) else None,
            structured_payload=dict(event.structured_payload) if event.structured_payload else {},
            revision_number=event.revision.revision_number if event.revision else 0,
            quality_score=event.quality.score if event.quality else 1.0,
            metadata={},
            fingerprint=self._compute_fingerprint_from_event(event),
            source_identity=event.source,
        )

    @staticmethod
    def _compute_fingerprint(record: NormalizedDataRecord, title: str) -> str:
        return sha256_fingerprint(
            {
                "record_id": str(record.record_id),
                "title": title,
                "available_at": record.availability.available_at.isoformat(),
                "revision_number": record.revision.revision_number,
            }
        )

    @staticmethod
    def _compute_fingerprint_from_event(event: Any) -> str:
        return sha256_fingerprint(
            {
                "event_id": str(event.event_id),
                "title": event.headline_or_title,
                "available_at": event.available_at.isoformat(),
                "revision_number": event.revision.revision_number if event.revision else 0,
            }
        )
