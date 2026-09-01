"""Immutable portfolio accounting contracts for Phase 16E."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import Symbol, UtcDatetime, _require_aware_utc
from app.services.portfolio_accounting.errors import (
    InvalidFillError,
    InvalidMethodologyError,
    PortfolioAccountingValidationError,
)

PORTFOLIO_ACCOUNTING_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def _normalize_portfolio_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _require_aware_utc(value)
    if isinstance(value, str):
        return _normalize_portfolio_timestamp(datetime.fromisoformat(value))
    raise TypeError("portfolio timestamp must be a datetime or ISO-8601 string")


def _coerce_string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


class _PortfolioFrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = PORTFOLIO_ACCOUNTING_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _PortfolioFrozenModel:
        raise TypeError("portfolio accounting contracts are immutable and do not support model_copy")


class PortfolioAccountingMethodology(_PortfolioFrozenModel):
    methodology_id: str = Field(min_length=64, max_length=64)
    methodology_name: str = Field(min_length=1)
    cost_basis_method: str = Field(min_length=1)
    allow_shorting: bool = False
    allow_negative_cash: bool = False
    base_currency_behavior: str = Field(min_length=1)
    valuation_policy: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("cost_basis_method")
    @classmethod
    def validate_cost_basis_method(cls, value: str) -> str:
        allowed = {"average_cost", "fifo", "lifo"}
        if value not in allowed:
            raise ValueError(f"cost_basis_method must be one of {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def validate_methodology_consistency(self) -> PortfolioAccountingMethodology:
        if self.allow_shorting:
            raise InvalidMethodologyError("Phase 16E does not authorize shorting in the baseline methodology.")
        if self.allow_negative_cash:
            raise InvalidMethodologyError("Phase 16E baseline methodology does not allow negative cash.")
        if self.cost_basis_method != "average_cost":
            raise InvalidMethodologyError("Phase 16E baseline methodology requires average_cost accounting.")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"methodology_id"})

    def _canonical_methodology_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create(cls, **values: object) -> PortfolioAccountingMethodology:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PORTFOLIO_ACCOUNTING_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.setdefault("allow_shorting", False)
        material.setdefault("allow_negative_cash", False)
        material.pop("methodology_id", None)
        provisional = cls.model_validate({"methodology_id": "0" * 64, **material})
        canonical_id = provisional._canonical_methodology_id()
        payload = provisional.model_dump(mode="json")
        payload["methodology_id"] = canonical_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        if not provenance_references:
            raise ValueError("provenance_references must not be empty")
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PORTFOLIO_ACCOUNTING_METHODOLOGY,
            logical_as_of=datetime.fromisoformat("1970-01-01T00:00:00+00:00"),
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PositionState(_PortfolioFrozenModel):
    symbol: Symbol
    quantity: int = Field(ge=0)
    average_cost: float = Field(ge=0, allow_inf_nan=False)
    cost_basis: float = Field(ge=0, allow_inf_nan=False)
    realized_pnl: float = 0.0
    last_mark_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unrealized_pnl: float | None = Field(default=None, allow_inf_nan=False)
    market_value: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_position_math(self) -> PositionState:
        expected_cost_basis = round(self.quantity * self.average_cost, 10)
        if abs(self.cost_basis - expected_cost_basis) > 1e-9:
            raise PortfolioAccountingValidationError("cost_basis must equal quantity * average_cost")
        if self.quantity == 0:
            if self.average_cost != 0.0:
                raise PortfolioAccountingValidationError("zero-quantity positions must have zero average_cost")
            if self.cost_basis != 0.0:
                raise PortfolioAccountingValidationError("zero-quantity positions must have zero cost_basis")
            if self.last_mark_price is not None:
                raise PortfolioAccountingValidationError("zero-quantity positions must not retain a mark price")
            if self.unrealized_pnl is not None and self.unrealized_pnl != 0.0:
                raise PortfolioAccountingValidationError("zero-quantity positions must have zero unrealized_pnl")
            if self.market_value is not None and self.market_value != 0.0:
                raise PortfolioAccountingValidationError("zero-quantity positions must have zero market_value")
        else:
            if self.average_cost <= 0:
                raise PortfolioAccountingValidationError("long positions must have positive average_cost")
            if self.cost_basis <= 0:
                raise PortfolioAccountingValidationError("long positions must have positive cost_basis")
        return self

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def symbol_str(self) -> str:
        return str(self.symbol)

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def _canonical_position_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))


class PortfolioLedgerEntry(_PortfolioFrozenModel):
    portfolio_ledger_entry_id: str = Field(min_length=64, max_length=64)
    portfolio_snapshot_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    simulated_at: UtcDatetime
    executed_at: UtcDatetime
    paper_execution_result_id: str | None = Field(default=None, min_length=64, max_length=64)
    paper_fill_ids: tuple[str, ...] = ()
    prior_portfolio_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    methodology_id: str = Field(min_length=64, max_length=64)
    sequence: int = Field(ge=0)
    event_type: Literal["initial", "fill", "noop"] = "fill"
    notes: tuple[str, ...] = ()
    producer_version: str = Field(min_length=1)

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("simulated_at", "executed_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_portfolio_timestamp(value)

    @field_validator("paper_fill_ids", "notes", mode="before")
    @classmethod
    def coerce_sequence_fields(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("sequence fields must be a list or tuple")

    @model_validator(mode="after")
    def validate_entry_consistency(self) -> PortfolioLedgerEntry:
        if self.event_type == "fill" and not self.paper_fill_ids:
            raise InvalidFillError("fill ledger entries must reference at least one paper_fill_id")
        if self.paper_fill_ids and self.event_type not in {"fill", "noop"}:
            raise InvalidFillError("paper_fill_ids require event_type fill or noop")
        if self.executed_at < self.simulated_at:
            raise PortfolioAccountingValidationError("executed_at cannot be before simulated_at")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"portfolio_ledger_entry_id"})

    def _canonical_ledger_entry_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @classmethod
    def create_initial(
        cls, *, snapshot: PortfolioSnapshot, methodology_id: str, producer_version: str = "phase16e-1.0"
    ) -> PortfolioLedgerEntry:
        provisional = cls(
            portfolio_ledger_entry_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=snapshot.simulated_at,
            executed_at=snapshot.simulated_at,
            prior_portfolio_snapshot_id=None,
            methodology_id=methodology_id,
            sequence=0,
            event_type="initial",
            producer_version=producer_version,
        )
        canonical_id = provisional._canonical_ledger_entry_id()
        payload = provisional.model_dump(mode="json")
        payload["portfolio_ledger_entry_id"] = canonical_id
        return cls.model_validate(payload)

    @classmethod
    def create_fill(
        cls,
        *,
        snapshot: PortfolioSnapshot,
        result: Any,
        prior_snapshot_id: str,
        methodology_id: str,
        sequence: int,
        producer_version: str = "phase16e-1.0",
    ) -> PortfolioLedgerEntry:
        provisional = cls(
            portfolio_ledger_entry_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=snapshot.simulated_at,
            executed_at=snapshot.simulated_at,
            paper_execution_result_id=result.paper_execution_result_id,
            paper_fill_ids=tuple(result.paper_fill_ids),
            prior_portfolio_snapshot_id=prior_snapshot_id,
            methodology_id=methodology_id,
            sequence=sequence,
            event_type="fill",
            producer_version=producer_version,
        )
        canonical_id = provisional._canonical_ledger_entry_id()
        payload = provisional.model_dump(mode="json")
        payload["portfolio_ledger_entry_id"] = canonical_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PORTFOLIO_LEDGER_ENTRY,
            logical_as_of=self.simulated_at,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PortfolioSnapshot(_PortfolioFrozenModel):
    portfolio_snapshot_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    simulated_at: UtcDatetime
    base_currency: str = Field(min_length=3, max_length=3)
    cash: float = Field(allow_inf_nan=False)
    positions: tuple[PositionState, ...] = ()
    total_cost_basis: float = Field(ge=0, allow_inf_nan=False)
    realized_pnl: float = 0.0
    unrealized_pnl: float | None = Field(default=None, allow_inf_nan=False)
    market_value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prior_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    applied_fill_ids: tuple[str, ...] = ()
    accounting_methodology_id: str = Field(min_length=64, max_length=64)
    producer_version: str = Field(min_length=1)

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("simulated_at", mode="before")
    @classmethod
    def normalize_simulated_at(cls, value: object) -> datetime:
        return _normalize_portfolio_timestamp(value)

    @field_validator("positions", mode="before")
    @classmethod
    def coerce_positions(cls, value: object) -> tuple[PositionState, ...]:
        if value is None:
            return ()
        if isinstance(value, list):
            return tuple(PositionState.model_validate(item) if isinstance(item, dict) else item for item in value)
        if isinstance(value, tuple):
            return tuple(PositionState.model_validate(item) if isinstance(item, dict) else item for item in value)
        raise TypeError("positions must be a list or tuple")

    @field_validator("applied_fill_ids", mode="before")
    @classmethod
    def coerce_applied_fill_ids(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            normalized = []
            for item in value:
                if not isinstance(item, str) or len(item) != 64:
                    raise ValueError("applied_fill_ids must contain 64-character hex strings")
                normalized.append(item)
            return tuple(normalized)
        raise TypeError("applied_fill_ids must be a list or tuple")

    @model_validator(mode="after")
    def validate_snapshot_math(self) -> PortfolioSnapshot:
        symbols = [position.symbol_str for position in self.positions]
        if len(symbols) != len(set(symbols)):
            raise PortfolioAccountingValidationError("portfolio snapshot positions must be unique by symbol")
        expected_total_cost_basis = round(sum(position.cost_basis for position in self.positions), 10)
        if abs(self.total_cost_basis - expected_total_cost_basis) > 1e-9:
            raise PortfolioAccountingValidationError("total_cost_basis must equal the sum of position cost_basis values")
        if self.cash < 0:
            raise PortfolioAccountingValidationError("cash cannot be negative")
        if self.base_currency != self.base_currency.upper() or len(self.base_currency) != 3:
            raise PortfolioAccountingValidationError("base_currency must be a 3-character uppercase code")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"portfolio_snapshot_id"})

    def _canonical_snapshot_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))

    @property
    def is_flat(self) -> bool:
        return all(position.is_flat for position in self.positions)

    def position(self, symbol: str | Symbol) -> PositionState | None:
        target = str(symbol)
        for position in self.positions:
            if position.symbol_str == target:
                return position
        return None

    @classmethod
    def create_initial(cls, specification: HistoricalReplaySpecification, *, producer_version: str = "phase16e-1.0") -> PortfolioSnapshot:
        provisional = cls.model_construct(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=specification.specification_id,
            replay_run_id=specification.run_id,
            simulated_at=specification.start_time,
            base_currency=specification.base_currency.upper(),
            cash=specification.initial_capital,
            positions=(),
            total_cost_basis=0.0,
            realized_pnl=0.0,
            unrealized_pnl=None,
            market_value=None,
            prior_snapshot_id=None,
            applied_fill_ids=(),
            accounting_methodology_id="ca3a9eae53093d88f9214cd44452d4f1f674b45beea622767b0a0c0924eeefc4",
            producer_version=producer_version,
        )
        snapshot_id = provisional._canonical_snapshot_id()
        payload = provisional.model_dump(mode="json")
        payload["portfolio_snapshot_id"] = snapshot_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PORTFOLIO_SNAPSHOT,
            logical_as_of=self.simulated_at,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PortfolioFillApplicationResult(BaseModel):
    snapshot: PortfolioSnapshot
    ledger_entry: PortfolioLedgerEntry
    applied: bool
    failure_code: str | None = None
    failure_message: str | None = None

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


__all__ = [
    "PORTFOLIO_ACCOUNTING_SCHEMA_VERSION",
    "PortfolioAccountingMethodology",
    "PortfolioFillApplicationResult",
    "PortfolioLedgerEntry",
    "PortfolioSnapshot",
    "PositionState",
]
