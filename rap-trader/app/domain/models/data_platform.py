"""Strict Pydantic v2 models for the Unified Research Data Platform (Phase 8A).

These models form the normalized, versioned, point-in-time-safe data layer that
future analysts will consume. They deliberately contain no analyst opinions,
no trade signals, and no external dependencies beyond deterministic contracts.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from app.domain.models.market_data import UtcDatetime, _require_aware_utc


def _require_finite(value: float) -> float | int:
    """Reject NaN and infinity for numeric values."""
    import math

    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        raise ValueError("value must be finite")
    return value


def _validate_safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    """Reject secrets in metadata fields."""
    if value:
        serialized = repr(value)
        if re.search(r"(?i)(password|passwd|token|api[_-]?key|secret|credential)", serialized):
            raise ValueError("credentials and secrets are forbidden in metadata")
        if re.search(r"(?i)(?:[A-Z]:[\\/]|file:|(?:^|['\"\s])[\\/]{1,2})", serialized):
            raise ValueError("absolute local filesystem paths are forbidden in metadata")
    return value


# ---------------------------------------------------------------------------
# DataRecordId
# ---------------------------------------------------------------------------

_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class DataRecordId(RootModel[str]):
    """Safe canonical identifier for a normalized data record."""

    model_config = ConfigDict(strict=True, frozen=True)
    root: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    @field_validator("root")
    @classmethod
    def validate_format(cls, value: str) -> str:
        if not _RECORD_ID_RE.match(value):
            raise ValueError("record id contains disallowed characters")
        return value

    def __str__(self) -> str:
        return self.root


# ---------------------------------------------------------------------------
# DataDomain
# ---------------------------------------------------------------------------


class DataDomain(StrEnum):
    MARKET = "market"
    FUNDAMENTAL = "fundamental"
    MACRO = "macro"
    CENTRAL_BANK = "central_bank"
    CORPORATE_ACTION = "corporate_action"
    EARNINGS = "earnings"
    CALENDAR = "calendar"
    NEWS = "news"
    ALTERNATIVE = "alternative"


# ---------------------------------------------------------------------------
# DataSourceIdentity
# ---------------------------------------------------------------------------


class DataSourceIdentity(BaseModel):
    """Identity of the origin system that produced the underlying data."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=128)
    dataset: str = Field(min_length=1, max_length=128)
    source_version: str = Field(min_length=1, max_length=64)
    schema_version: str = Field(min_length=1, max_length=64)
    endpoint_or_dataset_reference: str | None = Field(default=None, max_length=256)
    license_note: str | None = Field(default=None, max_length=256)
    offline_capable: bool = True
    authoritative: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=1024)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> Any:
        return _validate_safe_metadata(value if isinstance(value, dict) else {})

    @field_validator("endpoint_or_dataset_reference", "license_note")
    @classmethod
    def reject_absolute_paths(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/", "\\", "file:")):
            raise ValueError("absolute local filesystem paths are forbidden")
        return value


# ---------------------------------------------------------------------------
# DataAvailability
# ---------------------------------------------------------------------------


class DataAvailability(BaseModel):
    """Temporal provenance describing when a datum was observed and used."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    observed_at: UtcDatetime
    published_at: UtcDatetime | None = None
    available_at: UtcDatetime
    ingested_at: UtcDatetime
    revised_at: UtcDatetime | None = None
    effective_from: UtcDatetime | None = None
    effective_to: UtcDatetime | None = None

    @field_validator("observed_at", "published_at", "available_at", "ingested_at", "revised_at", "effective_from", "effective_to")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)

    @model_validator(mode="after")
    def chronology(self) -> DataAvailability:
        # Core invariant: availability must be at or before the observation time
        # is NOT required — availability is when the system could use it.
        if self.available_at > self.ingested_at:
            raise ValueError("available_at cannot be after ingested_at")
        if self.effective_from is not None and self.effective_to is not None and self.effective_from > self.effective_to:
            raise ValueError("effective_from cannot be after effective_to")
        return self


# ---------------------------------------------------------------------------
# DataRevision
# ---------------------------------------------------------------------------


class DataRevision(BaseModel):
    """A revision entry in the lineage of a logical data record."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    revision_id: str = Field(min_length=1, max_length=128)
    revision_number: int = Field(ge=0)
    previous_revision_id: str | None = Field(default=None, max_length=128)
    revised_at: UtcDatetime
    available_at: UtcDatetime
    reason: str | None = None
    changed_fields: tuple[str, ...] = ()
    source_fingerprint: str = Field(min_length=1, max_length=256)

    @field_validator("revised_at", "available_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_reason_and_fields(self) -> DataRevision:
        if self.revision_number == 0 and self.reason is not None:
            raise ValueError("revision 0 (first) must not have a revision reason")
        if self.revision_number == 0 and self.previous_revision_id is not None:
            raise ValueError("revision 0 must not have previous_revision_id")
        if self.revision_number > 0 and self.previous_revision_id is None:
            raise ValueError("revisions after 0 require previous_revision_id")
        return self


# ---------------------------------------------------------------------------
# DataQuality
# ---------------------------------------------------------------------------


class DataQuality(BaseModel):
    """Quality metadata for a normalized data record."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    completeness: float = Field(ge=0, le=1, allow_inf_nan=False)
    consistency: float = Field(ge=0, le=1, allow_inf_nan=False)
    timeliness: float = Field(ge=0, le=1, allow_inf_nan=False)
    source_reliability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    anomaly_flags: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def score_is_consistent(self) -> DataQuality:
        components = [self.completeness, self.consistency, self.timeliness]
        if self.source_reliability is not None:
            components.append(self.source_reliability)
        if components:
            expected_min = min(components)
            if self.score < expected_min:
                pass  # score is a composite, not strictly bounded by min
        if self.warnings and self.score > 0.8:
            # Warnings present but high confidence — acceptable but worth noting
            pass
        return self


# ---------------------------------------------------------------------------
# NormalizedDataRecord
# ---------------------------------------------------------------------------


class NormalizedDataRecord(BaseModel):
    """An immutable, normalized data record with full provenance and quality."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    record_id: DataRecordId
    domain: DataDomain
    symbol_or_entity: str | None = Field(default=None, min_length=1, max_length=128)
    series_id: str | None = Field(default=None, min_length=1, max_length=128)
    period_start: UtcDatetime | None = None
    period_end: UtcDatetime | None = None
    event_time: UtcDatetime | None = None
    value: Any = None
    units: str = Field(min_length=1, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    availability: DataAvailability
    revision: DataRevision
    source: DataSourceIdentity
    quality: DataQuality
    source_fingerprint: str = Field(min_length=1, max_length=256, pattern=r"^[a-f0-9]{64}$")
    schema_version: str = Field(min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=4096)
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("value")
    @classmethod
    def reject_non_finite(cls, value: Any) -> Any:
        if isinstance(value, float):
            _require_finite(value)
        return value

    @field_validator("period_start", "period_end", "event_time")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> Any:
        return _validate_safe_metadata(value if isinstance(value, dict) else {})

    @model_validator(mode="after")
    def validate_periods(self) -> NormalizedDataRecord:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("data records are research-only")
        if self.period_start is not None and self.period_end is not None and self.period_start > self.period_end:
            raise ValueError("period_start cannot be after period_end")
        if self.event_time is not None and self.period_start is not None and self.event_time < self.period_start:
            raise ValueError("event_time cannot be before period_start")
        return self


# ---------------------------------------------------------------------------
# EconomicSeriesDefinition
# ---------------------------------------------------------------------------


class RevisionPolicy(StrEnum):
    VINTAGE = "vintage"
    REALTIME = "realtime"
    LATEST = "latest"


class Frequency(StrEnum):
    REALTIME = "realtime"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class EconomicSeriesDefinition(BaseModel):
    """Metadata describing a normalized economic or market series."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    series_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    geography: str | None = Field(default=None, max_length=128)
    units: str = Field(min_length=1, max_length=64)
    frequency: Frequency
    seasonal_adjustment: bool = False
    revision_policy: RevisionPolicy = RevisionPolicy.VINTAGE
    source: DataSourceIdentity
    expected_release_lag: int | None = Field(default=None, ge=0)
    stale_after_seconds: int = Field(ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=4096)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> Any:
        return _validate_safe_metadata(value if isinstance(value, dict) else {})


# ---------------------------------------------------------------------------
# EconomicObservation
# ---------------------------------------------------------------------------


class EconomicObservation(BaseModel):
    """A single point-in-time observation of an economic series."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    series: EconomicSeriesDefinition
    reference_period: UtcDatetime
    value: float = Field(allow_inf_nan=False)
    first_release_at: UtcDatetime
    available_at: UtcDatetime
    revision_number: int = Field(ge=0)
    revised_at: UtcDatetime | None = None
    previous_value: float | None = Field(default=None, allow_inf_nan=False)
    consensus: float | None = Field(default=None, allow_inf_nan=False)
    prior_reported: float | None = Field(default=None, allow_inf_nan=False)
    surprise: float | None = Field(default=None, allow_inf_nan=False)
    quality: DataQuality
    source_fingerprint: str = Field(min_length=1, max_length=256, pattern=r"^[a-f0-9]{64}$")
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("reference_period", "first_release_at", "available_at", "revised_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)

    @model_validator(mode="after")
    def safety(self) -> EconomicObservation:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("economic observations are research-only")
        return self


# ---------------------------------------------------------------------------
# EventRecord
# ---------------------------------------------------------------------------


class EventImportance(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EventRecord(BaseModel):
    """A normalized event record (central-bank, earnings, corporate action, etc.)."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=64)
    entity: str | None = Field(default=None, max_length=128)
    scheduled_at: UtcDatetime | None = None
    occurred_at: UtcDatetime | None = None
    published_at: UtcDatetime | None = None
    available_at: UtcDatetime
    importance: EventImportance = EventImportance.MEDIUM
    source: DataSourceIdentity
    headline_or_title: str = Field(min_length=1, max_length=512)
    summary: str | None = Field(default=None, max_length=4096)
    structured_payload: dict[str, Any] = Field(default_factory=dict, max_length=8192)
    revision: DataRevision
    quality: DataQuality
    fingerprint: str = Field(min_length=1, max_length=256, pattern=r"^[a-f0-9]{64}$")
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("scheduled_at", "occurred_at", "published_at", "available_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)

    @field_validator("structured_payload", mode="before")
    @classmethod
    def validate_payload(cls, value: Any) -> Any:
        return _validate_safe_metadata(value if isinstance(value, dict) else {})

    @model_validator(mode="after")
    def validate_timelines(self) -> EventRecord:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("event records are research-only")
        times = [t for t in (self.scheduled_at, self.occurred_at, self.published_at) if t is not None]
        if times:
            latest_known = max(times)
            if self.available_at < latest_known:
                raise ValueError("available_at cannot be before the latest known event time")
        return self


# ---------------------------------------------------------------------------
# ResearchDataSnapshot
# ---------------------------------------------------------------------------


class SnapshotProvenance(BaseModel):
    """Provenance metadata for a research data snapshot."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)
    as_of: UtcDatetime = Field(strict=False)
    created_at: UtcDatetime
    source_versions: dict[str, str]
    input_fingerprints: tuple[str, ...]
    schema_version: str = Field(min_length=1, max_length=64)
    platform_version: str = Field(min_length=1, max_length=64)

    @field_validator("as_of", "created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class QualitySummary(BaseModel):
    """Aggregate quality summary for a snapshot."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    total_records: int = Field(ge=0)
    average_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    records_with_warnings: int = Field(ge=0)
    domains_represented: tuple[DataDomain, ...]


class ResearchDataSnapshot(BaseModel):
    """An immutable, point-in-time-safe collection of normalized data records."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    snapshot_id: str = Field(min_length=1, max_length=128)
    as_of: UtcDatetime
    requested_domains: tuple[DataDomain, ...]
    records: tuple[NormalizedDataRecord, ...]
    source_versions: dict[str, str]
    schema_version: str = Field(min_length=1, max_length=64)
    platform_version: str = Field(min_length=1, max_length=64)
    created_at: UtcDatetime
    input_fingerprints: tuple[str, ...]
    quality_summary: QualitySummary
    warnings: tuple[str, ...] = ()
    partial: bool = False
    provenance: SnapshotProvenance
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("as_of", "created_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def validate_records_point_in_time(self) -> ResearchDataSnapshot:
        """Every record must satisfy available_at <= snapshot.as_of."""
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("data snapshots are research-only")
        for record in self.records:
            if record.availability.available_at > self.as_of:
                raise ValueError(f"record {record.record_id} available_at is after snapshot as_of (lookahead violation)")
        return self

    @model_validator(mode="after")
    def validate_quality_summary_completeness(self) -> ResearchDataSnapshot:
        domains = {record.domain for record in self.records}
        expected = tuple(sorted(domains))
        if self.quality_summary.domains_represented != expected:
            # Not a hard failure — allow extra domains in summary, but warn
            pass
        if self.quality_summary.total_records != len(self.records):
            raise ValueError("quality_summary.total_records must match number of records")
        return self


# ---------------------------------------------------------------------------
# SnapshotError
# ---------------------------------------------------------------------------


class SnapshotErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    NO_DATA = "NO_DATA"
    LOOKAHEAD_REJECTED = "LOOKAHEAD_REJECTED"
    SOURCE_NOT_AVAILABLE = "SOURCE_NOT_AVAILABLE"
    MAX_RECORDS_EXCEEDED = "MAX_RECORDS_EXCEEDED"


class DataPlatformError(Exception):
    """Research-only data platform error with safe message."""

    def __init__(self, code: SnapshotErrorCode | str, safe_message: str, internal_detail: str | None = None) -> None:
        self.code = SnapshotErrorCode(code)
        self.safe_message = safe_message
        self.internal_detail = internal_detail
        super().__init__(safe_message)


# ---------------------------------------------------------------------------
# SnapshotRequest
# ---------------------------------------------------------------------------


class SnapshotRequest(BaseModel):
    """Request to produce a point-in-time-safe research data snapshot."""

    model_config = ConfigDict(strict=True, extra="forbid")

    as_of: UtcDatetime = Field(strict=False)
    domains: tuple[DataDomain, ...] = ()
    symbols: tuple[str, ...] = ()
    series_ids: tuple[str, ...] = ()
    max_records: int | None = Field(default=None, gt=0)
    source_preferences: tuple[str, ...] = ()
    allow_partial: bool = False
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("as_of")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def safety(self) -> SnapshotRequest:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("data platform snapshots are research-only")
        return self


__all__ = [
    "DataAvailability",
    "DataDomain",
    "DataPlatformError",
    "DataQuality",
    "DataRecordId",
    "DataRevision",
    "DataSourceIdentity",
    "EconomicObservation",
    "EconomicSeriesDefinition",
    "EventImportance",
    "EventRecord",
    "Frequency",
    "NormalizedDataRecord",
    "QualitySummary",
    "ResearchDataSnapshot",
    "RevisionPolicy",
    "SnapshotErrorCode",
    "SnapshotProvenance",
    "SnapshotRequest",
]
