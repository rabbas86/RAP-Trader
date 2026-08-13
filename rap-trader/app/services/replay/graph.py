"""Immutable replay graph nodes and edges."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.artifact import ArtifactEnvelope


class ReplayGraphNode(BaseModel):
    """Stable metadata for a replay graph node."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=64, max_length=64)
    artifact_type: str = Field(min_length=1)
    logical_as_of: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)

    @classmethod
    def from_envelope(cls, envelope: ArtifactEnvelope, stage: str) -> ReplayGraphNode:
        return cls(
            artifact_id=envelope.artifact_id,
            artifact_type=envelope.artifact_type.value,
            logical_as_of=envelope.logical_as_of.isoformat(),
            stage=stage,
            producer_version=envelope.producer_version,
        )


class ReplayGraphEdge(BaseModel):
    """Explicit dependency relationship between artifacts."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    upstream_artifact_id: str = Field(min_length=64, max_length=64)
    downstream_artifact_id: str = Field(min_length=64, max_length=64)
    relationship: Literal["depends_on"] = "depends_on"


class ReplayGraph(BaseModel):
    """Deterministic replay decision graph."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    nodes: tuple[ReplayGraphNode, ...] = Field(default_factory=tuple)
    edges: tuple[ReplayGraphEdge, ...] = Field(default_factory=tuple)
    root_artifact_ids: tuple[str, ...] = Field(default_factory=tuple)
    terminal_artifact_id: str = Field(min_length=64, max_length=64)
    ordered_artifact_ids: tuple[str, ...] = Field(default_factory=tuple)
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)


__all__ = ["ReplayGraph", "ReplayGraphEdge", "ReplayGraphNode"]
