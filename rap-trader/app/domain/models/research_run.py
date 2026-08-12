"""Canonical contracts for reproducible research decision runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self, cast
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.domain.canonical import canonical_json, sha256_fingerprint
from app.domain.models.market_data import UtcDatetime, _require_aware_utc

RESEARCH_RUN_SCHEMA_VERSION: Literal["1.0"] = "1.0"
GENESIS_EVENT_HASH = "0" * 64
_ID_NAMESPACE = UUID("9a8d86bf-c817-50e0-b86d-7f3e45b8140d")


class ResearchRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_ALLOWED_TRANSITIONS: dict[ResearchRunStatus, frozenset[ResearchRunStatus]] = {
    ResearchRunStatus.CREATED: frozenset({ResearchRunStatus.RUNNING, ResearchRunStatus.CANCELLED}),
    ResearchRunStatus.RUNNING: frozenset({ResearchRunStatus.COMPLETED, ResearchRunStatus.FAILED, ResearchRunStatus.CANCELLED}),
    ResearchRunStatus.COMPLETED: frozenset(),
    ResearchRunStatus.FAILED: frozenset(),
    ResearchRunStatus.CANCELLED: frozenset(),
}


class _ResearchRunModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = RESEARCH_RUN_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy through validation so permanent contract invariants cannot be bypassed."""
        values = self.model_dump(round_trip=True)
        if update is not None:
            values.update(update)
        return type(self).model_validate(values)


def _timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return _require_aware_utc(cast(datetime, value))


class ResearchRun(_ResearchRunModel):
    """Immutable identity and lifecycle of one complete research decision run."""

    run_id: UUID
    correlation_id: UUID
    logical_as_of: UtcDatetime
    recorded_at: UtcDatetime
    producer: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    lifecycle: tuple[ResearchRunStatus, ...] = (ResearchRunStatus.CREATED,)

    @field_validator("logical_as_of", "recorded_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        return _timestamp(value)

    @field_validator("lifecycle", mode="before")
    @classmethod
    def coerce_lifecycle(cls, value: object) -> object:
        if isinstance(value, (list, tuple)):
            return tuple(ResearchRunStatus(item) if isinstance(item, str) else item for item in value)
        return value

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        if self.logical_as_of > self.recorded_at:
            raise ValueError("logical_as_of cannot be after recorded_at")
        if not self.lifecycle or self.lifecycle[0] is not ResearchRunStatus.CREATED:
            raise ValueError("lifecycle must begin with created")
        for previous, current in zip(self.lifecycle, self.lifecycle[1:], strict=False):
            if current not in _ALLOWED_TRANSITIONS[previous]:
                raise ValueError(f"invalid research run lifecycle transition: {previous.value} -> {current.value}")
        if self.run_id != self._canonical_run_id():
            raise ValueError("run_id does not match canonical run identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"run_id", "lifecycle"})

    def _canonical_run_id(self) -> UUID:
        return uuid5(_ID_NAMESPACE, canonical_json(self._identity_material()))

    @property
    def status(self) -> ResearchRunStatus:
        return self.lifecycle[-1]

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    def transition_to(self, status: ResearchRunStatus) -> Self:
        values = self.model_dump()
        values["lifecycle"] = (*self.lifecycle, status)
        return type(self).model_validate(values)

    @classmethod
    def create(
        cls,
        *,
        correlation_id: UUID,
        logical_as_of: datetime,
        recorded_at: datetime,
        producer: str,
        producer_version: str,
    ) -> Self:
        normalized_logical_as_of = _timestamp(logical_as_of)
        normalized_recorded_at = _timestamp(recorded_at)
        provisional = cls.model_construct(
            run_id=UUID(int=0),
            correlation_id=correlation_id,
            logical_as_of=normalized_logical_as_of,
            recorded_at=normalized_recorded_at,
            producer=producer,
            producer_version=producer_version,
        )
        return cls(
            run_id=provisional._canonical_run_id(),
            correlation_id=correlation_id,
            logical_as_of=normalized_logical_as_of,
            recorded_at=normalized_recorded_at,
            producer=producer,
            producer_version=producer_version,
        )


class RunEvent(_ResearchRunModel):
    """An immutable, hash-chainable causal event belonging to a research run."""

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
    def normalize_timestamp(cls, value: object) -> datetime:
        return _timestamp(value)

    @model_validator(mode="after")
    def validate_event(self, info: ValidationInfo) -> Self:
        if self.logical_as_of > self.recorded_at:
            raise ValueError("logical_as_of cannot be after recorded_at")
        if self.causation_id == self.event_id:
            raise ValueError("event cannot cause itself")
        if self.sequence == 1 and self.prior_event_hash != GENESIS_EVENT_HASH:
            raise ValueError("first event must use the genesis prior-event hash")
        if self.sequence > 1 and self.prior_event_hash == GENESIS_EVENT_HASH:
            raise ValueError("non-first event must reference a prior event hash")
        deriving_identity = isinstance(info.context, dict) and info.context.get("derive_event_id") is True
        if not deriving_identity and self.event_id != self._canonical_event_id():
            raise ValueError("event_id does not match canonical event identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"event_id"})

    def _canonical_event_id(self) -> UUID:
        return uuid5(_ID_NAMESPACE, canonical_json(self._identity_material()))

    @property
    def event_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: Any) -> Self:
        """Validate and normalize content before deriving its deterministic ID."""
        material = dict(values)
        material.pop("event_id", None)
        material.setdefault("schema_version", RESEARCH_RUN_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)

        # Run the complete contract first. This normalizes timestamps and validates
        # every field that participates in identity before UUID5 sees the material.
        provisional = cls.model_validate(
            {"event_id": UUID(int=0), **material},
            context={"derive_event_id": True},
        )
        event_id = provisional._canonical_event_id()
        normalized = provisional.model_dump(exclude={"event_id"})
        return cls(event_id=event_id, **normalized)


__all__ = ["GENESIS_EVENT_HASH", "RESEARCH_RUN_SCHEMA_VERSION", "ResearchRun", "ResearchRunStatus", "RunEvent"]
