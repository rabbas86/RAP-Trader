"""Deterministic confirmation assessment across multiple news records.

Given multiple structured records describing the same logical event, determines
whether the event is confirmed, partially confirmed, conflicting, unverified,
or superseded.  Uses source identity, revision relationships, timestamps, and
structured-payload agreement — no LLM reasoning.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.services.news_analysis.domain import ConfirmationStatus
from app.services.news_analysis.observations import NewsObservation


@dataclass(frozen=True)
class ConfirmationResult:
    """The confirmation state for a cluster of observations."""

    status: ConfirmationStatus
    source_count: int
    agreeing_sources: tuple[str, ...]
    conflicting_sources: tuple[str, ...]
    has_revision_lineage: bool
    latest_revision_number: int
    conflict_fields: tuple[str, ...]


class NewsConfirmationService:
    """Determine confirmation status deterministically from structured records."""

    # Fields in the structured payload that are compared for agreement.
    _COMPARE_FIELDS: tuple[str, ...] = (
        "direction",
        "surprise",
        "eps",
        "revenue",
        "guidance_low",
        "guidance_high",
    )

    def assess(self, observations: list[NewsObservation]) -> ConfirmationResult:
        """Assess confirmation status for a group of observations about one event.

        Rules:
        - 0 observations → CONFLICTING (no data)
        - 1 observation → UNVERIFIED (single source)
        - Multiple observations from the same source → UNVERIFIED (not confirmed
          by independent sources)
        - Multiple observations from different sources that agree → CONFIRMED
        - Multiple observations from different sources that partially agree → PARTIALLY_CONFIRMED
        - Multiple observations from different sources that disagree → CONFLICTING
        - A later revision supersedes earlier ones → SUPERSEDED
        """
        if not observations:
            return ConfirmationResult(
                status=ConfirmationStatus.CONFLICTING,
                source_count=0,
                agreeing_sources=(),
                conflicting_sources=(),
                has_revision_lineage=False,
                latest_revision_number=0,
                conflict_fields=(),
            )

        sources = tuple({obs.source for obs in observations})
        source_count = len(sources)
        latest_revision = max(obs.revision_number for obs in observations)
        has_lineage = any(obs.revision_number > 0 for obs in observations)

        # Check for revision superseding.
        if has_lineage:
            revisions = sorted(observations, key=lambda obs: obs.revision_number)
            latest = revisions[-1]
            earlier = revisions[0]
            if self._is_superseded(latest, earlier):
                return ConfirmationResult(
                    status=ConfirmationStatus.SUPERSEDED,
                    source_count=source_count,
                    agreeing_sources=sources,
                    conflicting_sources=(),
                    has_revision_lineage=True,
                    latest_revision_number=latest_revision,
                    conflict_fields=(),
                )

        if source_count == 1 or not has_lineage and source_count == 1:
            return ConfirmationResult(
                status=ConfirmationStatus.UNVERIFIED,
                source_count=source_count,
                agreeing_sources=sources,
                conflicting_sources=(),
                has_revision_lineage=has_lineage,
                latest_revision_number=latest_revision,
                conflict_fields=(),
            )

        # Multiple independent sources — check agreement.
        agreement = self._check_agreement(observations)
        if agreement.conflict_count == 0:
            return ConfirmationResult(
                status=ConfirmationStatus.CONFIRMED,
                source_count=source_count,
                agreeing_sources=sources,
                conflicting_sources=(),
                has_revision_lineage=has_lineage,
                latest_revision_number=latest_revision,
                conflict_fields=(),
            )
        if agreement.conflict_count < source_count:
            return ConfirmationResult(
                status=ConfirmationStatus.PARTIALLY_CONFIRMED,
                source_count=source_count,
                agreeing_sources=agreement.aggreeing,
                conflicting_sources=agreement.conflicting,
                has_revision_lineage=has_lineage,
                latest_revision_number=latest_revision,
                conflict_fields=agreement.conflict_fields,
            )
        return ConfirmationResult(
            status=ConfirmationStatus.CONFLICTING,
            source_count=source_count,
            agreeing_sources=(),
            conflicting_sources=sources,
            has_revision_lineage=has_lineage,
            latest_revision_number=latest_revision,
            conflict_fields=agreement.conflict_fields,
        )

    def _is_superseded(self, latest: NewsObservation, earlier: NewsObservation) -> bool:
        """A later revision with different payload supersedes the earlier one."""
        if latest.revision_number <= earlier.revision_number:
            return False
        if latest.available_at <= earlier.available_at:
            return False
        # If the latest revision has a different fingerprint from the earliest,
        # and the latest is a proper revision (revision_number > 0).
        return latest.fingerprint != earlier.fingerprint

    def _check_agreement(self, observations: list[NewsObservation]) -> _AgreementResult:
        """Check if observations from different sources agree on key fields."""
        agreeing: set[str] = set()
        conflicting: set[str] = set()
        conflict_fields: set[str] = set()
        conflict_count = 0

        for field in self._COMPARE_FIELDS:
            values = defaultdict(set)
            for obs in observations:
                payload = obs.structured_payload or {}
                if field in payload:
                    values[str(payload[field])].add(obs.source)
            if len(values) > 1:
                conflict_count += 1
                conflict_fields.add(field)
                for source_set in values.values():
                    conflicting.update(source_set)
            elif len(values) == 1:
                agreeing.update(next(iter(values.values())))

        return _AgreementResult(
            aggreeing=tuple(sorted(agreeing)),
            conflicting=tuple(sorted(conflicting)),
            conflict_count=conflict_count,
            conflict_fields=tuple(sorted(conflict_fields)),
        )


@dataclass(frozen=True)
class _AgreementResult:
    aggreeing: tuple[str, ...]
    conflicting: tuple[str, ...]
    conflict_count: int
    conflict_fields: tuple[str, ...]
