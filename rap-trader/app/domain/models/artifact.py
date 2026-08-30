"""Immutable envelope for durable research and paper-trading artifacts."""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import canonical_bytes, sha256_fingerprint
from app.domain.models.market_data import UtcDatetime, _require_aware_utc

ARTIFACT_SCHEMA_VERSION: Literal["1.0"] = "1.0"
ARTIFACT_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactType(StrEnum):
    RESEARCH_DATA_SNAPSHOT = "research_data_snapshot"
    TRADE_DECISION = "trade_decision"
    HISTORICAL_BARS_RESULT = "historical_bars_result"
    BACKTEST_SUMMARY = "backtest_summary"
    RESEARCH_RUN = "research_run"
    RUN_EVENT = "run_event"
    FEATURE_SNAPSHOT = "feature_snapshot"
    FUNDAMENTAL_SNAPSHOT = "fundamental_snapshot"
    KRONOS_PREDICTION = "kronos_prediction"
    ANALYST_OPINION = "analyst_opinion"
    MACRO_OPINION = "macro_opinion"
    NEWS_OPINION = "news_opinion"
    PORTFOLIO_PROPOSAL = "portfolio_proposal"
    RISK_DECISION = "risk_decision"
    INVESTMENT_COMMITTEE_DECISION = "investment_committee_decision"
    CHAIRMAN_DECISION = "chairman_decision"
    DECISION_RUN_MANIFEST = "decision_run_manifest"
    DECISION_JOURNAL_ENTRY = "decision_journal_entry"
    OUTCOME_OBSERVATION = "outcome_observation"
    OUTCOME_EVALUATION = "outcome_evaluation"
    ATTRIBUTION_RECORD = "attribution_record"
    CHAMPION_CHALLENGER_EVALUATION = "champion_challenger_evaluation"


class ProvenanceReferenceKind(StrEnum):
    RESEARCH_RUN = "research_run"
    ARTIFACT = "artifact"
    RESEARCH_DATA_SNAPSHOT = "research_data_snapshot"
    SOURCE_DATASET = "source_dataset"
    MODEL_INPUT = "model_input"
    DETERMINISTIC_SOURCE = "deterministic_source"


class ProvenanceReference(BaseModel):
    """Typed reference to an upstream auditable source."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    kind: ProvenanceReferenceKind
    identifier: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=512)
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=128)

    @field_validator("kind", mode="before")
    @classmethod
    def coerce_kind(cls, value: object) -> ProvenanceReferenceKind | object:
        if isinstance(value, str):
            return ProvenanceReferenceKind(value)
        return value


class ArtifactEnvelope(BaseModel):
    """Strict immutable envelope for reproducible domain artifacts."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    artifact_id: str
    artifact_type: ArtifactType
    schema_version: Literal["1.0"] = ARTIFACT_SCHEMA_VERSION
    logical_as_of: UtcDatetime
    producer_version: str = Field(min_length=1)
    payload_hash: str
    provenance_references: tuple[ProvenanceReference, ...]
    payload: Any = None

    @field_validator("artifact_type", mode="before")
    @classmethod
    def coerce_artifact_type(cls, value: object) -> ArtifactType | object:
        if isinstance(value, str):
            return ArtifactType(value)
        return value

    @field_validator("artifact_id", "payload_hash")
    @classmethod
    def validate_hex_identifier(cls, value: str) -> str:
        if not ARTIFACT_ID_PATTERN.fullmatch(value):
            raise ValueError("identifier must be a 64-character lowercase hex string")
        return value

    @field_validator("logical_as_of")
    @classmethod
    def normalize_logical_as_of(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("provenance_references", mode="before")
    @classmethod
    def coerce_provenance_references(cls, value: object) -> tuple[ProvenanceReference, ...]:
        if value is None:
            raise ValueError("provenance_references is required")
        if isinstance(value, list):
            return tuple(ProvenanceReference(**item) if isinstance(item, dict) else item for item in value)
        if isinstance(value, tuple):
            return tuple(ProvenanceReference(**item) if isinstance(item, dict) else item for item in value)
        raise ValueError("provenance_references must be a sequence")

    @model_validator(mode="after")
    def validate_provenance_chain(self) -> ArtifactEnvelope:
        if not self.provenance_references:
            raise ValueError("provenance_references must not be empty")
        return self

    def verify_payload(self, payload: Any) -> bool:
        return sha256_fingerprint(payload) == self.payload_hash

    def _identity_material(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type.value,
            "schema_version": self.schema_version,
            "logical_as_of": self.logical_as_of.isoformat(),
            "producer_version": self.producer_version,
            "payload_hash": self.payload_hash,
            "provenance_references": [reference.model_dump(mode="json", exclude_none=True) for reference in self.provenance_references],
        }

    @classmethod
    def _canonical_payload(cls, payload: Any) -> bytes:
        return canonical_bytes(payload)

    @classmethod
    def create(cls, *, payload: Any, provenance_references: tuple[ProvenanceReference, ...], **values: Any) -> Self:
        if not provenance_references:
            raise ValueError("provenance_references must not be empty")

        material = dict(values)
        material.setdefault("schema_version", ARTIFACT_SCHEMA_VERSION)
        normalized_provenance = tuple(
            ProvenanceReference(**item.model_dump(mode="json", exclude_none=True))
            if isinstance(item, ProvenanceReference)
            else ProvenanceReference(**item)
            for item in provenance_references
        )
        material["provenance_references"] = normalized_provenance
        payload_hash = sha256_fingerprint(payload)

        provisional = cls.model_validate({**material, "artifact_id": "0" * 64, "payload_hash": "0" * 64})
        identity_material = {
            "artifact_type": provisional.artifact_type.value,
            "schema_version": provisional.schema_version,
            "logical_as_of": provisional.logical_as_of.isoformat(),
            "producer_version": provisional.producer_version,
            "payload_hash": payload_hash,
            "provenance_references": [reference.model_dump(mode="json", exclude_none=True) for reference in normalized_provenance],
        }
        artifact_id = sha256_fingerprint(identity_material)

        return cls(
            artifact_id=artifact_id,
            artifact_type=material["artifact_type"],
            logical_as_of=material["logical_as_of"],
            producer_version=material["producer_version"],
            payload_hash=payload_hash,
            provenance_references=normalized_provenance,
            payload=payload,
        )


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactEnvelope",
    "ArtifactType",
    "ProvenanceReference",
    "ProvenanceReferenceKind",
]
