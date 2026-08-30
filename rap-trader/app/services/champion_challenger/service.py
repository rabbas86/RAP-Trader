"""Deterministic champion/challenger evaluation service."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.champion_challenger.errors import (
    ChampionChallengerCorruptionError,
    ChampionChallengerError,
    ChampionChallengerNotFoundError,
    ChampionChallengerQueryError,
    ChampionChallengerValidationError,
)
from app.services.champion_challenger.models import (
    CHAMPION_CHALLENGER_SCHEMA_VERSION,
    ChampionChallengerEvaluation,
    ComparisonAssumptions,
    EvaluationMetrics,
    EvaluationRecommendation,
    ModelIdentity,
)

INDEXED_FIELDS = (
    "champion_model_id",
    "challenger_model_id",
    "evaluation_period",
    "instruments",
    "horizon",
    "recommendation",
    "producer_version",
)


class ChampionChallengerService:
    """Research-only champion/challenger evaluation service."""

    def __init__(self, store: ArtifactStore, producer_version: str = "rap-trader-champion-challenger-1.0") -> None:
        self.store = store
        self.producer_version = producer_version
        self._index: OrderedDict[str, str] = OrderedDict()
        self._secondary_indexes: dict[str, OrderedDict[str, list[str]]] = {field: OrderedDict() for field in INDEXED_FIELDS}
        self._lock = RLock()
        self._rebuild_indexes()

    def evaluate(
        self,
        *,
        champion_identity: dict[str, Any],
        challenger_identity: dict[str, Any],
        evaluation_period: str,
        instruments: list[str],
        horizon: int,
        champion_metrics: EvaluationMetrics,
        challenger_metrics: EvaluationMetrics,
        comparison_assumptions: ComparisonAssumptions,
        logical_as_of: datetime | None = None,
        recommendation: EvaluationRecommendation | str | None = None,
        producer_version: str | None = None,
    ) -> tuple[ChampionChallengerEvaluation, ArtifactEnvelope]:
        if logical_as_of is None:
            logical_as_of = self._evaluation_period_as_of(evaluation_period)
        if not champion_identity or not challenger_identity:
            raise ChampionChallengerValidationError("champion and challenger identity are required")
        if not instruments:
            raise ChampionChallengerValidationError("at least one instrument is required")
        normalized_horizon = int(horizon)
        if normalized_horizon <= 0:
            raise ChampionChallengerValidationError("horizon must be positive")
        if comparison_assumptions.horizon != normalized_horizon:
            raise ChampionChallengerValidationError("comparison_assumptions.horizon must match evaluation horizon")
        if comparison_assumptions.minimum_sample_size < 0:
            raise ChampionChallengerValidationError("minimum_sample_size must be non-negative")
        if champion_metrics.sample_count < comparison_assumptions.minimum_sample_size:
            raise ChampionChallengerValidationError("champion sample count is below minimum evidence threshold")
        if challenger_metrics.sample_count < comparison_assumptions.minimum_sample_size:
            raise ChampionChallengerValidationError("challenger sample count is below minimum evidence threshold")
        if champion_metrics.sample_count != challenger_metrics.sample_count:
            raise ChampionChallengerValidationError("champion and challenger sample counts must be equal for fair comparison")
        if (
            not comparison_assumptions.same_instruments
            or not comparison_assumptions.same_horizon
            or not comparison_assumptions.same_methodology
        ):
            raise ChampionChallengerValidationError("comparison assumptions require same instruments, horizon, and methodology")
        if not comparison_assumptions.point_in_time_semantics_preserved:
            raise ChampionChallengerValidationError("point-in-time semantics must be preserved")

        if recommendation is None:
            recommendation = self._derive_recommendation(champion_metrics, challenger_metrics)
        normalized_recommendation = (
            recommendation if isinstance(recommendation, EvaluationRecommendation) else EvaluationRecommendation(recommendation)
        )

        evaluation_id = self._build_evaluation_id(
            champion_identity=champion_identity,
            challenger_identity=challenger_identity,
            evaluation_period=evaluation_period,
            instruments=instruments,
            horizon=normalized_horizon,
            champion_metrics=champion_metrics,
            challenger_metrics=challenger_metrics,
            comparison_assumptions=comparison_assumptions,
            recommendation=normalized_recommendation,
            logical_as_of=logical_as_of,
        )
        evaluation = ChampionChallengerEvaluation(
            evaluation_id=evaluation_id,
            evaluation_as_of=logical_as_of,
            champion_identity=champion_identity,
            challenger_identity=challenger_identity,
            evaluation_period=evaluation_period,
            instruments=tuple(instruments),
            horizon=normalized_horizon,
            sample_count=champion_metrics.sample_count,
            champion_metrics=champion_metrics,
            challenger_metrics=challenger_metrics,
            methodology=comparison_assumptions.methodology,
            comparison_assumptions=comparison_assumptions,
            recommendation=normalized_recommendation,
            producer_version=producer_version or self.producer_version,
        )
        persisted = self.store.put(evaluation.envelope())
        with self._lock:
            self._index[evaluation_id] = persisted.artifact_id
            self._secondary_indexes["champion_model_id"].setdefault(self._model_id(champion_identity), []).append(persisted.artifact_id)
            self._secondary_indexes["challenger_model_id"].setdefault(self._model_id(challenger_identity), []).append(persisted.artifact_id)
            self._secondary_indexes["evaluation_period"].setdefault(evaluation_period, []).append(persisted.artifact_id)
            self._secondary_indexes["instruments"].setdefault(self._sorted_instruments(instruments), []).append(persisted.artifact_id)
            self._secondary_indexes["horizon"].setdefault(str(normalized_horizon), []).append(persisted.artifact_id)
            self._secondary_indexes["recommendation"].setdefault(normalized_recommendation.value, []).append(persisted.artifact_id)
            self._secondary_indexes["producer_version"].setdefault(persisted.producer_version, []).append(persisted.artifact_id)
        return evaluation, persisted

    def get_evaluation(self, evaluation_id: str) -> ChampionChallengerEvaluation:
        envelope = self._load_envelope(evaluation_id)
        return ChampionChallengerEvaluation.model_validate(envelope.payload)

    def get_evaluation_envelope(self, evaluation_id: str) -> ArtifactEnvelope:
        return self._load_envelope(evaluation_id)

    def query(self, **filters: Any) -> list[ChampionChallengerEvaluation]:
        unknown = sorted(set(filters) - set(INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise ChampionChallengerQueryError(f"unsupported champion/challenger filters: {unknown}")
        with self._lock:
            candidates = self._candidate_ids(filters)
        evaluations: list[ChampionChallengerEvaluation] = []
        seen: set[str] = set()
        for artifact_id in candidates:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            envelope = self.store.get(artifact_id)
            evaluation = ChampionChallengerEvaluation.model_validate(envelope.payload)
            if self._evaluation_matches_filters(evaluation, filters):
                evaluations.append(evaluation)
        return evaluations

    def compare_metrics(self, champion_metrics: EvaluationMetrics, challenger_metrics: EvaluationMetrics) -> dict[str, Any]:
        return {
            "champion_alignment_rate": champion_metrics.alignment_rate,
            "challenger_alignment_rate": challenger_metrics.alignment_rate,
            "champion_average_signed_return": champion_metrics.average_signed_return,
            "challenger_average_signed_return": challenger_metrics.average_signed_return,
            "champion_confidence_calibration": champion_metrics.confidence_calibration,
            "challenger_confidence_calibration": challenger_metrics.confidence_calibration,
            "sample_count_preserved": champion_metrics.sample_count == challenger_metrics.sample_count,
        }

    def _rebuild_indexes(self) -> None:
        self._index = OrderedDict()
        for field in INDEXED_FIELDS:
            self._secondary_indexes[field] = OrderedDict()
        try:
            evaluation_ids = self.store.list_ids(filters={"artifact_type": ArtifactType.CHAMPION_CHALLENGER_EVALUATION})
        except ChampionChallengerError:
            return
        except Exception as error:
            raise ChampionChallengerCorruptionError("failed to rebuild champion/challenger indexes") from error
        for artifact_id in evaluation_ids:
            try:
                envelope = self.store.get(artifact_id)
                evaluation = ChampionChallengerEvaluation.model_validate(envelope.payload)
            except Exception as error:
                raise ChampionChallengerCorruptionError("corrupted champion/challenger artifact during index rebuild") from error
            self._index[evaluation.evaluation_id] = envelope.artifact_id
            self._secondary_indexes["champion_model_id"].setdefault(self._model_id(evaluation.champion_identity), []).append(
                envelope.artifact_id
            )
            self._secondary_indexes["challenger_model_id"].setdefault(self._model_id(evaluation.challenger_identity), []).append(
                envelope.artifact_id
            )
            self._secondary_indexes["evaluation_period"].setdefault(evaluation.evaluation_period, []).append(envelope.artifact_id)
            self._secondary_indexes["instruments"].setdefault(self._sorted_instruments(evaluation.instruments), []).append(
                envelope.artifact_id
            )
            self._secondary_indexes["horizon"].setdefault(str(evaluation.horizon), []).append(envelope.artifact_id)
            self._secondary_indexes["recommendation"].setdefault(evaluation.recommendation.value, []).append(envelope.artifact_id)
            self._secondary_indexes["producer_version"].setdefault(envelope.producer_version, []).append(envelope.artifact_id)

    def _derive_recommendation(
        self, champion_metrics: EvaluationMetrics, challenger_metrics: EvaluationMetrics
    ) -> EvaluationRecommendation:
        champion_score = self._metric_score(champion_metrics)
        challenger_score = self._metric_score(challenger_metrics)
        if champion_score == challenger_score:
            return EvaluationRecommendation.INSUFFICIENT_EVIDENCE
        if challenger_score > champion_score:
            return EvaluationRecommendation.PROMOTE_CHALLENGER_FOR_RESEARCH
        return EvaluationRecommendation.KEEP_CHAMPION

    def _metric_score(self, metrics: EvaluationMetrics) -> float:
        score = 0.0
        if metrics.alignment_rate is not None:
            score += metrics.alignment_rate
        if metrics.average_signed_return is not None:
            score += float(metrics.average_signed_return)
        if metrics.confidence_calibration is not None:
            score -= float(metrics.confidence_calibration)
        return score

    def _evaluation_period_as_of(self, evaluation_period: str) -> datetime:
        try:
            year, month = evaluation_period.split("-", 1)
            return datetime(int(year), int(month), 1, tzinfo=UTC)
        except Exception as error:
            raise ChampionChallengerValidationError("evaluation_period must be in YYYY-MM format") from error

    def _build_evaluation_id(self, **material: Any) -> str:
        canonical_material = dict(material)
        canonical_material.pop("producer_version", None)
        canonical_material.pop("logical_as_of", None)
        canonical_material.pop("evaluation_as_of", None)
        return sha256_fingerprint({"schema_version": CHAMPION_CHALLENGER_SCHEMA_VERSION, **canonical_material})

    def _model_id(self, identity: dict[str, Any]) -> str:
        model_identity = ModelIdentity.model_validate(identity)
        return sha256_fingerprint(
            {
                "model_id": model_identity.model_id,
                "model_version": model_identity.model_version,
                "provider": model_identity.provider,
            }
        )

    def _sorted_instruments(self, instruments: list[str] | tuple[str, ...]) -> str:
        return ",".join(sorted(instruments))

    def _load_envelope(self, evaluation_id: str) -> ArtifactEnvelope:
        artifact_id = self._index.get(evaluation_id)
        if artifact_id is None:
            raise ChampionChallengerNotFoundError(f"unknown champion/challenger evaluation: {evaluation_id}")
        try:
            return self.store.get(artifact_id)
        except ChampionChallengerError:
            raise
        except ArtifactCorruptedError:
            raise
        except Exception as error:
            raise ChampionChallengerCorruptionError("failed to load champion/challenger evaluation") from error

    def _candidate_ids(self, filters: dict[str, Any]) -> list[str]:
        candidate_sets: list[set[str]] = []
        limit = filters.pop("limit", None)
        for field, value in filters.items():
            if field not in INDEXED_FIELDS:
                continue
            if field in {"champion_model_id", "challenger_model_id"}:
                normalized = self._model_id(value if isinstance(value, dict) else {"model_id": str(value)})
                values = self._secondary_indexes[field].get(normalized, [])
            elif field == "instruments":
                normalized = self._sorted_instruments(value if isinstance(value, (list, tuple)) else [value])
                values = self._secondary_indexes[field].get(normalized, [])
            else:
                values = self._secondary_indexes[field].get(str(value), [])
            candidate_sets.append(set(values))
        if not candidate_sets:
            return list(self._index.values())
        intersection = candidate_sets[0]
        for candidate_set in candidate_sets[1:]:
            intersection &= candidate_set
        ordered = [artifact_id for artifact_id in self._index.values() if artifact_id in intersection]
        if limit is not None:
            ordered = ordered[: int(limit)]
        return ordered

    def _evaluation_matches_filters(self, evaluation: ChampionChallengerEvaluation, filters: dict[str, Any]) -> bool:
        for field, value in filters.items():
            if field == "champion_model_id":
                identity = evaluation.champion_identity
                if self._model_id(identity) != self._model_id(value if isinstance(value, dict) else {"model_id": str(value)}):
                    return False
            elif field == "challenger_model_id":
                identity = evaluation.challenger_identity
                if self._model_id(identity) != self._model_id(value if isinstance(value, dict) else {"model_id": str(value)}):
                    return False
            elif field == "instruments":
                expected = value if isinstance(value, (list, tuple)) else [value]
                if sorted(evaluation.instruments) != sorted(expected):
                    return False
            elif field == "horizon":
                if evaluation.horizon != int(value):
                    return False
            elif hasattr(evaluation, field) and str(getattr(evaluation, field)) != str(value):
                return False
        return True


__all__ = ["ChampionChallengerService"]
