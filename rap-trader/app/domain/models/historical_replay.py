"""Phase 16A: Historical replay contracts.

This module defines the immutable canonical contracts for historical replay
and backtest runs. It does NOT execute the replay yet.

Contracts
---------
* :class:`HistoricalReplaySpecification` — immutable WHAT of a replay run.
* :class:`HistoricalReplayRun` — deterministic lifecycle identity for one
  replay execution attempt.
* :class:`ReplayRunEvent` — immutable append-only causal event for a replay
  run, enabling future event-sourcing without mutating prior artifacts.
* :class:`BacktestRunManifest` — immutable persisted manifest identifying a
  particular historical replay execution and referencing upstream artifacts
  by immutable IDs.

All contracts permanently enforce research-only, paper-only operation and
``suitable_for_live_trading=False``. No broker, execution, order, risk, or
portfolio components are imported or invoked from this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.domain.canonical import canonical_json, sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.market_data import Symbol, UtcDatetime, _require_aware_utc

REPLAY_SCHEMA_VERSION: Literal["1.0"] = "1.0"
REPLAY_GENESIS_EVENT_HASH = "0" * 64
REPLAY_ID_NAMESPACE = UUID("7d9c24a1-4b2f-5e6d-8a7b-9c0d1e2f3a4b")
REPLAY_RUN_ID_NAMESPACE = UUID("b1a2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")

VALID_TIMEFRAMES = frozenset({"1m", "5m", "15m", "1h", "1d", "1w"})
VALID_POINT_IN_TIME_POLICIES = frozenset(
    {
        "event_time_only",
        "available_at_aware",
    }
)


class ReplayRunStatus(StrEnum):
    """Explicit deterministic lifecycle states for a historical replay run."""

    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_REPLAY_TRANSITIONS: dict[ReplayRunStatus, frozenset[ReplayRunStatus]] = {
    ReplayRunStatus.CREATED: frozenset({ReplayRunStatus.RUNNING, ReplayRunStatus.CANCELLED}),
    ReplayRunStatus.RUNNING: frozenset({ReplayRunStatus.COMPLETED, ReplayRunStatus.FAILED, ReplayRunStatus.CANCELLED}),
    ReplayRunStatus.COMPLETED: frozenset(),
    ReplayRunStatus.FAILED: frozenset(),
    ReplayRunStatus.CANCELLED: frozenset(),
}


class _ReplayFrozenModel(BaseModel):
    """Base frozen contract for Phase 16A models."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _ReplayFrozenModel:
        """Copy through validation so permanent contract invariants cannot be bypassed."""
        values = self.model_dump(round_trip=True)
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


def _normalize_replay_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return _require_aware_utc(cast(datetime, value))


def _coerce_string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


def _coerce_symbol_sequence(value: object) -> tuple[Symbol, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(Symbol(str(item)) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


class HistoricalReplaySpecification(_ReplayFrozenModel):
    """Immutable canonical specification for one historical replay/backtest.

    The specification defines WHAT a replay run will test. A later execution
    engine may read this specification and produce a matching
    :class:`HistoricalReplayRun` artifact. The same immutable specification
    always yields the same ``specification_id`` and never mutates historical
    decision state.
    """

    specification_id: str
    run_id: UUID
    logical_as_of: UtcDatetime
    recorded_at: UtcDatetime
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    methodology_version: str = Field(min_length=1)
    start_time: UtcDatetime
    end_time: UtcDatetime
    instruments: tuple[Symbol, ...] = Field(min_length=1)
    timeframes: tuple[str, ...] = Field(min_length=1)
    decision_cadence: str = Field(min_length=1)
    data_boundary_description: str = Field(min_length=1)
    point_in_time_policy: str = Field(min_length=1)
    strategy_identities: tuple[str, ...] = ()
    model_identities: tuple[str, ...] = ()
    config_fingerprints: tuple[str, ...] = ()
    execution_methodology: str = Field(min_length=1)
    cost_methodology: str = Field(min_length=1)
    initial_capital: float = Field(gt=0)
    base_currency: str = Field(min_length=3, max_length=3)
    benchmark_identities: tuple[Symbol, ...] = ()
    deterministic_seed: int | None = None
    notes: str | None = Field(default=None, min_length=1)

    @field_validator("logical_as_of", "recorded_at", "start_time", "end_time", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_replay_timestamp(value)

    @field_validator("point_in_time_policy", mode="before")
    @classmethod
    def coerce_point_in_time_policy(cls, value: object) -> str:
        if isinstance(value, str):
            if value not in VALID_POINT_IN_TIME_POLICIES:
                raise ValueError("point_in_time_policy must be one of the allowed policies")
            return value
        raise TypeError("point_in_time_policy must be a string")

    @field_validator("instruments", "benchmark_identities", mode="before")
    @classmethod
    def coerce_symbol_sequence(cls, value: object) -> tuple[Symbol, ...]:
        return _coerce_symbol_sequence(value)

    @field_validator("timeframes", mode="before")
    @classmethod
    def coerce_timeframes(cls, value: object) -> tuple[str, ...]:
        values = _coerce_string_sequence(value)
        for item in values:
            if item not in VALID_TIMEFRAMES:
                raise ValueError(f"unsupported timeframe: {item}")
        return values

    @field_validator("strategy_identities", "model_identities", "config_fingerprints", mode="before")
    @classmethod
    def coerce_string_sequences(cls, value: object) -> tuple[str, ...]:
        return _coerce_string_sequence(value)

    @model_validator(mode="after")
    def validate_specification(self) -> HistoricalReplaySpecification:
        if self.logical_as_of > self.recorded_at:
            raise ValueError("logical_as_of cannot be after recorded_at")
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        if self.point_in_time_policy == "available_at_aware" and not self.data_boundary_description:
            raise ValueError("available_at_aware policy requires data_boundary_description")
        if not self.strategy_identities:
            raise ValueError("strategy_identities must not be empty")
        if not self.model_identities:
            raise ValueError("model_identities must not be empty")
        if not self.config_fingerprints:
            raise ValueError("config_fingerprints must not be empty")
        if self.specification_id != self._canonical_specification_id():
            raise ValueError("specification_id does not match canonical specification identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"specification_id", "run_id", "notes"})

    def _canonical_specification_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @property
    def replay_id(self) -> str:
        return self.specification_id

    @classmethod
    def create(
        cls,
        *,
        start_time: datetime,
        end_time: datetime,
        instruments: list[str] | tuple[str, ...],
        timeframes: list[str] | tuple[str, ...],
        decision_cadence: str,
        data_boundary_description: str,
        strategy_identities: list[str] | tuple[str, ...] = (),
        model_identities: list[str] | tuple[str, ...] = (),
        config_fingerprints: list[str] | tuple[str, ...] = (),
        execution_methodology: str,
        cost_methodology: str,
        initial_capital: float,
        base_currency: str,
        logical_as_of: datetime,
        recorded_at: datetime,
        producer: str,
        producer_version: str,
        methodology_version: str,
        benchmark_identities: list[str] | tuple[str, ...] | None = None,
        deterministic_seed: int | None = None,
        point_in_time_policy: str = "event_time_only",
        notes: str | None = None,
    ) -> HistoricalReplaySpecification:
        normalized_benchmarks = _coerce_symbol_sequence(benchmark_identities or ())
        provisional = cls.model_construct(
            specification_id="0" * 64,
            run_id=UUID(int=0),
            logical_as_of=_normalize_replay_timestamp(logical_as_of),
            recorded_at=_normalize_replay_timestamp(recorded_at),
            start_time=_normalize_replay_timestamp(start_time),
            end_time=_normalize_replay_timestamp(end_time),
            instruments=_coerce_symbol_sequence(instruments),
            timeframes=_coerce_string_sequence(timeframes),
            decision_cadence=decision_cadence,
            data_boundary_description=data_boundary_description,
            point_in_time_policy=point_in_time_policy,
            strategy_identities=_coerce_string_sequence(strategy_identities),
            model_identities=_coerce_string_sequence(model_identities),
            config_fingerprints=_coerce_string_sequence(config_fingerprints),
            execution_methodology=execution_methodology,
            cost_methodology=cost_methodology,
            initial_capital=initial_capital,
            base_currency=base_currency,
            benchmark_identities=normalized_benchmarks,
            deterministic_seed=deterministic_seed,
            notes=notes,
            producer=producer,
            producer_version=producer_version,
            methodology_version=methodology_version,
        )
        return cls(
            specification_id=provisional._canonical_specification_id(),
            run_id=uuid5(REPLAY_RUN_ID_NAMESPACE, provisional.specification_id),
            logical_as_of=provisional.logical_as_of,
            recorded_at=provisional.recorded_at,
            start_time=provisional.start_time,
            end_time=provisional.end_time,
            instruments=provisional.instruments,
            timeframes=provisional.timeframes,
            decision_cadence=provisional.decision_cadence,
            data_boundary_description=provisional.data_boundary_description,
            point_in_time_policy=provisional.point_in_time_policy,
            strategy_identities=provisional.strategy_identities,
            model_identities=provisional.model_identities,
            config_fingerprints=provisional.config_fingerprints,
            execution_methodology=provisional.execution_methodology,
            cost_methodology=provisional.cost_methodology,
            initial_capital=provisional.initial_capital,
            base_currency=provisional.base_currency,
            benchmark_identities=provisional.benchmark_identities,
            deterministic_seed=provisional.deterministic_seed,
            notes=provisional.notes,
            producer=provisional.producer,
            producer_version=provisional.producer_version,
            methodology_version=provisional.methodology_version,
        )


class HistoricalReplayRun(_ReplayFrozenModel):
    """Deterministic lifecycle identity for one historical replay execution.

    The run identity is derived from the immutable specification plus the
    explicit lifecycle state. Transitions are represented by new immutable
    run artifacts rather than rewriting prior history.
    """

    run_id: UUID
    specification_id: str = Field(min_length=64, max_length=64)
    correlation_id: UUID
    logical_as_of: UtcDatetime
    recorded_at: UtcDatetime
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    lifecycle: tuple[ReplayRunStatus, ...] = (ReplayRunStatus.CREATED,)

    @field_validator("logical_as_of", "recorded_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_replay_timestamp(value)

    @field_validator("lifecycle", mode="before")
    @classmethod
    def coerce_lifecycle(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(ReplayRunStatus(item) if isinstance(item, str) else item for item in value)
        return value

    @model_validator(mode="after")
    def validate_run(self) -> HistoricalReplayRun:
        if self.logical_as_of > self.recorded_at:
            raise ValueError("logical_as_of cannot be after recorded_at")
        if not self.lifecycle or self.lifecycle[0] is not ReplayRunStatus.CREATED:
            raise ValueError("lifecycle must begin with created")
        for previous, current in zip(self.lifecycle, self.lifecycle[1:], strict=False):
            if current not in _ALLOWED_REPLAY_TRANSITIONS[previous]:
                raise ValueError(f"invalid replay run lifecycle transition: {previous.value} -> {current.value}")
        if self.run_id != self._canonical_run_id():
            raise ValueError("run_id does not match canonical replay run identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_id", "lifecycle"})

    def _canonical_run_id(self) -> UUID:
        return uuid5(REPLAY_RUN_ID_NAMESPACE, canonical_json(self._identity_material()))

    @property
    def status(self) -> ReplayRunStatus:
        return self.lifecycle[-1]

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    def transition_to(self, status: ReplayRunStatus) -> HistoricalReplayRun:
        values = self.model_dump()
        values["lifecycle"] = (*self.lifecycle, status)
        return type(self).model_validate(values)

    @classmethod
    def create(
        cls,
        *,
        specification_id: str,
        correlation_id: UUID,
        logical_as_of: datetime,
        recorded_at: datetime,
        producer: str,
        producer_version: str,
    ) -> HistoricalReplayRun:
        normalized_logical_as_of = _normalize_replay_timestamp(logical_as_of)
        normalized_recorded_at = _normalize_replay_timestamp(recorded_at)
        provisional = cls.model_construct(
            run_id=UUID(int=0),
            specification_id=specification_id,
            correlation_id=correlation_id,
            logical_as_of=normalized_logical_as_of,
            recorded_at=normalized_recorded_at,
            producer=producer,
            producer_version=producer_version,
            lifecycle=(ReplayRunStatus.CREATED,),
        )
        return cls(
            run_id=provisional._canonical_run_id(),
            specification_id=specification_id,
            correlation_id=correlation_id,
            logical_as_of=normalized_logical_as_of,
            recorded_at=normalized_recorded_at,
            producer=producer,
            producer_version=producer_version,
            lifecycle=(ReplayRunStatus.CREATED,),
        )


class ReplayRunEvent(_ReplayFrozenModel):
    """An immutable, hash-chainable causal event belonging to a replay run."""

    run_id: UUID
    sequence: int = Field(gt=0)
    event_id: UUID
    correlation_id: UUID
    causation_id: UUID | None = None
    logical_as_of: UtcDatetime
    recorded_at: UtcDatetime
    event_type: str = Field(min_length=1)
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    payload_reference: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prior_event_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("logical_as_of", "recorded_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_replay_timestamp(value)

    @model_validator(mode="after")
    def validate_event(self, info: ValidationInfo) -> ReplayRunEvent:
        if self.logical_as_of > self.recorded_at:
            raise ValueError("logical_as_of cannot be after recorded_at")
        if self.causation_id == self.event_id:
            raise ValueError("event cannot cause itself")
        if self.sequence == 1 and self.prior_event_hash != REPLAY_GENESIS_EVENT_HASH:
            raise ValueError("first event must use the genesis prior-event hash")
        if self.sequence > 1 and self.prior_event_hash == REPLAY_GENESIS_EVENT_HASH:
            raise ValueError("non-first event must reference a prior event hash")
        deriving_identity = isinstance(info.context, dict) and info.context.get("derive_event_id") is True
        if not deriving_identity and self.event_id != self._canonical_event_id():
            raise ValueError("event_id does not match canonical event identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"event_id"})

    def _canonical_event_id(self) -> UUID:
        return uuid5(REPLAY_ID_NAMESPACE, canonical_json(self._identity_material()))

    @property
    def event_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: Any) -> ReplayRunEvent:
        material = dict(values)
        material.pop("event_id", None)
        material.setdefault("schema_version", REPLAY_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)

        provisional = cls.model_validate(
            {"event_id": UUID(int=0), **material},
            context={"derive_event_id": True},
        )
        event_id = provisional._canonical_event_id()
        if provisional.causation_id == event_id:
            raise ValueError("event cannot cause itself")
        normalized = provisional.model_dump(exclude={"event_id"})
        return cls(event_id=event_id, **normalized)


class BacktestRunManifest(BaseModel):
    """Immutable manifest identifying one historical replay execution.

    The manifest references upstream artifacts by immutable IDs only. It
    never duplicates full artifact payloads.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    manifest_schema_version: Literal["1.0"] = REPLAY_SCHEMA_VERSION
    replay_run_id: UUID = Field(frozen=True)
    specification_id: str = Field(min_length=64, max_length=64, frozen=True)
    logical_as_of: UtcDatetime
    producer_version: str = Field(min_length=1, frozen=True)
    upstream_artifact_ids: tuple[str, ...] = Field(min_length=1, frozen=True)
    deterministic_seed: int | None = Field(default=None, frozen=True)
    universe_identity: tuple[str, ...] = Field(min_length=1, frozen=True)
    methodology_identities: tuple[str, ...] = Field(min_length=1, frozen=True)
    status: ReplayRunStatus = Field(frozen=True)
    start_time: UtcDatetime = Field(frozen=True)
    end_time: UtcDatetime = Field(frozen=True)
    notes: str | None = Field(default=None, min_length=1, frozen=True)

    @field_validator("logical_as_of", "start_time", "end_time", mode="before")
    @classmethod
    def normalize_manifest_timestamps(cls, value: object) -> datetime:
        return _normalize_replay_timestamp(value)

    @field_validator("upstream_artifact_ids", mode="before")
    @classmethod
    def coerce_artifact_sequence(cls, value: object) -> tuple[str, ...]:
        return _coerce_string_sequence(value)

    @model_validator(mode="after")
    def validate_manifest(self) -> BacktestRunManifest:
        if not self.upstream_artifact_ids:
            raise ValueError("upstream_artifact_ids must not be empty")
        if self.start_time >= self.end_time:
            raise ValueError("manifest start_time must be before end_time")
        return self

    @classmethod
    def from_specification_and_run(
        cls,
        *,
        replay_run: HistoricalReplayRun,
        specification: HistoricalReplaySpecification,
        notes: str | None = None,
    ) -> BacktestRunManifest:
        return cls(
            replay_run_id=replay_run.run_id,
            specification_id=specification.specification_id,
            logical_as_of=replay_run.logical_as_of,
            producer_version=replay_run.producer_version,
            upstream_artifact_ids=(
                specification.specification_id,
                replay_run.run_id.hex,
            ),
            deterministic_seed=specification.deterministic_seed,
            universe_identity=_coerce_string_sequence(specification.instruments),
            methodology_identities=(
                *specification.strategy_identities,
                *specification.model_identities,
                *specification.config_fingerprints,
            ),
            status=replay_run.status,
            start_time=specification.start_time,
            end_time=specification.end_time,
            notes=notes,
        )

    def manifest_fingerprint(self) -> str:
        return sha256_fingerprint(self._manifest_material())

    def _manifest_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"manifest_schema_version"})

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.BACKTEST_RUN_MANIFEST,
            logical_as_of=self.logical_as_of,
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.RESEARCH_RUN,
                    identifier=self.replay_run_id.hex,
                    description="backtest manifest for replay run",
                    producer="phase16a",
                    producer_version="1.0",
                ),
            ),
        )


__all__ = [
    "REPLAY_GENESIS_EVENT_HASH",
    "REPLAY_ID_NAMESPACE",
    "REPLAY_RUN_ID_NAMESPACE",
    "REPLAY_SCHEMA_VERSION",
    "BacktestRunManifest",
    "HistoricalReplayRun",
    "HistoricalReplaySpecification",
    "ReplayRunEvent",
    "ReplayRunStatus",
]
