"""Immutable historical decision-step contracts for Phase 16C.

This module defines the canonical record for a single historical decision
point produced by the deterministic historical decision-pipeline orchestrator.
It does not execute decisions or connect to live execution components.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
)
from app.domain.models.market_data import UtcDatetime, _require_aware_utc

HISTORICAL_DECISION_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class HistoricalDecisionStepStatus(StrEnum):
    """Explicit deterministic lifecycle states for a historical decision step."""

    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    FAILED = "failed"


_VALID_HISTORICAL_EXECUTION_MODES = frozenset({"DETERMINISTIC_RECOMPUTE", "PERSISTED_REPLAY"})


class HistoricalDecisionStep(BaseModel):
    """Immutable canonical record for one historical decision point."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = HISTORICAL_DECISION_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    step_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    step_sequence: int = Field(gt=0)
    simulated_at: UtcDatetime
    point_in_time_snapshot_id: str = Field(min_length=64, max_length=64)
    snapshot_simulated_at: UtcDatetime
    research_run_id: str | None = None
    decision_run_manifest_id: str | None = None
    terminal_artifact_id: str | None = None
    trade_decision_artifact_id: str | None = None
    decision_journal_entry_id: str | None = None
    methodology_version: str = Field(min_length=1)
    execution_mode: str = Field(min_length=1)
    status: str = Field(min_length=1)
    failure_reference: str | None = None
    producer_version: str = Field(min_length=1)
    input_fingerprints: tuple[str, ...] = ()
    lineage_artifact_ids: tuple[str, ...] = ()

    @field_validator("simulated_at", "snapshot_simulated_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("input_fingerprints", "lineage_artifact_ids", mode="before")
    @classmethod
    def coerce_string_sequences(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("sequence fields must be a list or tuple")

    @field_validator("execution_mode")
    @classmethod
    def validate_execution_mode(cls, value: str) -> str:
        if value not in _VALID_HISTORICAL_EXECUTION_MODES:
            raise ValueError(f"execution_mode must be one of {sorted(_VALID_HISTORICAL_EXECUTION_MODES)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {item.value for item in HistoricalDecisionStepStatus}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def validate_step(self) -> HistoricalDecisionStep:
        if self.snapshot_simulated_at != self.simulated_at:
            raise ValueError("snapshot_simulated_at must equal simulated_at")
        if self.status == HistoricalDecisionStepStatus.COMPLETED.value and not self.trade_decision_artifact_id:
            raise ValueError("completed steps require a trade_decision_artifact_id")
        if self.status == HistoricalDecisionStepStatus.FAILED.value and not self.failure_reference:
            raise ValueError("failed steps require a failure_reference")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={
                "step_id",
                "trade_decision_artifact_id",
                "decision_run_manifest_id",
                "decision_journal_entry_id",
                "terminal_artifact_id",
            },
        )

    def _canonical_step_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: Any) -> HistoricalDecisionStep:
        material = dict(values)
        material.setdefault("schema_version", HISTORICAL_DECISION_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        provisional = cls.model_validate({"step_id": "0" * 64, **material})
        step_id = provisional._canonical_step_id()
        return cls(step_id=step_id, **material)

    @classmethod
    def create_completed(cls, **values: Any) -> HistoricalDecisionStep:
        values["status"] = HistoricalDecisionStepStatus.COMPLETED.value
        step = cls.create(**values)
        if step.trade_decision_artifact_id is None:
            raise ValueError("completed steps require a trade_decision_artifact_id")
        return step

    @classmethod
    def create_failed(cls, **values: Any) -> HistoricalDecisionStep:
        values["status"] = HistoricalDecisionStepStatus.FAILED.value
        step = cls.create(**values)
        if step.failure_reference is None:
            raise ValueError("failed steps require a failure_reference")
        return step

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_DECISION_STEP,
            logical_as_of=self.simulated_at,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


__all__ = [
    "HISTORICAL_DECISION_SCHEMA_VERSION",
    "HistoricalDecisionStep",
    "HistoricalDecisionStepStatus",
]
