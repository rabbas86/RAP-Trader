"""Strict, research-only contracts shared by all analysts."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePath, PureWindowsPath
from typing import Annotated, Any, cast

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.market_data import Timeframe, UtcDatetime, _require_aware_utc


def _analyst_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ValueError("invalid analyst identifier")
    return value


AnalystId = Annotated[str, Field(min_length=1, max_length=128), AfterValidator(_analyst_id)]


class AnalystRole(StrEnum):
    PRICE_FORECAST = "PRICE_FORECAST"
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    MACRO = "MACRO"
    NEWS = "NEWS"
    VALUATION = "VALUATION"
    PORTFOLIO = "PORTFOLIO"
    RISK = "RISK"
    META = "META"
    OTHER = "OTHER"


class AnalysisDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    MIXED = "MIXED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceType(StrEnum):
    MARKET_DATA = "market_data"
    FORECAST = "forecast"
    BACKTEST = "backtest"
    TECHNICAL_INDICATOR = "technical_indicator"
    FINANCIAL_STATEMENT = "financial_statement"
    VALUATION = "valuation"
    MACROECONOMIC = "macroeconomic"
    CENTRAL_BANK = "central_bank"
    NEWS = "news"
    REGULATORY_FILING = "regulatory_filing"
    SENTIMENT = "sentiment"
    RISK = "risk"
    PORTFOLIO = "portfolio"
    MODEL_OUTPUT = "model_output"
    EXPERT_ASSUMPTION = "expert_assumption"
    OTHER = "other"


class EvidenceStrength(StrEnum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    SPECULATIVE = "SPECULATIVE"


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True)


class Assumption(_StrictModel):
    description: str = Field(min_length=1)


class AnalysisWarning(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class AnalysisLimitation(_StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


_SECRET = re.compile(r"(?i)(password|passwd|token|api[_-]?key|secret|credential)(?:=|:|%3[dD])")


def validate_safe_uri(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        PurePath(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value.startswith(("/", "\\"))
        or re.match(r"^[A-Za-z]:[\\/]", value)
        or value.lower().startswith("file:")
    ):
        raise ValueError("absolute local filesystem paths are forbidden")
    if _SECRET.search(value) or re.search(r"(?i)https?://[^/@:]+:[^/@]+@", value):
        raise ValueError("credentials and secrets are forbidden")
    return value


class ModelIdentity(_StrictModel):
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    training_cut_off: datetime | None = None

    @field_validator("training_cut_off")
    @classmethod
    def timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)


class ProvenanceRecord(_StrictModel):
    source: str = Field(min_length=1)
    retrieved_at: UtcDatetime
    uri: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("uri")
    @classmethod
    def safe_uri(cls, value: str | None) -> str | None:
        return validate_safe_uri(value)


class EvidenceItem(_StrictModel):
    evidence_id: str = Field(min_length=1)
    evidence_type: EvidenceType
    observed_at: UtcDatetime
    available_at: UtcDatetime
    evaluated_at: UtcDatetime
    valid_until: UtcDatetime
    strength: EvidenceStrength
    summary: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    capped: bool = False
    calibration_status: str | None = None
    has_historical_calibration: bool = False
    source_analyst: AnalystId | None = None
    assumptions: list[Assumption] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    limitations: list[AnalysisLimitation] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)

    @field_validator("observed_at", "available_at", "evaluated_at", "valid_until")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def chronology(self) -> EvidenceItem:
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must be later than observed_at")
        if self.available_at > self.evaluated_at:
            raise ValueError("lookahead: available_at is after evaluated_at")
        return self


class ConfidenceScore(_StrictModel):
    value: float = Field(ge=0, le=1, allow_inf_nan=False)
    capped: bool
    calibration_note: str | None = None
    has_historical_calibration: bool


class DataFreshness(_StrictModel):
    observed_at: UtcDatetime
    available_at: UtcDatetime
    evaluated_at: UtcDatetime
    stale_threshold: timedelta
    is_stale: bool
    age_seconds: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("observed_at", "available_at", "evaluated_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class AnalystRequest(_StrictModel):
    analyst_id: AnalystId
    ticker: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9.-]+$")
    timeframe: Timeframe
    as_of: UtcDatetime
    lookback: int = Field(gt=0, le=100_000)
    horizon: int = Field(gt=0, le=10_000)
    asset_class: str = Field(min_length=1)
    extra_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker")
    @classmethod
    def ticker_upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("as_of", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))


class AnalystOpinion(_StrictModel):
    opinion_id: str = Field(min_length=1)
    analyst_id: AnalystId
    analyst_role: AnalystRole
    ticker: str = Field(min_length=1)
    direction: AnalysisDirection
    confidence: ConfidenceScore
    evidence: list[EvidenceItem]
    assumptions: list[Assumption] = Field(default_factory=list)
    warnings: list[AnalysisWarning] = Field(default_factory=list)
    limitations: list[AnalysisLimitation] = Field(default_factory=list)
    generated_at: UtcDatetime
    model_identity: ModelIdentity | None = None
    data_freshness: DataFreshness
    decision_ready: bool = False
    suitable_for_live_trading: bool = False
    research_only: bool = True

    @field_validator("generated_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def invariants(self) -> AnalystOpinion:
        if self.decision_ready or self.suitable_for_live_trading or not self.research_only:
            raise ValueError("analyst opinions are research-only and never decision-ready")
        if not self.evidence and self.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE:
            raise ValueError("at least one evidence item is required")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        if any(item.available_at > self.generated_at for item in self.evidence):
            raise ValueError("lookahead evidence is forbidden")
        if any(item.valid_until < self.generated_at for item in self.evidence):
            raise ValueError("expired evidence is forbidden")
        return self


class AnalystHealth(_StrictModel):
    analyst_id: AnalystId
    configured: bool
    reachable: bool | None
    checked_at: UtcDatetime
    status: str
    detail: str

    @field_validator("checked_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class AnalystMetadata(_StrictModel):
    analyst_id: AnalystId
    display_name: str
    role: AnalystRole
    supported_timeframes: list[str]
    supported_asset_classes: list[str]
    suitable_for_live_trading: bool = False
    research_only: bool = True
    description: str
    model_identity: ModelIdentity | None = None

    @model_validator(mode="after")
    def invariants(self) -> AnalystMetadata:
        if self.suitable_for_live_trading or not self.research_only:
            raise ValueError("analyst metadata must advertise research-only use")
        return self


class AnalystErrorCodes(StrEnum):
    UNSUPPORTED_ANALYST = "UNSUPPORTED_ANALYST"
    INVALID_REQUEST = "INVALID_REQUEST"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FRESHNESS_REJECTED = "FRESHNESS_REJECTED"
    LOOKAHEAD_REJECTED = "LOOKAHEAD_REJECTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FUSION_FAILED = "FUSION_FAILED"


class AnalystError(Exception):
    def __init__(
        self, code: AnalystErrorCodes | str, safe_message: str, retryable: bool = False, internal_detail: str | None = None
    ) -> None:
        self.code = AnalystErrorCodes(code)
        self.safe_message = safe_message
        self.retryable = retryable
        self.internal_detail = internal_detail
        super().__init__(safe_message)


class OpinionSummary(_StrictModel):
    opinion_id: str
    analyst_id: AnalystId
    direction: AnalysisDirection
    confidence: float = Field(ge=0, le=1)
    evidence_count: int = Field(ge=0)
    data_freshness: DataFreshness


class TraceNode(_StrictModel):
    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    uri: str | None = None
    created_at: UtcDatetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("uri")
    @classmethod
    def safe_uri(cls, value: str | None) -> str | None:
        return validate_safe_uri(value)

    @field_validator("metadata")
    @classmethod
    def safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _SECRET.search(str(value)):
            raise ValueError("credentials and secrets are forbidden")
        return value


class TraceEdge(_StrictModel):
    source_node_id: str
    target_node_id: str
    edge_type: str


class AnalysisTrace(_StrictModel):
    trace_id: str
    nodes: list[TraceNode]
    edges: list[TraceEdge]
    created_at: UtcDatetime

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def dag(self) -> AnalysisTrace:
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("trace node IDs must be unique")
        known = set(ids)
        if any(edge.source_node_id not in known or edge.target_node_id not in known for edge in self.edges):
            raise ValueError("trace edge references a missing node")
        graph: dict[str, list[str]] = {node_id: [] for node_id in known}
        for edge in self.edges:
            graph[edge.source_node_id].append(edge.target_node_id)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("analysis trace must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for target in graph[node_id]:
                visit(target)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in known:
            visit(node_id)
        return self


def validate_trace(trace: AnalysisTrace) -> AnalysisTrace:
    return AnalysisTrace.model_validate(trace.model_dump())
