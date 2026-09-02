"""Immutable transaction-cost and corporate-action contracts for Phase 16F."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference
from app.domain.models.market_data import Symbol, UtcDatetime, _require_aware_utc
from app.services.paper_execution.contracts import PaperOrderSide
from app.services.portfolio_accounting.errors import InvalidCostInputError

PHASE16F_SCHEMA_VERSION: Literal["1.0"] = "1.0"


def _normalize_cost_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _require_aware_utc(value)
    if isinstance(value, str):
        return _normalize_cost_timestamp(datetime.fromisoformat(value))
    raise TypeError("cost timestamp must be a datetime or ISO-8601 string")


def _coerce_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _require_aware_utc(value)
    if isinstance(value, str):
        if not value:
            return None
        return _normalize_cost_timestamp(value)
    raise TypeError("cost timestamp must be a datetime or ISO-8601 string")


class _Phase16FFrozenModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = PHASE16F_SCHEMA_VERSION
    research_only: Literal[True] = True
    paper_trading_only: Literal[True] = True
    suitable_for_live_trading: Literal[False] = False

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> _Phase16FFrozenModel:
        raise TypeError("Phase 16F contracts are immutable and do not support model_copy")

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


class TransactionCostMethodology(_Phase16FFrozenModel):
    """Immutable explicit transaction-cost methodology.

    All configured cost components are explicit and non-negative.
    Spread is the full bid/ask spread; per-side spread cost is half.
    """

    methodology_id: str = Field(min_length=64, max_length=64)
    methodology_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    spread_bps: float = Field(allow_inf_nan=False)
    slippage_bps: float = Field(default=0.0, allow_inf_nan=False)
    fixed_commission: float = Field(default=0.0, allow_inf_nan=False)
    per_unit_commission: float = Field(default=0.0, allow_inf_nan=False)
    commission_bps: float = Field(default=0.0, allow_inf_nan=False)
    minimum_commission: float | None = Field(default=None, allow_inf_nan=False)
    producer_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_non_negative_inputs(self) -> TransactionCostMethodology:
        if self.spread_bps < 0:
            raise InvalidCostInputError("spread_bps must be non-negative")
        if self.slippage_bps < 0:
            raise InvalidCostInputError("slippage_bps must be non-negative")
        if self.fixed_commission < 0:
            raise InvalidCostInputError("fixed_commission must be non-negative")
        if self.per_unit_commission < 0:
            raise InvalidCostInputError("per_unit_commission must be non-negative")
        if self.commission_bps < 0:
            raise InvalidCostInputError("commission_bps must be non-negative")
        if self.minimum_commission is not None and self.minimum_commission < 0:
            raise InvalidCostInputError("minimum_commission must be non-negative")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"methodology_id"})

    def _canonical_methodology_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create(cls, **values: object) -> TransactionCostMethodology:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16F_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
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
            artifact_type=ArtifactType.TRANSACTION_COST_METHODOLOGY,
            logical_as_of=datetime(1970, 1, 1, tzinfo=UTC),
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class ExecutionCostAssessment(_Phase16FFrozenModel):
    """Immutable deterministic execution-cost assessment."""

    assessment_id: str = Field(min_length=64, max_length=64)
    paper_fill_id: str = Field(min_length=64, max_length=64)
    paper_order_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    symbol: Symbol
    side: PaperOrderSide
    quantity: int = Field(gt=0)
    reference_execution_price: float = Field(gt=0, allow_inf_nan=False)
    effective_execution_price: float = Field(gt=0, allow_inf_nan=False)
    reference_notional: float = Field(ge=0, allow_inf_nan=False)
    effective_notional: float = Field(ge=0, allow_inf_nan=False)
    commission: float = Field(ge=0, allow_inf_nan=False)
    spread_cost: float = Field(ge=0, allow_inf_nan=False)
    slippage_cost: float = Field(ge=0, allow_inf_nan=False)
    total_transaction_cost: float = Field(ge=0, allow_inf_nan=False)
    methodology_id: str = Field(min_length=64, max_length=64)
    simulated_at: UtcDatetime
    producer_version: str = Field(min_length=1)

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

    @field_validator("simulated_at", mode="before")
    @classmethod
    def normalize_simulated_at(cls, value: object) -> datetime:
        return _normalize_cost_timestamp(value)

    @model_validator(mode="after")
    def validate_notional_math(self) -> ExecutionCostAssessment:
        if round(self.reference_execution_price * self.quantity, 10) != self.reference_notional:
            raise ValueError("reference_notional must equal reference_execution_price * quantity")
        if round(self.effective_execution_price * self.quantity, 10) != self.effective_notional:
            raise ValueError("effective_notional must equal effective_execution_price * quantity")
        expected_total = round(abs(self.effective_notional - self.reference_notional) + self.commission, 10)
        if abs(self.total_transaction_cost - expected_total) > 1e-9:
            raise ValueError("total_transaction_cost must equal abs(effective_notional - reference_notional) + commission")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"assessment_id", "replay_run_id"})

    def _canonical_assessment_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create(cls, **values: object) -> ExecutionCostAssessment:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16F_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("assessment_id", None)
        provisional = cls.model_validate({"assessment_id": "0" * 64, **material})
        assessment_id = provisional._canonical_assessment_id()
        payload = provisional.model_dump(mode="json")
        payload["assessment_id"] = assessment_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.EXECUTION_COST_ASSESSMENT,
            logical_as_of=self.simulated_at,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class CorporateActionType(StrEnum):
    STOCK_SPLIT = "stock_split"
    CASH_DIVIDEND = "cash_dividend"


class CorporateActionStatus(StrEnum):
    ANNOUNCED = "announced"
    EFFECTIVE = "effective"
    PAID = "paid"


class CorporateActionEvent(_Phase16FFrozenModel):
    """Immutable corporate action event."""

    corporate_action_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    action_type: str = Field(min_length=1)
    announced_at: UtcDatetime | None = None
    effective_at: UtcDatetime | None = None
    ex_date: UtcDatetime | None = None
    payment_at: UtcDatetime | None = None
    split_ratio: tuple[int, int] | None = Field(default=None, min_length=2, max_length=2)
    dividend_per_share: float | None = Field(default=None, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    status: str = Field(min_length=1)
    price_adjustment_convention: str = Field(min_length=1)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    methodology_version: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, value: str) -> str:
        return str(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = {item.value for item in CorporateActionStatus}
        if value not in allowed:
            raise ValueError(f"status must be one of {sorted(allowed)}")
        return value

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("split_ratio", mode="before")
    @classmethod
    def coerce_split_ratio(cls, value: object) -> tuple[int, int] | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)):
            if len(value) != 2 or not all(isinstance(item, int) for item in value):
                raise ValueError("split_ratio must be a 2-item integer tuple")
            if value[0] <= 0 or value[1] <= 0:
                raise ValueError("split ratio parts must be positive")
            return (int(value[0]), int(value[1]))
        raise TypeError("split_ratio must be a 2-item integer tuple or None")

    @field_validator("announced_at", "effective_at", "ex_date", "payment_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime | None:
        return _coerce_datetime(value)

    @model_validator(mode="after")
    def validate_event_consistency(self) -> CorporateActionEvent:
        if self.action_type == CorporateActionType.STOCK_SPLIT.value and self.split_ratio is None:
            raise ValueError("stock_split requires split_ratio")
        if self.action_type == CorporateActionType.CASH_DIVIDEND.value and self.dividend_per_share is None:
            raise ValueError("cash_dividend requires dividend_per_share")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"corporate_action_id"})

    def _canonical_corporate_action_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create(cls, **values: object) -> CorporateActionEvent:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16F_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("corporate_action_id", None)
        provisional = cls.model_validate({"corporate_action_id": "0" * 64, **material})
        corporate_action_id = provisional._canonical_corporate_action_id()
        payload = provisional.model_dump(mode="json")
        payload["corporate_action_id"] = corporate_action_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        logical_as_of = self.announced_at or self.effective_at or datetime(1970, 1, 1, tzinfo=UTC)
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.CORPORATE_ACTION,
            logical_as_of=logical_as_of,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class DividendEntitlement(_Phase16FFrozenModel):
    """Immutable dividend entitlement derived from a corporate action."""

    entitlement_id: str = Field(min_length=64, max_length=64)
    corporate_action_id: str = Field(min_length=64, max_length=64)
    snapshot_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    entitled_quantity: int = Field(gt=0)
    dividend_per_share: float = Field(gt=0, allow_inf_nan=False)
    gross_cash_amount: float = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)
    ex_date: UtcDatetime
    payment_at: UtcDatetime
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    producer_version: str = Field(min_length=1)

    @field_validator("replay_run_id", mode="before")
    @classmethod
    def coerce_replay_run_id(cls, value: object) -> UUID:
        if isinstance(value, str):
            return UUID(value)
        if isinstance(value, UUID):
            return value
        raise TypeError("replay_run_id must be a UUID or UUID string")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @field_validator("ex_date", "payment_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        return _normalize_cost_timestamp(value)

    @model_validator(mode="after")
    def validate_amount_math(self) -> DividendEntitlement:
        if round(self.entitled_quantity * self.dividend_per_share, 10) != self.gross_cash_amount:
            raise ValueError("gross_cash_amount must equal entitled_quantity * dividend_per_share")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"entitlement_id"})

    def _canonical_entitlement_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create(cls, **values: object) -> DividendEntitlement:
        material: dict[str, object] = dict(values)
        material.setdefault("schema_version", PHASE16F_SCHEMA_VERSION)
        material.setdefault("research_only", True)
        material.setdefault("paper_trading_only", True)
        material.setdefault("suitable_for_live_trading", False)
        material.pop("entitlement_id", None)
        provisional = cls.model_validate({"entitlement_id": "0" * 64, **material})
        entitlement_id = provisional._canonical_entitlement_id()
        payload = provisional.model_dump(mode="json")
        payload["entitlement_id"] = entitlement_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.DIVIDEND_ENTITLEMENT,
            logical_as_of=self.ex_date,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PortfolioAdjustmentType(StrEnum):
    EXECUTION_COST = "execution_cost"
    CORPORATE_ACTION_SPLIT = "corporate_action_split"
    DIVIDEND_PAYMENT = "dividend_payment"


class PortfolioAdjustmentLedgerEntry(_Phase16FFrozenModel):
    """Immutable portfolio adjustment applied after a base Phase 16E snapshot."""

    adjustment_id: str = Field(min_length=64, max_length=64)
    portfolio_snapshot_id: str = Field(min_length=64, max_length=64)
    replay_specification_id: str = Field(min_length=64, max_length=64)
    replay_run_id: UUID
    simulated_at: UtcDatetime
    event_type: str = Field(min_length=1)
    prior_portfolio_snapshot_id: str | None = Field(default=None, min_length=64, max_length=64)
    paper_execution_result_id: str | None = Field(default=None, min_length=64, max_length=64)
    assessment_id: str | None = Field(default=None, min_length=64, max_length=64)
    corporate_action_id: str | None = Field(default=None, min_length=64, max_length=64)
    entitlement_id: str | None = Field(default=None, min_length=64, max_length=64)
    sequence: int = Field(ge=0)
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

    @field_validator("simulated_at", mode="before")
    @classmethod
    def normalize_simulated_at(cls, value: object) -> datetime:
        return _normalize_cost_timestamp(value)

    @field_validator("notes", mode="before")
    @classmethod
    def coerce_notes(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("notes must be a list or tuple")

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        allowed = {item.value for item in PortfolioAdjustmentType}
        if value not in allowed:
            raise ValueError(f"event_type must be one of {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def validate_ledger_consistency(self) -> PortfolioAdjustmentLedgerEntry:
        if self.event_type == PortfolioAdjustmentType.EXECUTION_COST.value and self.assessment_id is None:
            raise ValueError("execution_cost ledger entries require assessment_id")
        if self.event_type == PortfolioAdjustmentType.CORPORATE_ACTION_SPLIT.value and self.corporate_action_id is None:
            raise ValueError("corporate_action_split entries require corporate_action_id")
        if self.event_type == PortfolioAdjustmentType.DIVIDEND_PAYMENT.value and self.entitlement_id is None:
            raise ValueError("dividend_payment entries require entitlement_id")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self._build_identity_payload(self.model_dump(mode="python"), {"adjustment_id"})

    def _canonical_adjustment_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @property
    def canonical_hash(self) -> str:
        return sha256_fingerprint(self._build_identity_payload(self.model_dump(mode="python"), set()))

    @classmethod
    def create_execution_cost(
        cls,
        *,
        snapshot: PortfolioSnapshot,
        assessment: ExecutionCostAssessment,
        prior_snapshot_id: str,
        sequence: int,
        producer_version: str = "phase16f-1.0",
    ) -> PortfolioAdjustmentLedgerEntry:
        provisional = cls(
            adjustment_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=snapshot.simulated_at,
            event_type=PortfolioAdjustmentType.EXECUTION_COST.value,
            prior_portfolio_snapshot_id=prior_snapshot_id,
            paper_execution_result_id=assessment.paper_order_id,
            assessment_id=assessment.assessment_id,
            sequence=sequence,
            producer_version=producer_version,
        )
        adjustment_id = provisional._canonical_adjustment_id()
        payload = provisional.model_dump(mode="json")
        payload["adjustment_id"] = adjustment_id
        return cls.model_validate(payload)

    @classmethod
    def create_corporate_action_split(
        cls,
        *,
        snapshot: PortfolioSnapshot,
        corporate_action: CorporateActionEvent,
        prior_snapshot_id: str,
        sequence: int,
        producer_version: str = "phase16f-1.0",
    ) -> PortfolioAdjustmentLedgerEntry:
        provisional = cls(
            adjustment_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=corporate_action.effective_at or snapshot.simulated_at,
            event_type=PortfolioAdjustmentType.CORPORATE_ACTION_SPLIT.value,
            prior_portfolio_snapshot_id=prior_snapshot_id,
            corporate_action_id=corporate_action.corporate_action_id,
            sequence=sequence,
            producer_version=producer_version,
        )
        adjustment_id = provisional._canonical_adjustment_id()
        payload = provisional.model_dump(mode="json")
        payload["adjustment_id"] = adjustment_id
        return cls.model_validate(payload)

    @classmethod
    def create_dividend_payment(
        cls,
        *,
        snapshot: PortfolioSnapshot,
        entitlement: DividendEntitlement,
        prior_snapshot_id: str,
        sequence: int,
        producer_version: str = "phase16f-1.0",
    ) -> PortfolioAdjustmentLedgerEntry:
        provisional = cls(
            adjustment_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=entitlement.payment_at,
            event_type=PortfolioAdjustmentType.DIVIDEND_PAYMENT.value,
            prior_portfolio_snapshot_id=prior_snapshot_id,
            corporate_action_id=entitlement.corporate_action_id,
            entitlement_id=entitlement.entitlement_id,
            sequence=sequence,
            producer_version=producer_version,
        )
        adjustment_id = provisional._canonical_adjustment_id()
        payload = provisional.model_dump(mode="json")
        payload["adjustment_id"] = adjustment_id
        return cls.model_validate(payload)

    def envelope(self, *, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.PORTFOLIO_LEDGER_ENTRY,
            logical_as_of=self.simulated_at,
            producer_version=self.producer_version,
            provenance_references=provenance_references,
        )


class PortfolioAdjustmentApplicationResult(BaseModel):
    snapshot: PortfolioSnapshot
    ledger_entry: PortfolioAdjustmentLedgerEntry
    applied: bool
    failure_code: str | None = None
    failure_message: str | None = None

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


__all__ = [
    "PHASE16F_SCHEMA_VERSION",
    "CorporateActionEvent",
    "CorporateActionStatus",
    "CorporateActionType",
    "DividendEntitlement",
    "ExecutionCostAssessment",
    "PortfolioAdjustmentApplicationResult",
    "PortfolioAdjustmentLedgerEntry",
    "PortfolioAdjustmentType",
    "TransactionCostMethodology",
]

from app.services.portfolio_accounting.models import PortfolioSnapshot

PortfolioAdjustmentApplicationResult.model_rebuild()
