"""Strict, immutable models for reusable market-intelligence features."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from app.domain.models.market_data import AdjustmentPolicy, SessionPolicy, Timeframe, UtcDatetime, _require_aware_utc


class _FeatureModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class FeatureId(RootModel[str]):
    model_config = ConfigDict(strict=True, frozen=True)
    root: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")

    def __str__(self) -> str:
        return self.root


class FeatureCategory(StrEnum):
    PRICE = "price"
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    STRUCTURE = "structure"
    SUPPORT_RESISTANCE = "support_resistance"
    KRONOS = "kronos"
    BACKTEST = "backtest"


FeatureScalar = Annotated[float | int | str | bool | None, Field(union_mode="left_to_right")]


class FeatureDependency(_FeatureModel):
    feature_id: FeatureId
    required: bool = True


class FeatureMetadata(_FeatureModel):
    feature_id: FeatureId
    category: FeatureCategory
    display_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    generator: str = Field(min_length=1)
    dependencies: tuple[FeatureDependency, ...] = ()
    unit: str | None = None
    schema_version: str = Field(min_length=1, default="1.0.0")
    platform_version: str = Field(min_length=1, default="mifp-6.5.0")


class FeatureValue(_FeatureModel):
    feature_id: FeatureId
    value: FeatureScalar
    observed_at: UtcDatetime
    available_at: UtcDatetime
    generated_at: UtcDatetime
    source_fingerprint: str = Field(min_length=1, max_length=128)
    category: FeatureCategory
    version: str = Field(min_length=1)

    @field_validator("observed_at", "available_at", "generated_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: FeatureScalar) -> FeatureScalar:
        if isinstance(value, float) and not isfinite(value):
            raise ValueError("feature values must be finite")
        return value

    @model_validator(mode="after")
    def availability_chronology(self) -> FeatureValue:
        if self.available_at > self.generated_at:
            raise ValueError("available_at cannot be after generated_at")
        if self.available_at > self.observed_at:
            raise ValueError("available_at cannot be after observed_at")
        return self


class FeatureVector(_FeatureModel):
    values: tuple[FeatureValue, ...]

    @model_validator(mode="after")
    def unique_and_sorted(self) -> FeatureVector:
        ids = [str(item.feature_id) for item in self.values]
        if len(ids) != len(set(ids)):
            raise ValueError("feature vector contains duplicate feature identifiers")
        if ids != sorted(ids):
            raise ValueError("feature vector must be sorted by feature identifier")
        return self

    def get(self, feature_id: FeatureId | str) -> FeatureValue | None:
        target = str(feature_id)
        return next((item for item in self.values if str(item.feature_id) == target), None)


class FeatureProvenance(_FeatureModel):
    source_data: str = Field(min_length=1)
    generator_version: str = Field(min_length=1)
    feature_schema_version: str = Field(min_length=1)
    platform_version: str = Field(min_length=1)
    feature_versions: tuple[tuple[str, str], ...]
    source_retrieved_at: UtcDatetime
    generated_at: UtcDatetime
    dependency_graph: tuple[tuple[str, tuple[str, ...]], ...]
    input_fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @field_validator("source_retrieved_at", "generated_at")
    @classmethod
    def aware_timestamps(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class FeatureSnapshot(_FeatureModel):
    snapshot_id: str = Field(min_length=1)
    ticker: str = Field(min_length=1, max_length=10, pattern=r"^[A-Z0-9]+(?:\.[A-Z0-9]+)*$")
    timeframe: Timeframe
    provider: str = Field(min_length=1)
    adjustment: AdjustmentPolicy
    session: SessionPolicy
    as_of: UtcDatetime
    generated_at: UtcDatetime
    bars_analyzed: int = Field(ge=0)
    vector: FeatureVector
    provenance: FeatureProvenance
    stale: bool
    age_seconds: float = Field(ge=0, allow_inf_nan=False)

    @field_validator("as_of", "generated_at")
    @classmethod
    def aware_timestamps(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def no_lookahead_features(self) -> FeatureSnapshot:
        """Reject any feature that was unavailable at snapshot.as_of."""
        for value in self.vector.values:
            if value.available_at > self.as_of:
                raise ValueError(f"feature '{value.feature_id}' is unavailable at snapshot as_of")
        return self


class FeatureStoreStatistics(_FeatureModel):
    registered_features: int = Field(ge=0)
    cached_snapshots: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    cache_misses: int = Field(ge=0)
    computations: int = Field(ge=0)


class FeatureStoreHealth(_FeatureModel):
    status: Literal["healthy", "degraded"]
    checked_at: UtcDatetime
    statistics: FeatureStoreStatistics
    detail: str

    @field_validator("checked_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class FeatureErrorCode(StrEnum):
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_FEATURE = "UNKNOWN_FEATURE"
    DUPLICATE_FEATURE = "DUPLICATE_FEATURE"
    INVALID_DEPENDENCY = "INVALID_DEPENDENCY"
    DEPENDENCY_CYCLE = "DEPENDENCY_CYCLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    COMPUTATION_FAILED = "COMPUTATION_FAILED"


class FeatureError(Exception):
    def __init__(self, code: FeatureErrorCode | str, safe_message: str, internal_detail: str | None = None) -> None:
        self.code = FeatureErrorCode(code)
        self.safe_message = safe_message
        self.internal_detail = internal_detail
        super().__init__(safe_message)


class FeatureSnapshotRequest(_FeatureModel):
    ticker: str = Field(min_length=1, max_length=10, pattern=r"^[A-Z0-9]+(?:\.[A-Z0-9]+)*$")
    timeframe: Timeframe = "1d"
    as_of: UtcDatetime
    lookback: int = Field(default=100, ge=52, le=10_000)
    adjustment: AdjustmentPolicy = "raw"
    session: SessionPolicy = "regular"
    feature_ids: tuple[FeatureId, ...] | None = None
    configuration: tuple[tuple[str, str], ...] = ()

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: Any) -> Any:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("as_of")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def parse_api_timestamp(cls, value: Any) -> Any:
        return datetime.fromisoformat(value) if isinstance(value, str) else value

    @field_validator("feature_ids", mode="before")
    @classmethod
    def parse_feature_ids(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("configuration", mode="before")
    @classmethod
    def parse_configuration(cls, value: Any) -> Any:
        return tuple(tuple(item) for item in value) if isinstance(value, list) else value
