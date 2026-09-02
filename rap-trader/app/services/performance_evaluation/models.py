"""Immutable performance, risk, and benchmark evaluation contracts for Phase 16G."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference
from app.domain.models.market_data import Symbol, UtcDatetime, _require_aware_utc

PHASE16G_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def _normalize_performance_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _require_aware_utc(value)
    if isinstance(value, str):
        return _normalize_performance_timestamp(datetime.fromisoformat(value))
    raise TypeError("performance timestamp must be a datetime or ISO-8601 string")


def _coerce_string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


def _coerce_symbol_sequence(value: object) -> tuple[Symbol, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(Symbol(str(item)) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


class _PerformanceFrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = PHASE16G_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _PerformanceFrozenModel:
        raise TypeError("Phase 16G contracts are immutable and do not support model_copy")


class MetricValue(_PerformanceFrozenModel):
    """Explicit metric value that can be unavailable."""

    value: float | None = None
    status: Literal["available", "unavailable", "insufficient_sample", "zero_denominator"] = "unavailable"
    reason: str | None = Field(default=None, min_length=1)
    producer_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_status(self) -> MetricValue:
        if self.status == "available" and self.value is None:
            raise ValueError("available metrics must carry a numeric value")
        if self.status != "available" and self.value is not None:
            raise ValueError("unavailable metrics must not carry a numeric value")
        return self


class PerformanceEvaluationMethodology(_PerformanceFrozenModel):
    """Immutable methodology for Phase 16G performance evaluation."""

    methodology_id: str = Field(min_length=64, max_length=64)
    methodology_name: str = Field(min_length=1)
    periods_per_year: float = Field(gt=0, allow_inf_nan=False)
    risk_free_rate_annual: float = Field(default=0.0, allow_inf_nan=False)
    minimum_acceptable_return_annual: float = Field(default=0.0, allow_inf_nan=False)
    volatility_convention: Literal["sample", "population"] = "sample"
    sharpe_convention: str = Field(min_length=1, default="arithmetic_excess_annualized")
    sortino_mar_convention: str = Field(min_length=1, default="configurable_annual_mar")
    benchmark_alignment_policy: str = Field(min_length=1, default="inner_common_timestamps")
    return_methodology: str = Field(min_length=1, default="time_weighted")
    drawdown_methodology: str = Field(min_length=1, default="running_peak_from_equity_curve")
    minimum_sample_count: int = Field(default=2, ge=0)
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"methodology_id"})

    def _canonical_methodology_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @property
    def periodic_risk_free_rate(self) -> float:
        return self.risk_free_rate_annual / self.periods_per_year

    @property
    def periodic_minimum_acceptable_return(self) -> float:
        return self.minimum_acceptable_return_annual / self.periods_per_year

    @classmethod
    def create(cls, **values: object) -> PerformanceEvaluationMethodology:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16G_SCHEMA_VERSION)
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
            artifact_type=ArtifactType.PERFORMANCE_EVALUATION_METHODOLOGY,
            logical_as_of=datetime(1970, 1, 1, tzinfo=UTC),
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PortfolioReturnObservation(_PerformanceFrozenModel):
    """One contiguous return interval derived from valued portfolio snapshots."""

    observation_id: str = Field(min_length=64, max_length=64)
    start_snapshot_id: str = Field(min_length=64, max_length=64)
    end_snapshot_id: str = Field(min_length=64, max_length=64)
    start_timestamp: UtcDatetime
    end_timestamp: UtcDatetime
    start_equity: float = Field(ge=0, allow_inf_nan=False)
    end_equity: float = Field(ge=0, allow_inf_nan=False)
    period_return: float
    status: Literal["available", "unavailable"] = "available"
    exclusion_reason: str | None = Field(default=None, min_length=1)
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"observation_id"})

    def _canonical_observation_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @classmethod
    def create(
        cls,
        *,
        start_snapshot_id: str,
        end_snapshot_id: str,
        start_timestamp: datetime,
        end_timestamp: datetime,
        start_equity: float,
        end_equity: float,
        period_return: float,
        status: str = "available",
        exclusion_reason: str | None = None,
        producer_version: str = "phase16g-1.0",
    ) -> PortfolioReturnObservation:
        provisional = cls(
            observation_id="0" * 64,
            start_snapshot_id=start_snapshot_id,
            end_snapshot_id=end_snapshot_id,
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
            start_equity=start_equity,
            end_equity=end_equity,
            period_return=period_return,
            status="available",
            exclusion_reason=exclusion_reason,
            producer_version=producer_version,
        )
        observation_id = provisional._canonical_observation_id()
        payload = provisional.model_dump()
        payload["observation_id"] = observation_id
        return cls.model_validate(payload)


class PortfolioReturnSeries(_PerformanceFrozenModel):
    """Chronological portfolio return series derived from valued snapshots."""

    series_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    methodology_id: str = Field(min_length=64, max_length=64)
    snapshots_considered: int = Field(ge=0)
    valued_snapshot_count: int = Field(ge=0)
    unvalued_snapshot_ids: tuple[str, ...] = ()
    observations: tuple[PortfolioReturnObservation, ...] = ()
    return_observation_count: int = Field(ge=0)
    valuation_coverage: float = Field(ge=0, le=1, allow_inf_nan=False)
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"series_id"})

    def _canonical_series_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("unvalued_snapshot_ids", mode="before")
    @classmethod
    def coerce_unvalued_snapshot_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("unvalued_snapshot_ids must be a list or tuple")

    @field_validator("observations", mode="before")
    @classmethod
    def coerce_observations(cls, value: object) -> tuple[PortfolioReturnObservation, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(PortfolioReturnObservation.model_validate(item) if isinstance(item, dict) else item for item in value)
        raise TypeError("observations must be a list or tuple")

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @classmethod
    def create(
        cls,
        *,
        replay_specification_id: str,
        replay_run_id: UUID,
        methodology_id: str,
        snapshots_considered: int,
        valued_snapshot_count: int,
        unvalued_snapshot_ids: Sequence[str],
        observations: Sequence[PortfolioReturnObservation],
        valuation_coverage: float,
        producer_version: str = "phase16g-1.0",
    ) -> PortfolioReturnSeries:
        provisional = cls(
            series_id="0" * 64,
            replay_specification_id=replay_specification_id,
            replay_run_id=replay_run_id,
            methodology_id=methodology_id,
            snapshots_considered=snapshots_considered,
            valued_snapshot_count=valued_snapshot_count,
            unvalued_snapshot_ids=tuple(unvalued_snapshot_ids),
            observations=tuple(observations),
            return_observation_count=len(observations),
            valuation_coverage=valuation_coverage,
            producer_version=producer_version,
        )
        series_id = provisional._canonical_series_id()
        payload = provisional.model_dump()
        payload["series_id"] = series_id
        return cls.model_validate(payload)


class DrawdownPeriod(_PerformanceFrozenModel):
    """Deterministic maximum drawdown record."""

    max_drawdown: float = Field(allow_inf_nan=False)
    max_drawdown_percent: float = Field(allow_inf_nan=False)
    peak_timestamp: UtcDatetime
    trough_timestamp: UtcDatetime
    recovery_timestamp: UtcDatetime | None = None
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    status: Literal["available", "unavailable"] = "available"
    reason: str | None = Field(default=None, min_length=1)
    producer_version: str = Field(min_length=1)


class PerformanceMetrics(_PerformanceFrozenModel):
    """Deterministic portfolio performance metrics."""

    starting_equity: MetricValue
    ending_equity: MetricValue
    total_return: MetricValue
    cagr: MetricValue
    cumulative_return_series: tuple[float, ...] = ()
    positive_period_ratio: MetricValue
    period_return_count: int = Field(ge=0)
    producer_version: str = Field(min_length=1)


class RiskMetrics(_PerformanceFrozenModel):
    """Deterministic portfolio risk metrics."""

    annualized_volatility: MetricValue
    downside_deviation: MetricValue
    maximum_drawdown: DrawdownPeriod
    sharpe_ratio: MetricValue
    sortino_ratio: MetricValue
    calmar_ratio: MetricValue
    best_period_return: MetricValue
    worst_period_return: MetricValue
    producer_version: str = Field(min_length=1)


class BenchmarkSpecification(_PerformanceFrozenModel):
    """Explicit immutable benchmark specification."""

    benchmark_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    price_methodology: str = Field(min_length=1)
    return_methodology: str = Field(min_length=1)
    base_currency: str = Field(min_length=3, max_length=3)
    timeframe: str = Field(min_length=1)
    source_version: str = Field(min_length=1)
    replay_specification_id: str | None = Field(default=None, min_length=64, max_length=64)
    replay_run_id: UUID | None = None
    producer_version: str = Field(min_length=1)

    @field_validator("base_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> Symbol:
        if isinstance(value, Symbol):
            return value
        if isinstance(value, str):
            return Symbol(value)
        raise TypeError("symbol must be a string or Symbol")

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"benchmark_id"})

    def _canonical_benchmark_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @classmethod
    def create(cls, **values: object) -> BenchmarkSpecification:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16G_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("benchmark_id", None)
        provisional = cls.model_validate({"benchmark_id": "0" * 64, **material})
        benchmark_id = provisional._canonical_benchmark_id()
        payload = provisional.model_dump()
        payload["benchmark_id"] = benchmark_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        if not provenance_references:
            raise ValueError("provenance_references must not be empty")
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_PERFORMANCE_EVALUATION,
            logical_as_of=datetime(1970, 1, 1, tzinfo=UTC),
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class BenchmarkReturnObservation(_PerformanceFrozenModel):
    """One benchmark return observation."""

    observation_id: str = Field(min_length=64, max_length=64)
    timestamp: UtcDatetime
    price: float = Field(gt=0, allow_inf_nan=False)
    benchmark_return: float | None = None
    status: Literal["available", "unavailable"] = "available"
    exclusion_reason: str | None = Field(default=None, min_length=1)
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"observation_id"})

    def _canonical_observation_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @classmethod
    def create(
        cls,
        *,
        timestamp: datetime,
        price: float,
        benchmark_return: float | None = None,
        status: str = "available",
        exclusion_reason: str | None = None,
        producer_version: str = "phase16g-1.0",
    ) -> BenchmarkReturnObservation:
        provisional = cls(
            observation_id="0" * 64,
            timestamp=timestamp,
            price=price,
            benchmark_return=benchmark_return,
            status="available",
            exclusion_reason=exclusion_reason,
            producer_version=producer_version,
        )
        observation_id = provisional._canonical_observation_id()
        payload = provisional.model_dump()
        payload["observation_id"] = observation_id
        return cls.model_validate(payload)


class BenchmarkReturnSeries(_PerformanceFrozenModel):
    """Benchmark return series aligned to portfolio timestamps."""

    series_id: str = Field(min_length=64, max_length=64)
    benchmark_specification_id: str = Field(min_length=64, max_length=64)
    observations: tuple[BenchmarkReturnObservation, ...] = ()
    observation_count: int = Field(ge=0)
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"series_id"})

    def _canonical_series_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @field_validator("observations", mode="before")
    @classmethod
    def coerce_observations(cls, value: object) -> tuple[BenchmarkReturnObservation, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(BenchmarkReturnObservation.model_validate(item) if isinstance(item, dict) else item for item in value)
        raise TypeError("observations must be a list or tuple")

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @classmethod
    def create(
        cls,
        *,
        benchmark_specification_id: str,
        observations: Sequence[BenchmarkReturnObservation],
        producer_version: str = "phase16g-1.0",
    ) -> BenchmarkReturnSeries:
        provisional = cls(
            series_id="0" * 64,
            benchmark_specification_id=benchmark_specification_id,
            observations=tuple(observations),
            observation_count=len(observations),
            producer_version=producer_version,
        )
        series_id = provisional._canonical_series_id()
        payload = provisional.model_dump()
        payload["series_id"] = series_id
        return cls.model_validate(payload)


class BenchmarkComparison(_PerformanceFrozenModel):
    """Deterministic benchmark comparison."""

    benchmark_specification_id: str = Field(min_length=64, max_length=64)
    benchmark_total_return: MetricValue
    portfolio_excess_total_return: MetricValue
    tracking_error: MetricValue
    information_ratio: MetricValue
    beta: MetricValue
    alpha: MetricValue
    correlation: MetricValue
    aligned_sample_count: int = Field(ge=0)
    portfolio_sample_count: int = Field(ge=0)
    benchmark_sample_count: int = Field(ge=0)
    excluded_intervals: tuple[str, ...] = ()
    benchmark_price_return_semantics: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)


class TransactionCostAggregate(_PerformanceFrozenModel):
    """Aggregated Phase 16F transaction costs."""

    assessment_count: int = Field(ge=0)
    total_commission: float = Field(ge=0, allow_inf_nan=False)
    total_spread_cost: float = Field(ge=0, allow_inf_nan=False)
    total_slippage_cost: float = Field(ge=0, allow_inf_nan=False)
    total_transaction_cost: float = Field(ge=0, allow_inf_nan=False)
    cost_to_starting_capital_ratio: MetricValue
    producer_version: str = Field(min_length=1)


class CorporateActionAggregate(_PerformanceFrozenModel):
    """Aggregated Phase 16F corporate actions."""

    dividend_count: int = Field(ge=0)
    total_dividend_cash: float = Field(ge=0, allow_inf_nan=False)
    split_count: int = Field(ge=0)
    producer_version: str = Field(min_length=1)


class HistoricalPerformanceEvaluation(_PerformanceFrozenModel):
    """Immutable downstream historical performance evaluation artifact."""

    evaluation_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    methodology_id: str = Field(min_length=64, max_length=64)
    first_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    last_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    snapshot_count: int = Field(ge=0)
    valued_snapshot_count: int = Field(ge=0)
    return_observation_count: int = Field(ge=0)
    performance_metrics: PerformanceMetrics
    risk_metrics: RiskMetrics
    transaction_cost_aggregate: TransactionCostAggregate
    corporate_action_aggregate: CorporateActionAggregate
    benchmark_comparison: BenchmarkComparison | None = None
    input_artifact_ids: tuple[str, ...] = ()
    logical_as_of: UtcDatetime
    producer_version: str = Field(min_length=1)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"evaluation_id", "logical_as_of", "producer_version"},
        )

    def _canonical_evaluation_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump())

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("input_artifact_ids", mode="before")
    @classmethod
    def coerce_input_artifact_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("input_artifact_ids must be a list or tuple")

    @classmethod
    def create(cls, **values: object) -> HistoricalPerformanceEvaluation:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16G_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("evaluation_id", None)
        provisional = cls.model_validate({"evaluation_id": "0" * 64, **material})
        evaluation_id = provisional._canonical_evaluation_id()
        payload = provisional.model_dump()
        payload["evaluation_id"] = evaluation_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        if not provenance_references:
            raise ValueError("provenance_references must not be empty")
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_PERFORMANCE_EVALUATION,
            logical_as_of=self.logical_as_of,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )
