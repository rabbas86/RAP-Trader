"""Immutable decision journal entry."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.market_data import Symbol, UtcDatetime

JOURNAL_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class DecisionJournalEntry(BaseModel):
    """Immutable historical record of a finalized decision."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    journal_entry_id: str = Field(min_length=64, max_length=64)
    journal_schema_version: Literal["1.0"] = JOURNAL_SCHEMA_VERSION
    decision_artifact_id: str = Field(min_length=64, max_length=64)
    decision_run_manifest_id: str = Field(min_length=64, max_length=64)
    research_run_id: str = Field(min_length=64, max_length=64)
    symbol: Symbol
    decision_at: UtcDatetime
    logical_as_of: UtcDatetime
    direction: Literal["BUY", "SELL", "WAIT"]
    confidence: float = Field(ge=0, le=1)
    producer_version: str = Field(min_length=1)
    graph_fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("decision_at", "logical_as_of", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            normalized = datetime.fromisoformat(value)
        elif isinstance(value, datetime):
            normalized = value
        else:
            raise TypeError("timestamp must be an ISO datetime string or datetime instance")
        if normalized.tzinfo is None or normalized.utcoffset() is None:
            raise ValueError("timestamp must include timezone information")
        return normalized.astimezone(UTC)

    @model_validator(mode="after")
    def validate_decision_time_ordering(self) -> DecisionJournalEntry:
        if self.decision_at > self.logical_as_of:
            raise ValueError("decision_at cannot be after logical_as_of")
        return self

    def fingerprint(self) -> str:
        material = self.model_dump(mode="json")
        return sha256_fingerprint(material)

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.DECISION_JOURNAL_ENTRY,
            logical_as_of=self.logical_as_of,
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_artifact_id,
                    description="finalized decision artifact recorded in the journal",
                    producer="rap-trader-decision-journal",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=self.decision_run_manifest_id,
                    description="decision run manifest for this journal entry",
                    producer="rap-trader-decision-journal",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.RESEARCH_RUN,
                    identifier=self.research_run_id,
                    description="research run associated with this journal entry",
                    producer="rap-trader-decision-journal",
                    producer_version="1.0",
                ),
            ),
        )


__all__ = ["JOURNAL_SCHEMA_VERSION", "DecisionJournalEntry"]
