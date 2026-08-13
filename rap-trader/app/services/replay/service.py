"""Bounded deterministic replay service using verified ArtifactStore loading."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, ClassVar

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError
from app.services.replay.errors import (
    ReplayArtifactNotFoundError,
    ReplayGraphCorruptedError,
    ReplayInvalidTerminalError,
)
from app.services.replay.graph import ReplayGraph
from app.services.replay.graph_builder import ReplayGraphBuilder, ReplayGraphNodeMetadata
from app.services.replay.manifest import DecisionRunManifest


class _ARTIFACT_TYPE_TO_STAGE:
    MAPPING: ClassVar[dict[ArtifactType, str]] = {
        ArtifactType.RESEARCH_DATA_SNAPSHOT: "research_data_snapshot",
        ArtifactType.FEATURE_SNAPSHOT: "feature_snapshot",
        ArtifactType.FUNDAMENTAL_SNAPSHOT: "fundamental_snapshot",
        ArtifactType.TRADE_DECISION: "trade_decision",
        ArtifactType.HISTORICAL_BARS_RESULT: "historical_bars_result",
        ArtifactType.BACKTEST_SUMMARY: "backtest_summary",
        ArtifactType.RESEARCH_RUN: "research_run",
        ArtifactType.RUN_EVENT: "run_event",
        ArtifactType.KRONOS_PREDICTION: "kronos_prediction",
        ArtifactType.ANALYST_OPINION: "analyst_opinion",
        ArtifactType.MACRO_OPINION: "macro_opinion",
        ArtifactType.NEWS_OPINION: "news_opinion",
        ArtifactType.PORTFOLIO_PROPOSAL: "portfolio_proposal",
        ArtifactType.RISK_DECISION: "risk_decision",
        ArtifactType.INVESTMENT_COMMITTEE_DECISION: "investment_committee_decision",
        ArtifactType.CHAIRMAN_DECISION: "chairman_decision",
        ArtifactType.DECISION_RUN_MANIFEST: "decision_run_manifest",
    }

    @classmethod
    def stage(cls, artifact_type: ArtifactType) -> str:
        return cls.MAPPING.get(artifact_type, artifact_type.value)


class ReplayService:
    """Verified replay DAG builder for persisted decision lineage."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        max_depth: int = 64,
        max_nodes: int = 512,
    ) -> None:
        if max_depth <= 0 or max_nodes <= 0:
            raise ValueError("traversal limits must be positive")
        self.store = store
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self._builder = ReplayGraphBuilder(max_depth=max_depth, max_nodes=max_nodes)

    def build_graph(self, terminal_artifact_id: str) -> ReplayGraph:
        terminal = self._load_verified(terminal_artifact_id)
        if terminal.artifact_type is not ArtifactType.TRADE_DECISION:
            raise ReplayInvalidTerminalError(
                artifact_id=terminal.artifact_id,
                artifact_type=terminal.artifact_type.value,
            )

        lookup: dict[str, ReplayGraphNodeMetadata] = {}
        queue: deque[str] = deque()
        queue.append(terminal.artifact_id)

        while queue:
            current_id = queue.popleft()
            if current_id in lookup:
                continue
            envelope = self._load_verified(current_id)
            upstream_ids = tuple(self.store.get_direct_dependencies(current_id))
            lookup[current_id] = ReplayGraphNodeMetadata(
                artifact_id=envelope.artifact_id,
                artifact_type=envelope.artifact_type.value,
                logical_as_of=envelope.logical_as_of.isoformat(),
                stage=_ARTIFACT_TYPE_TO_STAGE.stage(envelope.artifact_type),
                producer_version=envelope.producer_version,
                upstream_ids=upstream_ids,
            )
            for upstream_id in upstream_ids:
                if upstream_id not in lookup:
                    queue.append(upstream_id)

        return self._builder.build(lookup[terminal.artifact_id], lookup)

    def replay(self, terminal_artifact_id: str) -> ReplayGraph:
        """Replay and verify a persisted decision lineage."""
        return self.build_graph(terminal_artifact_id)

    def create_manifest(
        self,
        terminal_artifact_id: str,
        research_run_id: str,
        producer_version: str = "rap-trader-replay-1.0",
    ) -> tuple[DecisionRunManifest, ArtifactEnvelope]:
        graph = self.build_graph(terminal_artifact_id)
        manifest = DecisionRunManifest(
            research_run_id=research_run_id,
            terminal_artifact_id=graph.terminal_artifact_id,
            logical_as_of=next(node.logical_as_of for node in graph.nodes if node.artifact_id == graph.terminal_artifact_id),
            ordered_graph_nodes=tuple(graph.ordered_artifact_ids),
            ordered_graph_edges=tuple((edge.upstream_artifact_id, edge.downstream_artifact_id) for edge in graph.edges),
            root_artifact_ids=tuple(graph.root_artifact_ids),
            producer_version=producer_version,
        )
        envelope = self.store.put(manifest.envelope())
        return manifest, envelope

    def _load_verified(self, artifact_id: str) -> ArtifactEnvelope:
        try:
            return self.store.get(artifact_id)
        except ArtifactNotFoundError as exc:
            raise ReplayArtifactNotFoundError(artifact_id=exc.artifact_id) from exc
        except ArtifactCorruptedError as exc:
            raise ReplayGraphCorruptedError(artifact_id=exc.artifact_id, reason=exc.reason) from exc


class DecisionRunRecorder:
    """Persist stage outputs with immutable artifact provenance."""

    def __init__(self, store: ArtifactStore, producer_version: str = "rap-trader-replay-1.0") -> None:
        self.store = store
        self.producer_version = producer_version

    def record_stage(
        self,
        *,
        artifact_type: ArtifactType,
        payload: Any,
        logical_as_of: datetime | str,
        producer_version: str | None = None,
        upstream_artifact_ids: tuple[str, ...] = (),
        research_run_id: str | None = None,
    ) -> ArtifactEnvelope:
        if isinstance(logical_as_of, str):
            logical_as_of = datetime.fromisoformat(logical_as_of)
        provenance = [
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=upstream_id,
                description="upstream decision lineage artifact",
                producer="rap-trader-replay",
                producer_version=self.producer_version,
            )
            for upstream_id in dict.fromkeys(upstream_artifact_ids)
        ]
        if research_run_id is not None:
            provenance.append(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.RESEARCH_RUN,
                    identifier=research_run_id,
                    description="research run associated with this artifact",
                    producer="rap-trader-replay",
                    producer_version=self.producer_version,
                )
            )
        if not provenance:
            raise ValueError("provenance_references must not be empty")
        return self.store.put(
            ArtifactEnvelope.create(
                payload=payload,
                artifact_type=artifact_type,
                logical_as_of=logical_as_of,
                producer_version=producer_version or self.producer_version,
                provenance_references=tuple(provenance),
            )
        )


__all__ = ["DecisionRunRecorder", "ReplayService"]
