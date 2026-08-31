"""Immutable paper order and paper execution result contracts for Phase 16D.

This module defines the canonical simulated order and execution-result records
for research-only paper execution. It never imports or invokes broker, risk,
portfolio, or live execution components.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from uuid import UUID

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference
from app.domain.models.decision import TradeDecision
from app.domain.models.historical_decision import HistoricalDecisionStep
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import Symbol, UtcDatetime, _require_aware_utc

PAPER_ORDER_SCHEMA_VERSION: Literal["1.0"] = "1.0"
PAPER_FILL_SCHEMA_VERSION: Literal["1.0"] = "1.0"
PAPER_EXECUTION_RESULT_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class PaperOrderSide(StrEnum):
    """Paper execution side derived from canonical decision action."""

    BUY = "BUY"
    SELL = "SELL"


class PaperOrderStatus(StrEnum):
    """Immutable paper-order lifecycle statuses."""

    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    UNFILLED = "unfilled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PaperFillStatus(StrEnum):
    """Immutable paper-fill statuses."""

    FULL = "full"
    PARTIAL = "partial"


class _PaperFrozenModel(BaseModel):
    """Base frozen contract for Phase 16D paper execution models."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _PaperFrozenModel:
        raise TypeError("paper execution contracts are immutable and do not support model_copy")


def _normalize_paper_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return _require_aware_utc(value)


class PaperOrder(_PaperFrozenModel):
    """Immutable simulated paper order.

    A PaperOrder describes the simulated instruction only. It never implies
    acceptance by a live broker adapter.
    """

    paper_order_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    historical_decision_step_id: str = Field(min_length=64, max_length=64)
    trade_decision_artifact_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    side: PaperOrderSide
    quantity: int = Field(gt=0)
    order_type: Literal["market"] = "market"
    submitted_at: UtcDatetime
    eligible_execution_at: UtcDatetime
    execution_methodology_id: str = Field(min_length=64, max_length=64)
    status: PaperOrderStatus = PaperOrderStatus.SUBMITTED

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("side", mode="before")
    @classmethod
    def coerce_side(cls, value: object) -> PaperOrderSide:
        if isinstance(value, str):
            return PaperOrderSide(value)
        if isinstance(value, PaperOrderSide):
            return value
        raise TypeError("side must be a PaperOrderSide or valid string")

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, value: object) -> PaperOrderStatus:
        if isinstance(value, str):
            return PaperOrderStatus(value)
        if isinstance(value, PaperOrderStatus):
            return value
        raise TypeError("status must be a PaperOrderStatus or valid string")

    @field_validator("submitted_at", "eligible_execution_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_paper_timestamp(value)

    @model_validator(mode="after")
    def validate_execution_timing(self) -> PaperOrder:
        if self.eligible_execution_at < self.submitted_at:
            raise ValueError("eligible_execution_at must be at or after submitted_at")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"paper_order_id"},
        )

    def _canonical_paper_order_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> PaperOrder:
        material = dict(values)
        material.setdefault("schema_version", PAPER_ORDER_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.setdefault("status", PaperOrderStatus.SUBMITTED.value)
        material.setdefault("order_type", "market")
        material.pop("paper_order_id", None)
        provisional = cls.model_validate({"paper_order_id": "0" * 64, **dict(material)})
        paper_order_id = provisional._canonical_paper_order_id()
        return cls(paper_order_id=paper_order_id, **dict(material))

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PAPER_ORDER,
            logical_as_of=self.submitted_at,
            producer_version="phase16d-1.0",
            provenance_references=provenance_references,
        )


class PaperFill(_PaperFrozenModel):
    """Immutable paper fill record."""

    paper_fill_id: str = Field(min_length=64, max_length=64)
    paper_order_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    side: PaperOrderSide
    quantity: int = Field(gt=0)
    execution_price: float = Field(gt=0, allow_inf_nan=False)
    executed_at: UtcDatetime
    source_bar_timestamp: UtcDatetime
    methodology_id: str = Field(min_length=64, max_length=64)
    status: PaperFillStatus

    @field_validator("side", mode="before")
    @classmethod
    def coerce_side(cls, value: object) -> PaperOrderSide:
        if isinstance(value, str):
            return PaperOrderSide(value)
        if isinstance(value, PaperOrderSide):
            return value
        raise TypeError("side must be a PaperOrderSide or valid string")

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, value: object) -> PaperFillStatus:
        if isinstance(value, str):
            return PaperFillStatus(value)
        if isinstance(value, PaperFillStatus):
            return value
        raise TypeError("status must be a PaperFillStatus or valid string")

    @field_validator("executed_at", "source_bar_timestamp", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_paper_timestamp(value)

    @model_validator(mode="after")
    def validate_execution_availability(self) -> PaperFill:
        if self.executed_at < self.source_bar_timestamp:
            raise ValueError("executed_at must be at or after source_bar_timestamp")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"paper_fill_id"},
        )

    def _canonical_paper_fill_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> PaperFill:
        material = dict(values)
        material.setdefault("schema_version", PAPER_FILL_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        provisional = cls.model_validate({"paper_fill_id": "0" * 64, **dict(material)})
        paper_fill_id = provisional._canonical_paper_fill_id()
        return cls(paper_fill_id=paper_fill_id, **dict(material))

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PAPER_FILL,
            logical_as_of=self.executed_at,
            producer_version="phase16d-1.0",
            provenance_references=provenance_references,
        )


class PaperExecutionResult(_PaperFrozenModel):
    """Immutable paper execution result tying together order and fills."""

    paper_execution_result_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    historical_decision_step_id: str = Field(min_length=64, max_length=64)
    trade_decision_artifact_id: str = Field(min_length=64, max_length=64)
    paper_order_id: str = Field(min_length=64, max_length=64)
    execution_methodology_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    side: PaperOrderSide
    requested_quantity: int = Field(ge=0)
    filled_quantity: int = Field(ge=0)
    remaining_quantity: int = Field(ge=0)
    execution_status: PaperOrderStatus
    execution_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    executed_at: UtcDatetime | None = None
    paper_fill_ids: tuple[str, ...] = ()
    transaction_cost_bps: float = Field(ge=0.0)
    additional_slippage_bps: float = Field(ge=0.0)

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("executed_at", mode="before")
    @classmethod
    def normalize_executed_at(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return _normalize_paper_timestamp(value)

    @model_validator(mode="after")
    def validate_quantities(self) -> PaperExecutionResult:
        if self.filled_quantity > self.requested_quantity:
            raise ValueError("filled_quantity cannot exceed requested_quantity")
        if self.remaining_quantity != self.requested_quantity - self.filled_quantity:
            raise ValueError("remaining_quantity must equal requested_quantity minus filled_quantity")
        if self.execution_status in {PaperOrderStatus.FILLED, PaperOrderStatus.PARTIALLY_FILLED}:
            if self.execution_price is None or self.executed_at is None:
                raise ValueError("filled or partially filled results require execution_price and executed_at")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"paper_execution_result_id"},
        )

    def _canonical_paper_execution_result_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> PaperExecutionResult:
        material = dict(values)
        material.setdefault("schema_version", PAPER_EXECUTION_RESULT_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.setdefault("transaction_cost_bps", 0.0)
        material.setdefault("additional_slippage_bps", 0.0)
        provisional = cls.model_validate({"paper_execution_result_id": "0" * 64, **material})
        paper_execution_result_id = provisional._canonical_paper_execution_result_id()
        return cls(paper_execution_result_id=paper_execution_result_id, **material)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        logical_as_of = self.executed_at or datetime.fromisoformat("1970-01-01T00:00:00+00:00")
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PAPER_EXECUTION_RESULT,
            logical_as_of=logical_as_of,
            producer_version="phase16d-1.0",
            provenance_references=provenance_references,
        )


__all__ = [
    "PAPER_EXECUTION_RESULT_SCHEMA_VERSION",
    "PAPER_FILL_SCHEMA_VERSION",
    "PAPER_ORDER_SCHEMA_VERSION",
    "PaperExecutionResult",
    "PaperFill",
    "PaperFillStatus",
    "PaperOrder",
    "PaperOrderSide",
    "PaperOrderStatus",
]
