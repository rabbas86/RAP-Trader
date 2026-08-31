"""Immutable paper-execution methodology contracts for Phase 16D.

This module defines the explicit deterministic execution methodology for
research-only paper execution simulation. It does not connect to any broker,
exchange, or live execution component.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.market_data import UtcDatetime, _require_aware_utc

PAPER_EXECUTION_METHODOLOGY_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class FillTimingPolicy(StrEnum):
    """Explicit deterministic fill-timing policies for paper execution."""

    NEXT_BAR_CLOSE = "next_bar_close"


class PaperOrderType(StrEnum):
    """Paper-only order types supported in Phase 16D."""

    MARKET = "market"


class UnfilledOrderPolicy(StrEnum):
    """Explicit unfilled-order policies for paper execution."""

    CANCEL = "cancel"
    EXPIRE = "expire"


class TimeInForce(StrEnum):
    """Explicit time-in-force assumptions for paper execution."""

    DAY = "day"


class PaperExecutionMethodology(BaseModel):
    """Immutable deterministic paper-execution methodology.

    The methodology identity is derived from canonical content. The same
    methodology material always yields the same ``methodology_id``.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = PAPER_EXECUTION_METHODOLOGY_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    methodology_id: str = Field(min_length=64, max_length=64)
    methodology_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    fill_timing_policy: FillTimingPolicy
    order_type: PaperOrderType = PaperOrderType.MARKET
    allow_partial_fills: bool = False
    price_source: str = Field(min_length=1)
    time_in_force: TimeInForce = TimeInForce.DAY
    unfilled_order_policy: UnfilledOrderPolicy = UnfilledOrderPolicy.CANCEL
    transaction_cost_bps: float = Field(default=0.0, ge=0.0)
    additional_slippage_bps: float = Field(default=0.0, ge=0.0)
    producer_version: str = Field(min_length=1)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> PaperExecutionMethodology:
        raise TypeError("paper execution methodology is immutable and does not support model_copy")

    @field_validator("fill_timing_policy", mode="before")
    @classmethod
    def coerce_fill_timing_policy(cls, value: object) -> FillTimingPolicy:
        if isinstance(value, str):
            return FillTimingPolicy(value)
        if isinstance(value, FillTimingPolicy):
            return value
        raise TypeError("fill_timing_policy must be a FillTimingPolicy or valid string")

    @field_validator("order_type", mode="before")
    @classmethod
    def coerce_order_type(cls, value: object) -> PaperOrderType:
        if isinstance(value, str):
            return PaperOrderType(value)
        if isinstance(value, PaperOrderType):
            return value
        raise TypeError("order_type must be a PaperOrderType or valid string")

    @field_validator("time_in_force", mode="before")
    @classmethod
    def coerce_time_in_force(cls, value: object) -> TimeInForce:
        if isinstance(value, str):
            return TimeInForce(value)
        if isinstance(value, TimeInForce):
            return value
        raise TypeError("time_in_force must be a TimeInForce or valid string")

    @field_validator("unfilled_order_policy", mode="before")
    @classmethod
    def coerce_unfilled_order_policy(cls, value: object) -> UnfilledOrderPolicy:
        if isinstance(value, str):
            return UnfilledOrderPolicy(value)
        if isinstance(value, UnfilledOrderPolicy):
            return value
        raise TypeError("unfilled_order_policy must be an UnfilledOrderPolicy or valid string")

    @model_validator(mode="after")
    def validate_methodology_consistency(self) -> PaperExecutionMethodology:
        if self.price_source != "next_bar_close":
            raise ValueError("price_source must be 'next_bar_close' for the current completed-bar model")
        return self

    def _identity_material(self) -> dict[str, object]:
        return self.model_dump(
            mode="json",
            exclude={"methodology_id"},
        )

    def _canonical_methodology_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> PaperExecutionMethodology:
        material = dict(values)
        material.setdefault("schema_version", PAPER_EXECUTION_METHODOLOGY_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        provisional = cls.model_validate({"methodology_id": "0" * 64, **dict(material)})
        methodology_id = provisional._canonical_methodology_id()
        return cls(methodology_id=methodology_id, **dict(material))


__all__ = [
    "FillTimingPolicy",
    "PAPER_EXECUTION_METHODOLOGY_SCHEMA_VERSION",
    "PaperExecutionMethodology",
    "PaperOrderType",
    "TimeInForce",
    "UnfilledOrderPolicy",
]
