"""Phase 17A forward data ingestion service.

The service ingests forward observations through provider-neutral adapters,
validates/normalizes them, persists immutable artifacts, and journals receipt
history. It does not place orders, connect brokers, or make trading decisions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.forward_data import (
    ForwardDataSession,
    ForwardDataSource,
    ForwardIngestionResult,
    ForwardMarketObservation,
)
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError
from app.services.forward_data.errors import (
    DuplicateConflictError,
    ForwardDataServiceError,
    UnsupportedTimeframeError,
    WrongSymbolError,
)
from app.services.forward_data.journal import ForwardDataJournal
from app.services.forward_data.providers import FakeForwardDataProvider, SimulatedForwardDataProvider
from app.services.forward_data.validation import (
    validate_duplicate_conflict,
    validate_event_interval,
    validate_market_bar_prices,
    validate_observation_type,
    validate_source,
    validate_timeframe,
    validate_timestamp,
)


class ForwardDataIngestionService:
    """Provider-neutral forward data ingestion service.

    The service owns canonical validation/normalization, artifact persistence,
    and append-only journaling. Network access is confined to provider
    adapters. Tests use fake/simulated providers only.
    """

    def __init__(self, store: Any, journal: ForwardDataJournal | None = None) -> None:
        self.store = store
        self.journal = journal or ForwardDataJournal(store)

    def ingest_fake(
        self,
        *,
        source: ForwardDataSource,
        session: ForwardDataSession,
        observations: Sequence[ForwardMarketObservation],
    ) -> ForwardIngestionResult:
        provider = FakeForwardDataProvider(observations)
        return self._ingest(source=source, session=session, provider=provider)

    def ingest_simulated(
        self,
        *,
        source: ForwardDataSource,
        session: ForwardDataSession,
        observations: Sequence[ForwardMarketObservation],
    ) -> ForwardIngestionResult:
        provider = SimulatedForwardDataProvider(observations)
        return self._ingest(source=source, session=session, provider=provider)

    def _ingest(
        self,
        *,
        source: ForwardDataSource,
        session: ForwardDataSession,
        provider: Any,
    ) -> ForwardIngestionResult:
        validate_source(source)
        self.journal.ensure_rebuilt()

        observations = provider.poll(source=source)
        accepted: list[ForwardMarketObservation] = []
        accepted_ids: list[str] = []
        artifact_ids: list[str] = []
        warnings: list[str] = []
        duplicate_count = 0
        rejected_count = 0
        conflict_count = 0

        received_timestamps: list[datetime] = []

        for observation in observations:
            try:
                normalized = self._validate_and_normalize(observation, source=source, session=session)
            except ForwardDataServiceError:
                rejected_count += 1
                continue

            try:
                validate_timeframe(normalized.timeframe)
            except UnsupportedTimeframeError:
                rejected_count += 1
                continue

            validate_event_interval(
                interval_start=normalized.interval_start,
                interval_end=normalized.interval_end,
                event_time=normalized.event_time,
            )
            validate_observation_type(normalized.observation_type)
            if normalized.observation_type == "market_bar":
                validate_market_bar_prices(
                    open_=normalized.open,
                    high=normalized.high,
                    low=normalized.low,
                    close=normalized.close,
                    volume=normalized.volume,
                )

            if normalized.symbol.root != session.instruments[0].root:
                rejected_count += 1
                continue

            existing_artifact_id = self._existing_artifact_id(normalized)
            if existing_artifact_id is not None:
                incoming_hash = self._payload_hash(normalized)
                try:
                    validate_duplicate_conflict(
                        self.store.get(existing_artifact_id).payload_hash,
                        incoming_hash,
                        normalized.observation_id,
                    )
                except DuplicateConflictError:
                    conflict_count += 1
                    continue
                duplicate_count += 1
                accepted.append(normalized)
                accepted_ids.append(normalized.observation_id)
                artifact_ids.append(existing_artifact_id)
                continue

            envelope = self._persist(normalized, session=session)
            accepted.append(normalized)
            accepted_ids.append(normalized.observation_id)
            artifact_ids.append(envelope.artifact_id)
            self.journal.append(normalized)
            received_timestamps.append(normalized.received_at)

        receipt_window_start = min(received_timestamps) if received_timestamps else None
        receipt_window_end = max(received_timestamps) if received_timestamps else None
        result = ForwardIngestionResult.create(
            session_id=session.session_id,
            source_id=source.source_id,
            accepted_count=len(accepted),
            duplicate_count=duplicate_count,
            rejected_count=rejected_count,
            conflict_count=conflict_count,
            accepted_observation_ids=accepted_ids,
            persisted_artifact_ids=artifact_ids,
            receipt_window_start=receipt_window_start,
            receipt_window_end=receipt_window_end,
            warnings=warnings,
        )
        self._persist_result(result, session=session)
        return result

    def _validate_and_normalize(
        self, observation: ForwardMarketObservation, *, source: ForwardDataSource, session: ForwardDataSession
    ) -> ForwardMarketObservation:
        validate_timestamp(observation.event_time, "event_time")
        validate_timestamp(observation.received_at, "received_at")
        validate_timestamp(observation.normalized_at, "normalized_at")
        if observation.provider_available_at is not None:
            validate_timestamp(observation.provider_available_at, "provider_available_at")
        validate_timeframe(observation.timeframe)
        validate_observation_type(observation.observation_type)
        if observation.observation_type == "market_bar":
            validate_market_bar_prices(
                open_=observation.open,
                high=observation.high,
                low=observation.low,
                close=observation.close,
                volume=observation.volume,
            )
        if observation.symbol.root != session.instruments[0].root:
            raise WrongSymbolError("Symbol mismatch for session.")
        return observation

    def _existing_artifact_id(self, observation: ForwardMarketObservation) -> str | None:
        expected_hash = observation.observation_hash
        for artifact_id in self.store.list_ids(filters={"artifact_type": "forward_data_observation"}):
            try:
                envelope = self.store.get(artifact_id)
            except (ArtifactNotFoundError, ArtifactCorruptedError):
                continue
            if envelope.payload.get("observation_hash") == expected_hash:
                return artifact_id  # type: ignore[no-any-return]
        return None  # type: ignore[no-any-return]

    def _persist(self, observation: ForwardMarketObservation, session: ForwardDataSession) -> ArtifactEnvelope:
        provenance = (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
                identifier=session.session_id,
                description="forward validation session",
                producer="phase17a",
                producer_version="1.0",
            ),
        )
        envelope = ArtifactEnvelope.create(
            payload=observation.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.FORWARD_DATA_OBSERVATION,
            logical_as_of=observation.received_at,
            producer_version="1.0",
            provenance_references=provenance,
        )
        return self.store.put(envelope)  # type: ignore[no-any-return]

    def _persist_result(self, result: ForwardIngestionResult, session: ForwardDataSession) -> None:
        provenance = (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
                identifier=session.session_id,
                description="forward validation session",
                producer="phase17a",
                producer_version="1.0",
            ),
        )
        envelope = ArtifactEnvelope.create(
            payload=result.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.FORWARD_INGESTION_RESULT,
            logical_as_of=result.receipt_window_end or datetime.fromtimestamp(0, tz=UTC),
            producer_version="1.0",
            provenance_references=provenance,
        )
        self.store.put(envelope)

    @staticmethod
    def _payload_hash(observation: ForwardMarketObservation) -> str:
        from app.domain.canonical import sha256_fingerprint

        return sha256_fingerprint(observation.model_dump(mode="json", exclude_none=False))


__all__ = ["ForwardDataIngestionService"]
