"""Immutable DecisionRunManifest for persisted decision runs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind

MANIFEST_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class DecisionRunManifest(BaseModel):
    """Immutable manifest describing one completed research decision run."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    manifest_schema_version: Literal["1.0"] = MANIFEST_SCHEMA_VERSION
    research_run_id: str = Field(min_length=64, max_length=64)
    terminal_artifact_id: str = Field(min_length=64, max_length=64)
    logical_as_of: str = Field(min_length=1)
    ordered_graph_nodes: tuple[str, ...] = Field(default_factory=tuple)
    ordered_graph_edges: tuple[tuple[str, str], ...] = Field(default_factory=tuple)
    root_artifact_ids: tuple[str, ...] = Field(default_factory=tuple)
    producer_version: str = Field(min_length=1)

    @field_validator("ordered_graph_nodes", "root_artifact_ids", mode="before")
    @classmethod
    def coerce_str_sequence(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise TypeError("ordered_graph_nodes must be a list or tuple of strings")

    @field_validator("ordered_graph_edges", mode="before")
    @classmethod
    def coerce_edge_tuples(cls, value: object) -> tuple[tuple[str, str], ...]:
        if isinstance(value, (list, tuple)):
            return tuple(tuple(item) for item in value)
        raise TypeError("ordered_graph_edges must be a list or tuple of string pairs")

    def graph_fingerprint(self) -> str:
        material = {
            "manifest_schema_version": self.manifest_schema_version,
            "research_run_id": self.research_run_id,
            "terminal_artifact_id": self.terminal_artifact_id,
            "logical_as_of": self.logical_as_of,
            "ordered_graph_nodes": list(self.ordered_graph_nodes),
            "ordered_graph_edges": [list(edge) for edge in self.ordered_graph_edges],
            "root_artifact_ids": list(self.root_artifact_ids),
            "producer_version": self.producer_version,
        }
        return sha256_fingerprint(material)

    def envelope(self) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.DECISION_RUN_MANIFEST,
            logical_as_of=datetime.fromisoformat(self.logical_as_of),
            producer_version=self.producer_version,
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.RESEARCH_RUN,
                    identifier=self.research_run_id,
                    description="research run associated with this decision run manifest",
                    producer="rap-trader-replay",
                    producer_version="1.0",
                ),
            ),
        )


__all__ = ["MANIFEST_SCHEMA_VERSION", "DecisionRunManifest"]
