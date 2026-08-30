"""Immutable outcome observation and evaluation contracts.

These models represent the post-decision observation and evaluation layer.
They are strictly separated from T0 decision artifacts and never mutate
historical decision state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import canonical_json, sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.market_data import Symbol, UtcDatetime

OUTCOME_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class OutcomeStatus(StrEnum):
    """Lifecycle status of an outcome observation/evaluation."""

    PENDING = "PENDING"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    COMPLETED = "COMPLETED"


class ReferencePriceMethodology(StrEnum):
    """Canonical methodology for selecting the reference price at decision time."""

    DECISION_BAR_CLOSE = "decision_bar_close"
    DECISION_BAR_OPEN = "decision_bar_open"
    DECISION_SESSION_SETTLEMENT = "decision_session_settlement"


class FuturePriceMethodology(StrEnum):
    """Canonical methodology for selecting the observed future price."""

    OBSERVATION_BAR_CLOSE = "observation_bar_close"
    OBSERVATION_BAR_OPEN = "observation_bar_open"
    OBSERVATION_SESSION_SETTLEMENT = "observation_session_settlement"


class OutcomeObservation(BaseModel):
    """Immutable canonical record of observed market data after a decision."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    observation_id: str = Field(min_length=64, max_length=64)
    outcome_schema_version: Literal["1.0"] = OUTCOME_SCHEMA_VERSION
    decision_artifact_id: str = Field(min_length=64, max_length=64)
    decision_journal_entry_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    decision_at: UtcDatetime
    observation_at: UtcDatetime
    horizon: int = Field(gt=0)
    evaluation_timeframe: str = Field(min_length=1)
    reference_price_methodology: ReferencePriceMethodology
    observed_future_price_methodology: FuturePriceMethodology
    reference_price_at_decision: float = Field(gt=0, allow_inf_nan=False)
    observed_future_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    market_data_provider: str = Field(min_length=1)
    adjustment: str = Field(min_length=1)
    session: str = Field(min_length=1)
    outcome_status: OutcomeStatus

    @field_validator("reference_price_methodology", "observed_future_price_methodology", "outcome_status", mode="before")
    @classmethod
    def coerce_enum(cls, value: object) -> object:
        if isinstance(value, str):
            if value in ReferencePriceMethodology._value2member_map_:
                return ReferencePriceMethodology(value)
            if value in FuturePriceMethodology._value2member_map_:
                return FuturePriceMethodology(value)
            if value in OutcomeStatus._value2member_map_:
                return OutcomeStatus(value)
        return value

    @field_validator("decision_at", "observation_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            normalized = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            normalized = value
        else:
            raise TypeError("timestamp must be an ISO datetime string or datetime instance")
        if normalized.tzinfo is None or normalized.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return normalized.astimezone(UTC)

    @model_validator(mode="after")
    def validate_temporal_order(self) -> OutcomeObservation:
        if self.observation_at <= self.decision_at:
            raise ValueError("observation_at must be after decision_at")
        if self.outcome_status == OutcomeStatus.COMPLETED and self.observed_future_price is None:
            raise ValueError("completed observations require observed_future_price")
        if self.outcome_status != OutcomeStatus.COMPLETED and self.observed_future_price is not None:
            raise ValueError("non-completed observations must not contain observed_future_price")
        return self

    def fingerprint(self) -> str:
        material = self.model_dump(mode="json")
        return sha256_fingerprint(material)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OutcomeObservation:
        return cls.model_validate_json(canonical_json(data))

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.OUTCOME_OBSERVATION,
            logical_as_of=self.observation_at,
            producer_version="1.0",
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_artifact_id,
                    description="finalized decision artifact evaluated by this observation",
                    producer="rap-trader-outcome-journal",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_journal_entry_id,
                    description="decision journal entry for this observation",
                    producer="rap-trader-outcome-journal",
                    producer_version="1.0",
                ),
            ),
        )


class OutcomeEvaluation(BaseModel):
    """Immutable evaluation derived from a decision and its outcome observation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    evaluation_id: str = Field(min_length=64, max_length=64)
    outcome_schema_version: Literal["1.0"] = OUTCOME_SCHEMA_VERSION
    outcome_observation_id: str = Field(min_length=64, max_length=64)
    decision_artifact_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    decision_at: UtcDatetime
    direction: Literal["BUY", "SELL", "WAIT"]
    evaluation_horizon: int = Field(gt=0)
    raw_return: float | None = Field(default=None, allow_inf_nan=False)
    signed_return: float | None = Field(default=None, allow_inf_nan=False)
    directionally_correct: bool | None = Field(default=None)
    outcome_status: OutcomeStatus
    producer_version: str = Field(min_length=1)

    @field_validator("decision_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            normalized = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            normalized = value
        else:
            raise TypeError("timestamp must be an ISO datetime string or datetime instance")
        if normalized.tzinfo is None or normalized.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return normalized.astimezone(UTC)

    @field_validator("outcome_status", mode="before")
    @classmethod
    def coerce_outcome_status(cls, value: object) -> object:
        if isinstance(value, str) and value in OutcomeStatus._value2member_map_:
            return OutcomeStatus(value)
        return value

    @model_validator(mode="after")
    def validate_completed_metrics(self) -> OutcomeEvaluation:
        if self.outcome_status == OutcomeStatus.COMPLETED:
            if self.raw_return is None:
                raise ValueError("completed evaluations require raw_return")
            if self.signed_return is None:
                raise ValueError("completed evaluations require signed_return")
            if self.directionally_correct is None:
                raise ValueError("completed evaluations require directionally_correct")
        if self.outcome_status != OutcomeStatus.COMPLETED:
            if self.raw_return is not None:
                raise ValueError("non-completed evaluations must not contain raw_return")
            if self.signed_return is not None:
                raise ValueError("non-completed evaluations must not contain signed_return")
            if self.directionally_correct is not None:
                raise ValueError("non-completed evaluations must not contain directionally_correct")
        return self

    def fingerprint(self) -> str:
        material = self.model_dump(mode="json")
        return sha256_fingerprint(material)

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.OUTCOME_EVALUATION,
            logical_as_of=self.decision_at,
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.outcome_observation_id,
                    description="outcome observation evaluated here",
                    producer="rap-trader-outcome-journal",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_artifact_id,
                    description="finalized decision artifact evaluated here",
                    producer="rap-trader-outcome-journal",
                    producer_version="1.0",
                ),
            ),
        )


__all__ = [
    "OUTCOME_SCHEMA_VERSION",
    "FuturePriceMethodology",
    "OutcomeEvaluation",
    "OutcomeObservation",
    "OutcomeStatus",
    "ReferencePriceMethodology",
]
