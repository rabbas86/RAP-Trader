"""Outcome journal service.

Immutable evaluation layer for what happened after a finalized decision.
All future information evaluates T0 and never mutates historical artifacts.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from threading import RLock
from typing import Any, Literal

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType
from app.domain.models.market_data import Symbol
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError
from app.services.market_data.base import MarketDataProvider
from app.services.outcome_journal.errors import (
    OutcomeEvaluationUnsupportedError,
    OutcomeJournalEntryNotFoundError,
    OutcomeJournalQueryError,
    OutcomeJournalTemporalViolationError,
    OutcomeJournalValidationError,
)
from app.services.outcome_journal.models import (
    OUTCOME_SCHEMA_VERSION,
    OutcomeEvaluation,
    OutcomeObservation,
    OutcomeStatus,
)

OBSERVATION_INDEXED_FIELDS = (
    "symbol",
    "decision_artifact_id",
    "decision_journal_entry_id",
    "horizon",
    "outcome_status",
    "decision_at",
    "observation_at",
    "market_data_provider",
)

EVALUATION_INDEXED_FIELDS = (
    "symbol",
    "decision_artifact_id",
    "evaluation_horizon",
    "outcome_status",
    "decision_at",
    "producer_version",
)


class OutcomeJournalService:
    """Immutable durable journal of post-decision observations and evaluations."""

    def __init__(self, store: ArtifactStore, market_data_provider: MarketDataProvider | None = None) -> None:
        self.store = store
        self.market_data_provider = market_data_provider
        self._observation_index: OrderedDict[str, str] = OrderedDict()
        self._evaluation_index: OrderedDict[str, str] = OrderedDict()
        self._observation_secondary_indexes: dict[str, OrderedDict[str, list[str]]] = {
            field: OrderedDict() for field in OBSERVATION_INDEXED_FIELDS
        }
        self._evaluation_secondary_indexes: dict[str, OrderedDict[str, list[str]]] = {
            field: OrderedDict() for field in EVALUATION_INDEXED_FIELDS
        }
        self._lock = RLock()
        self._rebuild_indexes()

    def record_observation(self, observation: OutcomeObservation) -> ArtifactEnvelope:
        """Persist an immutable outcome observation."""
        if not isinstance(observation, OutcomeObservation):
            raise TypeError("observation must be an OutcomeObservation")
        self._verify_temporal_ordering(observation.decision_at, observation.observation_at)
        persisted = self.store.put(observation.envelope())
        with self._lock:
            self._observation_index[observation.observation_id] = persisted.artifact_id
            for field in OBSERVATION_INDEXED_FIELDS:
                value = self._index_value(observation, field)
                index = self._observation_secondary_indexes[field]
                index.setdefault(value, []).append(persisted.artifact_id)
        return persisted

    def record_evaluation(self, evaluation: OutcomeEvaluation) -> ArtifactEnvelope:
        """Persist an immutable outcome evaluation."""
        if not isinstance(evaluation, OutcomeEvaluation):
            raise TypeError("evaluation must be an OutcomeEvaluation")
        persisted = self.store.put(evaluation.envelope())
        with self._lock:
            self._evaluation_index[evaluation.evaluation_id] = persisted.artifact_id
            for field in EVALUATION_INDEXED_FIELDS:
                value = self._index_value(evaluation, field)
                index = self._evaluation_secondary_indexes[field]
                index.setdefault(value, []).append(persisted.artifact_id)
        return persisted

    def evaluate_observation(self, observation_id: str, direction: Literal["BUY", "SELL", "WAIT"]) -> ArtifactEnvelope:
        """Build and persist an evaluation for a completed observation."""
        observation = self.get_observation(observation_id)
        if observation.outcome_status != OutcomeStatus.COMPLETED:
            raise OutcomeJournalValidationError(f"cannot evaluate non-completed observation: {observation.outcome_status.value}")
        if observation.observed_future_price is None:
            raise OutcomeEvaluationUnsupportedError("observation is missing observed future price")

        reference_price = observation.reference_price_at_decision
        future_price = observation.observed_future_price
        raw_return = (future_price - reference_price) / reference_price
        signed_return = raw_return if direction == "BUY" else -raw_return if direction == "SELL" else 0.0
        directionally_correct = signed_return > 0 if direction == "BUY" else signed_return < 0 if direction == "SELL" else True

        evaluation = OutcomeEvaluation(
            evaluation_id=sha256_fingerprint(
                {
                    "schema_version": OUTCOME_SCHEMA_VERSION,
                    "observation_id": observation.observation_id,
                    "decision_artifact_id": observation.decision_artifact_id,
                    "direction": direction,
                    "evaluation_horizon": observation.horizon,
                    "raw_return": raw_return,
                    "signed_return": signed_return,
                    "directionally_correct": directionally_correct,
                    "outcome_status": OutcomeStatus.COMPLETED.value,
                    "producer_version": "1.0",
                }
            ),
            outcome_schema_version=OUTCOME_SCHEMA_VERSION,
            outcome_observation_id=observation.observation_id,
            decision_artifact_id=observation.decision_artifact_id,
            symbol=observation.symbol,
            decision_at=observation.decision_at,
            direction=direction,
            evaluation_horizon=observation.horizon,
            raw_return=raw_return,
            signed_return=signed_return,
            directionally_correct=directionally_correct,
            outcome_status=OutcomeStatus.COMPLETED,
            producer_version="1.0",
        )
        return self.record_evaluation(evaluation)

    def get_observation(self, observation_id: str) -> OutcomeObservation:
        envelope = self._load_observation_envelope(observation_id)
        return OutcomeObservation.model_validate(envelope.payload)

    def get_observation_envelope(self, observation_id: str) -> ArtifactEnvelope:
        return self._load_observation_envelope(observation_id)

    def get_evaluation(self, evaluation_id: str) -> OutcomeEvaluation:
        envelope = self._load_evaluation_envelope(evaluation_id)
        return OutcomeEvaluation.model_validate(envelope.payload)

    def get_evaluation_envelope(self, evaluation_id: str) -> ArtifactEnvelope:
        return self._load_evaluation_envelope(evaluation_id)

    def query_observations(self, **filters: Any) -> list[OutcomeObservation]:
        self._validate_observation_filters(filters)
        with self._lock:
            candidates = self._observation_candidate_ids(filters)
        observations = []
        for artifact_id in candidates:
            envelope = self.store.get(artifact_id)
            observation = OutcomeObservation.model_validate(envelope.payload)
            if self._observation_matches_filters(observation, filters):
                observations.append(observation)
        return observations

    def query_evaluations(self, **filters: Any) -> list[OutcomeEvaluation]:
        self._validate_evaluation_filters(filters)
        with self._lock:
            candidates = self._evaluation_candidate_ids(filters)
        evaluations = []
        for artifact_id in candidates:
            envelope = self.store.get(artifact_id)
            evaluation = OutcomeEvaluation.model_validate(envelope.payload)
            if self._evaluation_matches_filters(evaluation, filters):
                evaluations.append(evaluation)
        return evaluations

    def _verify_temporal_ordering(self, decision_at: datetime, observation_at: datetime) -> None:
        if observation_at <= decision_at:
            raise OutcomeJournalTemporalViolationError("observation_at must be after decision_at")

    def _decision_direction(self, decision_artifact_id: str) -> str:
        envelope = self.store.get(decision_artifact_id)
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        raw_direction = payload.get("action")
        if not isinstance(raw_direction, str) or raw_direction not in {"BUY", "SELL", "WAIT"}:
            raise OutcomeJournalValidationError(f"unsupported decision direction for outcome evaluation: {raw_direction}")
        return raw_direction

    def _load_verified_artifact(self, artifact_id: str, expected_type: ArtifactType, label: str) -> ArtifactEnvelope:
        try:
            envelope = self.store.get(artifact_id)
        except ArtifactNotFoundError as exc:
            raise OutcomeJournalValidationError(f"{label} not found: {exc.artifact_id}") from exc
        except ArtifactCorruptedError as exc:
            raise OutcomeJournalValidationError(f"{label} is corrupted: {exc.reason}") from exc
        if envelope.artifact_type != expected_type:
            raise OutcomeJournalValidationError(f"{label} has wrong artifact type: {envelope.artifact_type.value}")
        return envelope

    def _resolve_observation_id(self, observation_id: str) -> str:
        with self._lock:
            if observation_id in self._observation_index:
                return self._observation_index[observation_id]
            for candidate_id in self._observation_index.values():
                if candidate_id == observation_id:
                    return candidate_id
        raise OutcomeJournalEntryNotFoundError(observation_id)

    def _resolve_evaluation_id(self, evaluation_id: str) -> str:
        with self._lock:
            if evaluation_id in self._evaluation_index:
                return self._evaluation_index[evaluation_id]
            for candidate_id in self._evaluation_index.values():
                if candidate_id == evaluation_id:
                    return candidate_id
        raise OutcomeJournalEntryNotFoundError(evaluation_id)

    def _load_observation_envelope(self, observation_id: str) -> ArtifactEnvelope:
        artifact_id = self._resolve_observation_id(observation_id)
        return self.store.get(artifact_id)

    def _load_evaluation_envelope(self, evaluation_id: str) -> ArtifactEnvelope:
        artifact_id = self._resolve_evaluation_id(evaluation_id)
        return self.store.get(artifact_id)

    def _validate_observation_filters(self, filters: dict[str, Any]) -> None:
        unknown = sorted(set(filters) - set(OBSERVATION_INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise OutcomeJournalQueryError(f"unsupported observation filters: {unknown}")

    def _validate_evaluation_filters(self, filters: dict[str, Any]) -> None:
        unknown = sorted(set(filters) - set(EVALUATION_INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise OutcomeJournalQueryError(f"unsupported evaluation filters: {unknown}")

    def _observation_candidate_ids(self, filters: dict[str, Any]) -> list[str]:
        candidates = None
        for field, value in filters.items():
            if field == "limit":
                continue
            index = self._observation_secondary_indexes[field]
            normalized = self._index_value_for_filter(value)
            ids = index.get(normalized, [])
            if candidates is None:
                candidates = OrderedDict.fromkeys(ids)
            else:
                candidates = OrderedDict.fromkeys(candidate for candidate in candidates if candidate in ids)
        return list(candidates or self._observation_index.values())

    def _evaluation_candidate_ids(self, filters: dict[str, Any]) -> list[str]:
        candidates = None
        for field, value in filters.items():
            if field == "limit":
                continue
            index = self._evaluation_secondary_indexes[field]
            normalized = self._index_value_for_filter(value)
            ids = index.get(normalized, [])
            if candidates is None:
                candidates = OrderedDict.fromkeys(ids)
            else:
                candidates = OrderedDict.fromkeys(candidate for candidate in candidates if candidate in ids)
        return list(candidates or self._evaluation_index.values())

    def _observation_matches_filters(self, observation: OutcomeObservation, filters: dict[str, Any]) -> bool:
        return all(
            self._index_value(observation, field) == self._index_value_for_filter(value)
            for field, value in filters.items()
            if field != "limit"
        )

    def _evaluation_matches_filters(self, evaluation: OutcomeEvaluation, filters: dict[str, Any]) -> bool:
        return all(
            self._index_value(evaluation, field) == self._index_value_for_filter(value)
            for field, value in filters.items()
            if field != "limit"
        )

    def _index_value_for_filter(self, value: Any) -> str:
        if isinstance(value, Symbol):
            return value.root
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, OutcomeStatus):
            return value.value
        return str(value)

    def _index_value(self, obj: Any, field: str) -> str:
        value = getattr(obj, field)
        if isinstance(value, Symbol):
            return value.root
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, OutcomeStatus):
            return value.value
        return str(value)

    def _rebuild_indexes(self) -> None:
        for artifact_id in self.store.list_ids(filters={"artifact_type": ArtifactType.OUTCOME_OBSERVATION}):
            envelope = self.store.get(artifact_id)
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            observation_id = payload.get("observation_id")
            if not isinstance(observation_id, str) or not observation_id:
                raise OutcomeJournalValidationError("observation payload is missing observation_id")
            self._observation_index[observation_id] = artifact_id
            for field in OBSERVATION_INDEXED_FIELDS:
                value = payload.get(field)
                if value is None:
                    continue
                self._observation_secondary_indexes[field].setdefault(str(value), []).append(artifact_id)

        for artifact_id in self.store.list_ids(filters={"artifact_type": ArtifactType.OUTCOME_EVALUATION}):
            envelope = self.store.get(artifact_id)
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            evaluation_id = payload.get("evaluation_id")
            if not isinstance(evaluation_id, str) or not evaluation_id:
                raise OutcomeJournalValidationError("evaluation payload is missing evaluation_id")
            self._evaluation_index[evaluation_id] = artifact_id
            for field in EVALUATION_INDEXED_FIELDS:
                value = payload.get(field)
                if value is None:
                    continue
                self._evaluation_secondary_indexes[field].setdefault(str(value), []).append(artifact_id)


__all__ = ["OutcomeJournalService"]
