"""Immutable attribution contracts for Phase 15G."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.market_data import Symbol, UtcDatetime

ATTRIBUTION_SCHEMA_VERSION: Literal["1.0"] = "1.0"

COMPONENT_OUTPUT_TYPES = {
    "technical": ArtifactType.ANALYST_OPINION,
    "fundamental": ArtifactType.ANALYST_OPINION,
    "macro": ArtifactType.MACRO_OPINION,
    "news": ArtifactType.NEWS_OPINION,
    "kronos": ArtifactType.KRONOS_PREDICTION,
    "fusion": ArtifactType.ANALYST_OPINION,
    "portfolio": ArtifactType.PORTFOLIO_PROPOSAL,
    "risk": ArtifactType.RISK_DECISION,
    "investment_committee": ArtifactType.INVESTMENT_COMMITTEE_DECISION,
    "chairman": ArtifactType.CHAIRMAN_DECISION,
}


class ComponentKind(StrEnum):
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    MACRO = "macro"
    NEWS = "news"
    KRONOS = "kronos"
    FUSION = "fusion"
    PORTFOLIO = "portfolio"
    RISK = "risk"
    INVESTMENT_COMMITTEE = "investment_committee"
    CHAIRMAN = "chairman"
    UNKNOWN = "unknown"


class OutcomeAlignment(StrEnum):
    ALIGNED = "aligned"
    MISALIGNED = "misaligned"
    NEUTRAL = "neutral"


class GovernanceInterventionKind(StrEnum):
    REDUCED_WEIGHT = "reduced_weight"
    REJECTED = "rejected"
    REQUIRED_MODIFICATION = "required_modification"
    APPROVED = "approved"
    UNKNOWN = "unknown"


class ComponentAttribution(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    component: ComponentKind
    component_artifact_id: str = Field(min_length=64, max_length=64)
    component_name: str = Field(min_length=1)
    historical_signal: str = Field(min_length=1)
    historical_confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    historical_weight: float | None = Field(default=None, allow_inf_nan=False)
    weight_available: bool = False
    outcome_alignment: OutcomeAlignment
    signed_outcome_metric: float | None = Field(default=None, allow_inf_nan=False)
    methodology: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("component", "outcome_alignment", mode="before")
    @classmethod
    def coerce_enums(cls, value: object) -> object:
        if isinstance(value, str):
            if value in ComponentKind._value2member_map_:
                return ComponentKind(value)
            if value in OutcomeAlignment._value2member_map_:
                return OutcomeAlignment(value)
        return value

    @model_validator(mode="after")
    def validate_weight_availability(self) -> ComponentAttribution:
        if self.historical_weight is None:
            expected = False
        else:
            expected = True
        if self.weight_available != expected:
            raise ValueError("weight_available must reflect presence of historical_weight")
        return self


class GovernanceAttribution(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    component: ComponentKind = ComponentKind.RISK
    pre_governance_signal: str = Field(min_length=1)
    post_governance_signal: str = Field(min_length=1)
    intervention: GovernanceInterventionKind
    asset_outcome_direction: str = Field(min_length=1)
    pre_governance_persisted: bool = False
    post_governance_persisted: bool = False
    assertion: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @field_validator("component", mode="before")
    @classmethod
    def coerce_component(cls, value: object) -> object:
        if isinstance(value, str):
            return ComponentKind(value)
        return value

    @field_validator("intervention", mode="before")
    @classmethod
    def coerce_intervention(cls, value: object) -> object:
        if isinstance(value, str):
            return GovernanceInterventionKind(value)
        return value

    @property
    def valid_comparison(self) -> bool:
        return self.pre_governance_persisted and self.post_governance_persisted


class AlignmentSummary(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    component: ComponentKind
    sample_count: int = Field(ge=0)
    alignment_count: int = Field(ge=0)
    alignment_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    average_signed_return: float | None = Field(default=None, allow_inf_nan=False)
    confidence_calibration: float | None = Field(default=None, allow_inf_nan=False)


class AttributionRecord(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    attribution_id: str = Field(min_length=64, max_length=64)
    attribution_schema_version: Literal["1.0"] = ATTRIBUTION_SCHEMA_VERSION
    decision_artifact_id: str = Field(min_length=64, max_length=64)
    decision_run_manifest_id: str = Field(min_length=64, max_length=64)
    decision_journal_entry_id: str = Field(min_length=64, max_length=64)
    outcome_evaluation_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol | str
    decision_at: UtcDatetime
    horizon: int = Field(gt=0)
    period: str = Field(min_length=1)
    direction: Literal["BUY", "SELL", "WAIT"]
    components: tuple[ComponentAttribution, ...] = Field(min_length=1)
    governance: GovernanceAttribution | None = None
    signed_outcome_metric: float | None = Field(default=None, allow_inf_nan=False)
    outcome_alignment: OutcomeAlignment
    producer_version: str = Field(min_length=1)
    methodology: str = Field(min_length=1)

    @field_validator("decision_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            normalized = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            normalized = value
        else:
            raise TypeError("decision_at must be an ISO datetime string or datetime instance")
        if normalized.tzinfo is None or normalized.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return normalized.astimezone(UTC)

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> Symbol:
        if isinstance(value, str):
            return Symbol(value)
        if isinstance(value, Symbol):
            return value
        raise TypeError("symbol must be a symbol string or Symbol instance")

    @field_validator("components", mode="before")
    @classmethod
    def coerce_components(cls, value: object) -> tuple[Any, ...]:
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, tuple):
            return value
        raise TypeError("components must be a list or tuple")

    @field_validator("outcome_alignment", mode="before")
    @classmethod
    def coerce_outcome_alignment(cls, value: object) -> object:
        if isinstance(value, str) and value in OutcomeAlignment._value2member_map_:
            return OutcomeAlignment(value)
        return value

    @field_validator("governance", mode="before")
    @classmethod
    def coerce_governance(cls, value: object) -> object:
        if isinstance(value, dict):
            return GovernanceAttribution(**value)
        return value

    @model_validator(mode="after")
    def validate_governance(self) -> AttributionRecord:
        if self.governance is not None and not self.governance.valid_comparison:
            raise ValueError("governance attribution requires valid persisted comparison")
        return self

    def fingerprint(self) -> str:
        material = self.model_dump(mode="json")
        return sha256_fingerprint(material)

    def envelope(self) -> ArtifactEnvelope:
        payload = self.model_dump(mode="json", exclude_none=False)
        return ArtifactEnvelope.create(
            payload=payload,
            artifact_type=ArtifactType.ATTRIBUTION_RECORD,
            logical_as_of=self.decision_at,
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_artifact_id,
                    description="attribution target decision artifact",
                    producer="rap-trader-attribution",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_journal_entry_id,
                    description="attribution target decision journal entry",
                    producer="rap-trader-attribution",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.outcome_evaluation_id,
                    description="attribution target outcome evaluation",
                    producer="rap-trader-attribution",
                    producer_version="1.0",
                ),
            ),
        )


__all__ = [
    "ATTRIBUTION_SCHEMA_VERSION",
    "AlignmentSummary",
    "AttributionRecord",
    "ComponentAttribution",
    "ComponentKind",
    "GovernanceAttribution",
    "GovernanceInterventionKind",
    "OutcomeAlignment",
]
