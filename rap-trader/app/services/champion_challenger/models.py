"""Immutable champion/challenger evaluation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.market_data import UtcDatetime

CHAMPION_CHALLENGER_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class _FrozenDict(dict[str, Any]):
    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError("immutable champion/challenger identity")

    def __delitem__(self, key: str) -> None:
        raise TypeError("immutable champion/challenger identity")

    def pop(self, key: str, *args: Any, **kwargs: Any) -> Any:
        raise TypeError("immutable champion/challenger identity")

    def popitem(self) -> tuple[str, Any]:
        raise TypeError("immutable champion/challenger identity")

    def clear(self) -> None:
        raise TypeError("immutable champion/challenger identity")

    def setdefault(self, key: str, default: Any = None) -> Any:
        raise TypeError("immutable champion/challenger identity")

    def update(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("immutable champion/challenger identity")


def _freeze_identity_value(value: Any) -> Any:
    if isinstance(value, dict) and not isinstance(value, _FrozenDict):
        return _FrozenDict({key: _freeze_identity_value(item) for key, value in value.items() for item in [_freeze_identity_value(value)]})
    if isinstance(value, list):
        return tuple(_freeze_identity_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_identity_value(item) for item in value)
    return value


class EvaluationRecommendation(StrEnum):
    """Constrained research-only recommendation from champion/challenger evaluation."""

    KEEP_CHAMPION = "keep_champion"
    PROMOTE_CHALLENGER_FOR_RESEARCH = "promote_challenger_for_research"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    REJECT_CHALLENGER = "reject_challenger"


class ModelIdentity(BaseModel):
    """Canonical model identity when available from persisted artifacts."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    training_cut_off: UtcDatetime | None = None


class ComparisonAssumptions(BaseModel):
    """Explicit assumptions required for fair historical comparison."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    horizon: int = Field(gt=0)
    same_instruments: bool = True
    same_horizon: bool = True
    same_methodology: bool = True
    same_pricing_convention: bool = True
    same_transaction_cost_assumptions: bool = True
    same_sample_eligibility: bool = True
    point_in_time_semantics_preserved: bool = True
    minimum_sample_size: int = Field(ge=0)
    eligibility_rule: str = Field(min_length=1)
    methodology: str = Field(min_length=1)


class EvaluationMetrics(BaseModel):
    """Metrics derived from persisted outcome/attribution data."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    sample_count: int = Field(ge=0)
    alignment_count: int | None = Field(default=None, ge=0)
    alignment_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    average_signed_return: float | None = Field(default=None, allow_inf_nan=False)
    average_raw_return: float | None = Field(default=None, allow_inf_nan=False)
    confidence_calibration: float | None = Field(default=None, allow_inf_nan=False)
    directionally_correct_count: int | None = Field(default=None, ge=0)
    directionally_correct_rate: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_rates(self) -> EvaluationMetrics:
        if self.alignment_rate is not None and self.sample_count and self.alignment_count is None:
            raise ValueError("alignment_rate requires alignment_count when sample_count > 0")
        if self.directionally_correct_rate is not None and self.sample_count and self.directionally_correct_count is None:
            raise ValueError("directionally_correct_rate requires directionally_correct_count when sample_count > 0")
        return self


class ChampionChallengerEvaluation(BaseModel):
    """Immutable canonical champion/challenger comparison artifact."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    evaluation_id: str = Field(min_length=64, max_length=64)
    schema_version: Literal["1.0"] = CHAMPION_CHALLENGER_SCHEMA_VERSION
    evaluation_as_of: datetime | None = Field(default=None)
    champion_identity: dict[str, Any]
    challenger_identity: dict[str, Any]
    evaluation_period: str = Field(min_length=1)
    instruments: tuple[str, ...] = Field(min_length=1)
    horizon: int = Field(gt=0)
    sample_count: int = Field(ge=0)
    champion_metrics: EvaluationMetrics
    challenger_metrics: EvaluationMetrics
    methodology: str = Field(min_length=1)
    comparison_assumptions: ComparisonAssumptions
    recommendation: EvaluationRecommendation
    producer_version: str = Field(min_length=1)

    @field_validator("champion_identity", "challenger_identity", mode="before")
    @classmethod
    def validate_identity(cls, value: object) -> dict[str, Any]:
        if isinstance(value, dict):
            canonical = cls._freeze_identity(value)
        elif isinstance(value, Mapping):
            canonical = cls._freeze_identity(dict(value))
        else:
            raise TypeError("identity must be a dict-like mapping")
        if not canonical:
            raise ValueError("identity must not be empty")
        return canonical

    @classmethod
    def _freeze_identity(cls, value: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("identity keys must be strings")
            result[key] = _freeze_identity_value(item)
        return result

    @field_validator("instruments", mode="before")
    @classmethod
    def coerce_instruments(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            return tuple(value)
        if isinstance(value, tuple):
            return value
        raise TypeError("instruments must be a sequence of strings")

    @field_validator("recommendation", mode="before")
    @classmethod
    def coerce_recommendation(cls, value: object) -> EvaluationRecommendation:
        if isinstance(value, str):
            return EvaluationRecommendation(value)
        if isinstance(value, EvaluationRecommendation):
            return value
        raise TypeError("recommendation must be an EvaluationRecommendation")

    def fingerprint(self) -> str:
        material = self.model_dump(mode="json")
        return sha256_fingerprint(material)

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False, exclude={"evaluation_as_of"}),
            artifact_type=ArtifactType.CHAMPION_CHALLENGER_EVALUATION,
            logical_as_of=self._evaluation_period_as_of(self.evaluation_period),
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.evaluation_id,
                    description="champion/challenger evaluation artifact",
                    producer="rap-trader-champion-challenger",
                    producer_version=self.producer_version,
                ),
            ),
        )

    @staticmethod
    def _evaluation_period_as_of(evaluation_period: str) -> datetime:
        try:
            year, month = evaluation_period.split("-", 1)
            return datetime(int(year), int(month), 1, tzinfo=UTC)
        except Exception as error:
            raise ValueError("evaluation_period must be in YYYY-MM format") from error


__all__ = [
    "CHAMPION_CHALLENGER_SCHEMA_VERSION",
    "ChampionChallengerEvaluation",
    "ComparisonAssumptions",
    "EvaluationMetrics",
    "EvaluationRecommendation",
    "ModelIdentity",
]
