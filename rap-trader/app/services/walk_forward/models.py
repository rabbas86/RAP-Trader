"""Immutable walk-forward evaluation contracts for Phase 16H."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference
from app.domain.models.market_data import UtcDatetime, _require_aware_utc
from app.services.performance_evaluation.models import (
    BenchmarkComparison,
    CorporateActionAggregate,
    HistoricalPerformanceEvaluation,
    MetricValue,
    PerformanceMetrics,
    RiskMetrics,
    TransactionCostAggregate,
)

PHASE16H_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def _normalize_walk_forward_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    assert isinstance(value, datetime)
    return _require_aware_utc(value)


class WalkForwardMode(StrEnum):
    ANCHORED = "ANCHORED"
    ROLLING = "ROLLING"


class IncompleteFinalFoldPolicy(StrEnum):
    DROP_INCOMPLETE = "DROP_INCOMPLETE"
    EVALUATE_IF_MINIMUM_SAMPLE_MET = "EVALUATE_IF_MINIMUM_SAMPLE_MET"


class FoldStatus(StrEnum):
    VALID = "valid"
    INSUFFICIENT_DATA = "insufficient_data"
    INCOMPLETE = "incomplete"
    EMPTY = "empty"


class _WalkForwardFrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = PHASE16H_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _WalkForwardFrozenModel:
        raise TypeError("Phase 16H contracts are immutable and do not support model_copy")

    @classmethod
    def _build_identity_payload(cls, material: dict[str, object], exclude: set[str]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in material.items():
            if key in exclude:
                continue
            if isinstance(value, datetime):
                payload[key] = _require_aware_utc(value).isoformat()
                continue
            if isinstance(value, UUID):
                payload[key] = str(value)
                continue
            if isinstance(value, tuple):
                payload[key] = list(value)
                continue
            payload[key] = value
        return payload


class WalkForwardEvaluationMethodology(_WalkForwardFrozenModel):
    """Immutable methodology for chronological walk-forward evaluation."""

    methodology_id: str = Field(min_length=64, max_length=64)
    methodology_name: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    train_window: str = Field(min_length=1)
    test_window: str = Field(min_length=1)
    step: str = Field(min_length=1)
    embargo: str = Field(min_length=1)
    minimum_train_observations: int = Field(default=0, ge=0)
    minimum_test_observations: int = Field(default=2, ge=0)
    incomplete_final_fold_policy: str = Field(min_length=1)
    performance_evaluation_methodology_id: str = Field(min_length=64, max_length=64)
    benchmark_policy: str = Field(min_length=1)
    fold_aggregation_policy: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in {item.value for item in WalkForwardMode}:
            raise ValueError("mode must be ANCHORED or ROLLING")
        return value

    @field_validator("incomplete_final_fold_policy")
    @classmethod
    def validate_incomplete_final_fold_policy(cls, value: str) -> str:
        if value not in {item.value for item in IncompleteFinalFoldPolicy}:
            raise ValueError("incomplete_final_fold_policy must be DROP_INCOMPLETE or EVALUATE_IF_MINIMUM_SAMPLE_MET")
        return value

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"methodology_id"})

    def _canonical_methodology_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create(cls, **values: object) -> WalkForwardEvaluationMethodology:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16H_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("methodology_id", None)
        provisional = cls.model_validate({"methodology_id": "0" * 64, **material})
        canonical_id = provisional._canonical_methodology_id()
        payload = provisional.model_dump()
        payload["methodology_id"] = canonical_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        if not provenance_references:
            raise ValueError("provenance_references must not be empty")
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.WALK_FORWARD_EVALUATION_METHODOLOGY,
            logical_as_of=datetime(1970, 1, 1, tzinfo=UTC),
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class WalkForwardFold(_WalkForwardFrozenModel):
    """Immutable representation of one walk-forward fold."""

    fold_id: str = Field(min_length=64, max_length=64)
    fold_index: int = Field(ge=0)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    methodology_id: str = Field(min_length=64, max_length=64)
    train_start: UtcDatetime
    train_end: UtcDatetime
    test_start: UtcDatetime
    test_end: UtcDatetime
    embargo_start: UtcDatetime | None = Field(default=None)
    embargo_end: UtcDatetime | None = Field(default=None)
    training_observation_count: int = Field(default=0, ge=0)
    test_observation_count: int = Field(default=0, ge=0)
    performance_evaluation_id: str | None = Field(default=None, min_length=64, max_length=64)
    performance_evaluation: HistoricalPerformanceEvaluation | None = Field(default=None)
    status: Literal["valid", "insufficient_data", "incomplete", "empty"] = Field(min_length=1)
    warnings: tuple[str, ...] = ()
    retrained: Literal[False] = False
    suitable_for_live_trading: Literal[False] = False
    producer_version: str = Field(min_length=1)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {item.value for item in FoldStatus}:
            raise ValueError("status must be a valid FoldStatus")
        return value

    @field_validator("train_start", "train_end", "test_start", "test_end", "embargo_start", "embargo_end", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        assert isinstance(value, datetime)
        return _require_aware_utc(value)

    @field_validator("warnings", mode="before")
    @classmethod
    def coerce_warnings(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("warnings must be a list or tuple")

    @model_validator(mode="after")
    def validate_fold_ordering(self) -> WalkForwardFold:
        if self.train_start > self.train_end:
            raise ValueError("train_start must be before train_end")
        if self.test_start > self.test_end:
            raise ValueError("test_start must be before test_end")
        if self.train_end > self.test_start:
            raise ValueError("train_end must be before test_start")
        if self.embargo_start is not None and self.embargo_end is not None and self.embargo_start > self.embargo_end:
            raise ValueError("embargo_start must be before embargo_end")
        if self.embargo_start is not None and (
            self.embargo_end is None or not (self.train_end <= self.embargo_start <= self.embargo_end <= self.test_start)
        ):
            raise ValueError("embargo must lie between train_end and test_start")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "replay_specification_id": self.replay_specification_id,
            "methodology_id": self.methodology_id,
            "train_start": self.train_start.isoformat(),
            "train_end": self.train_end.isoformat(),
            "test_start": self.test_start.isoformat(),
            "test_end": self.test_end.isoformat(),
            "embargo_start": self.embargo_start.isoformat() if self.embargo_start is not None else None,
            "embargo_end": self.embargo_end.isoformat() if self.embargo_end is not None else None,
            "training_observation_count": self.training_observation_count,
            "test_observation_count": self.test_observation_count,
            "status": self.status,
            "warnings": list(self.warnings),
            "retrained": self.retrained,
            "producer_version": self.producer_version,
        }

    def _canonical_fold_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> WalkForwardFold:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16H_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.setdefault("retrained", False)
        material.pop("fold_id", None)
        provisional = cls.model_validate({"fold_id": "0" * 64, **material})
        fold_id = provisional._canonical_fold_id()
        payload = BaseModel.model_dump(provisional, mode="json")
        payload["fold_id"] = fold_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.WALK_FORWARD_FOLD,
            logical_as_of=self.test_end,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class FoldStabilityMetrics(_WalkForwardFrozenModel):
    """Conservative fold-stability evidence."""

    fold_count: int = Field(ge=0)
    valid_fold_count: int = Field(ge=0)
    insufficient_fold_count: int = Field(ge=0)
    positive_fold_count: int = Field(ge=0)
    negative_fold_count: int = Field(ge=0)
    positive_fold_ratio: MetricValue
    best_fold_total_return: MetricValue
    worst_fold_total_return: MetricValue
    median_fold_total_return: MetricValue
    median_fold_sharpe: MetricValue | None = Field(default=None)
    worst_fold_drawdown: MetricValue | None = Field(default=None)
    producer_version: str = Field(min_length=1)


class WalkForwardEvaluation(_WalkForwardFrozenModel):
    """Immutable aggregate walk-forward evaluation."""

    walk_forward_evaluation_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    backtest_run_manifest_id: str | None = Field(default=None, min_length=64, max_length=64)
    methodology_id: str = Field(min_length=64, max_length=64)
    performance_methodology_id: str = Field(min_length=64, max_length=64)
    fold_ids: tuple[str, ...] = ()
    fold_count: int = Field(ge=0)
    valid_fold_count: int = Field(ge=0)
    insufficient_fold_count: int = Field(ge=0)
    oos_start: UtcDatetime | None = Field(default=None)
    oos_end: UtcDatetime | None = Field(default=None)
    oos_performance_metrics: PerformanceMetrics
    oos_risk_metrics: RiskMetrics
    fold_stability_metrics: FoldStabilityMetrics
    benchmark_comparison: BenchmarkComparison | None = None
    transaction_cost_aggregate: TransactionCostAggregate
    corporate_action_aggregate: CorporateActionAggregate
    warnings: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    logical_as_of: UtcDatetime
    producer_version: str = Field(min_length=1)

    @field_validator("fold_ids", "warnings", "input_artifact_ids", mode="before")
    @classmethod
    def coerce_string_sequences(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("sequence fields must be a list, tuple, or string")

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"walk_forward_evaluation_id", "logical_as_of", "producer_version"})

    def _canonical_evaluation_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> WalkForwardEvaluation:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16H_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("walk_forward_evaluation_id", None)
        provisional = cls.model_validate({"walk_forward_evaluation_id": "0" * 64, **material})
        evaluation_id = provisional._canonical_evaluation_id()
        payload = provisional.model_dump()
        payload["walk_forward_evaluation_id"] = evaluation_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.WALK_FORWARD_EVALUATION,
            logical_as_of=self.logical_as_of,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class HistoricalBacktestReport(_WalkForwardFrozenModel):
    """Immutable final Phase 16H backtest report."""

    backtest_report_id: str = Field(min_length=64, max_length=64)
    deterministic_report_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    backtest_run_manifest_id: str | None = Field(default=None, min_length=64, max_length=64)
    walk_forward_evaluation_id: str = Field(min_length=64, max_length=64)
    performance_evaluation_id: str | None = Field(default=None, min_length=64, max_length=64)
    methodology_ids: tuple[str, ...] = ()
    producer_version: str = Field(min_length=1)
    status: Literal["COMPLETE", "COMPLETE_WITH_WARNINGS", "INSUFFICIENT_DATA", "INVALID"] = Field(min_length=1)
    scope: dict[str, Any] = Field(default_factory=dict)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    performance: dict[str, Any] = Field(default_factory=dict)
    benchmark: dict[str, Any] = Field(default_factory=dict)
    costs: dict[str, Any] = Field(default_factory=dict)
    corporate_actions: dict[str, Any] = Field(default_factory=dict)
    walk_forward: dict[str, Any] = Field(default_factory=dict)
    reproducibility: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    logical_as_of: UtcDatetime

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {"COMPLETE", "COMPLETE_WITH_WARNINGS", "INSUFFICIENT_DATA", "INVALID"}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value

    @field_validator("methodology_ids", "warnings", "limitations", mode="before")
    @classmethod
    def coerce_string_sequences(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("sequence fields must be a list, tuple, or string")

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"backtest_report_id", "logical_as_of", "producer_version"})

    def _canonical_report_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> HistoricalBacktestReport:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16H_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("backtest_report_id", None)
        material.pop("deterministic_report_id", None)
        provisional = cls.model_validate({"backtest_report_id": "0" * 64, "deterministic_report_id": "0" * 64, **material})
        report_id = provisional._canonical_report_id()
        payload = provisional.model_dump()
        payload["backtest_report_id"] = report_id
        payload["deterministic_report_id"] = report_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_BACKTEST_REPORT,
            logical_as_of=self.logical_as_of,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


__all__ = [
    "PHASE16H_SCHEMA_VERSION",
    "FoldStatus",
    "HistoricalBacktestReport",
    "IncompleteFinalFoldPolicy",
    "WalkForwardEvaluation",
    "WalkForwardEvaluationMethodology",
    "WalkForwardFold",
    "WalkForwardMode",
]
