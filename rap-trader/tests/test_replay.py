"""Phase 15D Decision Run Replay DAG tests."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.data_platform import (
    DataAvailability,
    DataDomain,
    DataQuality,
    DataRecordId,
    DataRevision,
    DataSourceIdentity,
    NormalizedDataRecord,
    QualitySummary,
    ResearchDataSnapshot,
    SnapshotProvenance,
)
from app.domain.models.decision import TradeDecision
from app.services.artifacts.errors import InvalidArtifactIdError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.replay.errors import (
    ReplayArtifactNotFoundError,
    ReplayCycleDetectedError,
    ReplayDepthExceededError,
    ReplayGraphCorruptedError,
    ReplayGraphTooLargeError,
    ReplayInvalidTerminalError,
    ReplayTemporalViolationError,
)
from app.services.replay.graph_builder import ReplayGraphBuilder, ReplayGraphNodeMetadata
from app.services.replay.manifest import DecisionRunManifest
from app.services.replay.service import ReplayService

AS_OF = datetime(2026, 8, 1, tzinfo=UTC)
LATER = datetime(2026, 8, 2, tzinfo=UTC)
SOURCE = DataSourceIdentity(
    provider="deterministic_mock",
    dataset="unit_test",
    source_version="1",
    schema_version="1",
    offline_capable=True,
    authoritative=False,
)

RESEARCH_RUN_ID = "e3cf8b99" + "0" * 56


@pytest.fixture()
def store() -> InMemoryArtifactStore:
    return InMemoryArtifactStore()


@pytest.fixture()
def temp_dir() -> str:
    with tempfile.TemporaryDirectory() as directory:
        yield directory


def _snapshot() -> ResearchDataSnapshot:
    availability = DataAvailability(
        observed_at=AS_OF,
        available_at=AS_OF,
        ingested_at=AS_OF,
    )
    revision = DataRevision(
        revision_id="r0",
        revision_number=0,
        revised_at=AS_OF,
        available_at=AS_OF,
        source_fingerprint=sha256_fingerprint({"record": "revision"}),
    )
    quality = DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0)
    record = NormalizedDataRecord(
        record_id=DataRecordId("market.test.1"),
        domain=DataDomain.MARKET,
        symbol_or_entity="AAPL",
        value=189.55,
        units="price_close",
        availability=availability,
        revision=revision,
        source=SOURCE,
        quality=quality,
        source_fingerprint=sha256_fingerprint({"record": "market.test.1"}),
        schema_version="1",
        period_start=AS_OF,
        period_end=AS_OF,
    )
    provenance = SnapshotProvenance(
        snapshot_id="snapshot-unit-1",
        as_of=AS_OF,
        created_at=AS_OF,
        source_versions={"deterministic_mock": "1"},
        input_fingerprints=(sha256_fingerprint({"input": "market"}),),
        schema_version="1",
        platform_version="platform-1",
    )
    return ResearchDataSnapshot(
        snapshot_id="snapshot-unit-1",
        as_of=AS_OF,
        requested_domains=(DataDomain.MARKET,),
        records=(record,),
        source_versions={"deterministic_mock": "1"},
        schema_version="1",
        platform_version="platform-1",
        created_at=AS_OF,
        input_fingerprints=(sha256_fingerprint({"input": "market"}),),
        quality_summary=QualitySummary(
            total_records=1,
            average_score=1.0,
            records_with_warnings=0,
            domains_represented=(DataDomain.MARKET,),
        ),
        provenance=provenance,
    )


def _trade_decision() -> TradeDecision:
    return TradeDecision(
        decision_id=UUID("91f7b3dc-3b74-4d82-9e55-1c30990e2a8d"),
        ticker="AAPL",
        action="BUY",
        confidence=0.84,
        quantity=120,
        order_type="limit",
        limit_price=188.25,
        stop_loss=176.10,
        take_profit=210.45,
        rationale="replay unit test decision",
        evidence=[],
        created_at=AS_OF,
    )


def _provenance(
    *, identifier: str = RESEARCH_RUN_ID, kind: ProvenanceReferenceKind = ProvenanceReferenceKind.RESEARCH_RUN
) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=kind,
            identifier=identifier,
            description="seed provenance for replay tests",
            producer="phase15d-tests",
            producer_version="1.0",
        ),
    )


def _artifact_provenance(upstream_artifact_id: str) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.ARTIFACT,
            identifier=upstream_artifact_id,
            description="unit-test upstream",
            producer="phase15d-tests",
            producer_version="1.0",
        ),
    )


def _research_run_provenance() -> tuple[ProvenanceReference, ...]:
    return _provenance()


def _snapshot_envelope() -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=_snapshot().model_dump(mode="json"),
        artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=_research_run_provenance(),
    )


def _terminal(upstream_artifact_id: str = "0" * 64) -> ArtifactEnvelope:
    provenance = _artifact_provenance(upstream_artifact_id)
    if upstream_artifact_id == "0" * 64:
        try:
            from app.services.artifacts.base import _validate_artifact_id

            _validate_artifact_id(upstream_artifact_id)
        except InvalidArtifactIdError:
            provenance = ()
    return ArtifactEnvelope.create(
        payload=_trade_decision().model_dump(mode="json"),
        artifact_type=ArtifactType.TRADE_DECISION,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=provenance,
    )


def _put_chain(store: InMemoryArtifactStore) -> tuple[ArtifactEnvelope, ArtifactEnvelope, ArtifactEnvelope]:
    root = _snapshot_envelope()
    middle = ArtifactEnvelope.create(
        payload={"value": 2.0},
        artifact_type=ArtifactType.BACKTEST_SUMMARY,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=_artifact_provenance(root.artifact_id),
    )
    terminal = _terminal(upstream_artifact_id=middle.artifact_id)
    for item in (root, middle, terminal):
        store.put(item)
    return root, middle, terminal


class TestReplayGraphBuilderContracts:
    def test_self_cycle_detected(self) -> None:
        builder = ReplayGraphBuilder(max_depth=2, max_nodes=2)
        metadata = ReplayGraphNodeMetadata(
            artifact_id="a" * 64,
            artifact_type="trade_decision",
            logical_as_of=AS_OF.isoformat(),
            stage="trade_decision",
            producer_version="1.0",
            upstream_ids=("a" * 64,),
        )
        with pytest.raises(ReplayCycleDetectedError):
            builder.build(metadata, {"a" * 64: metadata})

    def test_two_node_cycle_detected(self) -> None:
        builder = ReplayGraphBuilder(max_depth=2, max_nodes=4)
        lookup = {
            "a" * 64: ReplayGraphNodeMetadata(
                artifact_id="a" * 64,
                artifact_type="backtest_summary",
                logical_as_of=AS_OF.isoformat(),
                stage="backtest_summary",
                producer_version="1.0",
                upstream_ids=("b" * 64,),
            ),
            "b" * 64: ReplayGraphNodeMetadata(
                artifact_id="b" * 64,
                artifact_type="trade_decision",
                logical_as_of=AS_OF.isoformat(),
                stage="trade_decision",
                producer_version="1.0",
                upstream_ids=("a" * 64,),
            ),
        }
        with pytest.raises(ReplayCycleDetectedError):
            builder.build(lookup["b" * 64], lookup)

    def test_depth_exceeded(self) -> None:
        builder = ReplayGraphBuilder(max_depth=3, max_nodes=10)

        def node(artifact_id: str, upstream_id: str | None) -> ReplayGraphNodeMetadata:
            return ReplayGraphNodeMetadata(
                artifact_id=artifact_id,
                artifact_type="backtest_summary",
                logical_as_of=AS_OF.isoformat(),
                stage="backtest_summary",
                producer_version="1.0",
                upstream_ids=(upstream_id,) if upstream_id else (),
            )

        lookup = {
            "f" * 64: node("f" * 64, "e" * 64),
            "e" * 64: node("e" * 64, "d" * 64),
            "d" * 64: node("d" * 64, "c" * 64),
            "c" * 64: node("c" * 64, "b" * 64),
            "b" * 64: node("b" * 64, None),
        }
        terminal = ReplayGraphNodeMetadata(
            artifact_id="g" * 64,
            artifact_type="trade_decision",
            logical_as_of=AS_OF.isoformat(),
            stage="trade_decision",
            producer_version="1.0",
            upstream_ids=("f" * 64,),
        )
        lookup[terminal.artifact_id] = terminal
        with pytest.raises(ReplayDepthExceededError):
            builder.build(terminal, lookup)

    def test_node_limit_exceeded(self) -> None:
        builder = ReplayGraphBuilder(max_depth=10, max_nodes=3)

        def node(artifact_id: str, upstream_id: str | None) -> ReplayGraphNodeMetadata:
            return ReplayGraphNodeMetadata(
                artifact_id=artifact_id,
                artifact_type="backtest_summary",
                logical_as_of=AS_OF.isoformat(),
                stage="backtest_summary",
                producer_version="1.0",
                upstream_ids=(upstream_id,) if upstream_id else (),
            )

        lookup = {
            "y" * 64: node("y" * 64, "x" * 64),
            "x" * 64: node("x" * 64, "w" * 64),
            "w" * 64: node("w" * 64, "v" * 64),
            "v" * 64: node("v" * 64, None),
        }
        terminal = ReplayGraphNodeMetadata(
            artifact_id="z" * 64,
            artifact_type="trade_decision",
            logical_as_of=AS_OF.isoformat(),
            stage="trade_decision",
            producer_version="1.0",
            upstream_ids=("y" * 64,),
        )
        lookup[terminal.artifact_id] = terminal
        with pytest.raises(ReplayGraphTooLargeError):
            builder.build(terminal, lookup)

    def test_temporal_violation_rejected(self) -> None:
        builder = ReplayGraphBuilder(max_depth=10, max_nodes=10)
        lookup = {
            "a" * 64: ReplayGraphNodeMetadata(
                artifact_id="a" * 64,
                artifact_type="backtest_summary",
                logical_as_of=LATER.isoformat(),
                stage="backtest_summary",
                producer_version="1.0",
                upstream_ids=(),
            ),
            "b" * 64: ReplayGraphNodeMetadata(
                artifact_id="b" * 64,
                artifact_type="trade_decision",
                logical_as_of=AS_OF.isoformat(),
                stage="trade_decision",
                producer_version="1.0",
                upstream_ids=("a" * 64,),
            ),
        }
        with pytest.raises(ReplayTemporalViolationError):
            builder.build(lookup["b" * 64], lookup)

    def test_branching_dag_produces_stable_order(self) -> None:
        builder = ReplayGraphBuilder(max_depth=10, max_nodes=10)
        root = ReplayGraphNodeMetadata(
            artifact_id="a" * 64,
            artifact_type="research_data_snapshot",
            logical_as_of=AS_OF.isoformat(),
            stage="research_data_snapshot",
            producer_version="1.0",
            upstream_ids=(),
        )
        branch_a = ReplayGraphNodeMetadata(
            artifact_id="b" * 64,
            artifact_type="backtest_summary",
            logical_as_of=AS_OF.isoformat(),
            stage="backtest_summary",
            producer_version="1.0",
            upstream_ids=(root.artifact_id,),
        )
        branch_b = ReplayGraphNodeMetadata(
            artifact_id="c" * 64,
            artifact_type="fundamental_snapshot",
            logical_as_of=AS_OF.isoformat(),
            stage="fundamental_snapshot",
            producer_version="1.0",
            upstream_ids=(root.artifact_id,),
        )
        terminal = ReplayGraphNodeMetadata(
            artifact_id="d" * 64,
            artifact_type="trade_decision",
            logical_as_of=AS_OF.isoformat(),
            stage="trade_decision",
            producer_version="1.0",
            upstream_ids=(branch_a.artifact_id, branch_b.artifact_id),
        )
        lookup = {
            root.artifact_id: root,
            branch_a.artifact_id: branch_a,
            branch_b.artifact_id: branch_b,
            terminal.artifact_id: terminal,
        }
        graph = builder.build(terminal, lookup)
        assert graph.node_count == 4
        assert graph.edge_count == 4
        assert graph.root_artifact_ids == (root.artifact_id,)
        assert graph.ordered_artifact_ids.index(root.artifact_id) < graph.ordered_artifact_ids.index(terminal.artifact_id)
        assert builder.build(terminal, lookup).ordered_artifact_ids == graph.ordered_artifact_ids

    def test_missing_upstream_rejected(self) -> None:
        builder = ReplayGraphBuilder(max_depth=10, max_nodes=10)
        lookup = {
            "a" * 64: ReplayGraphNodeMetadata(
                artifact_id="a" * 64,
                artifact_type="backtest_summary",
                logical_as_of=AS_OF.isoformat(),
                stage="backtest_summary",
                producer_version="1.0",
                upstream_ids=("f" * 64,),
            ),
        }
        with pytest.raises(ReplayCycleDetectedError):
            builder.build(lookup["a" * 64], lookup)

    def test_list_and_tuple_inputs_produce_identical_manifest(self) -> None:
        manifest_list = DecisionRunManifest(
            research_run_id=RESEARCH_RUN_ID,
            terminal_artifact_id="b" * 64,
            logical_as_of=AS_OF.isoformat(),
            ordered_graph_nodes=["a" * 64, "b" * 64],
            ordered_graph_edges=[["a" * 64, "b" * 64]],
            root_artifact_ids=["a" * 64],
            producer_version="1.0",
        )
        manifest_tuple = DecisionRunManifest(
            research_run_id=RESEARCH_RUN_ID,
            terminal_artifact_id="b" * 64,
            logical_as_of=AS_OF.isoformat(),
            ordered_graph_nodes=("a" * 64, "b" * 64),
            ordered_graph_edges=(("a" * 64, "b" * 64),),
            root_artifact_ids=("a" * 64,),
            producer_version="1.0",
        )
        assert isinstance(manifest_list.ordered_graph_nodes, tuple)
        assert isinstance(manifest_list.ordered_graph_edges, tuple)
        assert isinstance(manifest_list.root_artifact_ids, tuple)
        assert isinstance(manifest_list.ordered_graph_edges[0], tuple)
        assert manifest_list.graph_fingerprint() == manifest_tuple.graph_fingerprint()


class TestReplayServiceIntegration:
    def test_valid_replay(self, store: InMemoryArtifactStore) -> None:
        root, middle, terminal = _put_chain(store)
        graph = ReplayService(store=store).replay(terminal.artifact_id)
        assert graph.ordered_artifact_ids == (root.artifact_id, middle.artifact_id, terminal.artifact_id)
        assert graph.terminal_artifact_id == terminal.artifact_id
        assert graph.node_count == 3
        assert graph.edge_count == 2

    def test_missing_dependency_raises(self, store: InMemoryArtifactStore) -> None:
        terminal = _terminal()
        store.put(terminal)
        with pytest.raises(ReplayArtifactNotFoundError):
            ReplayService(store=store).replay(terminal.artifact_id)

    def test_corrupted_artifact_raises(self, temp_dir: str) -> None:
        file_store = FileArtifactStore(temp_dir)
        _, _, terminal = _put_chain(file_store)
        prefix = terminal.artifact_id[:2]
        target_dir = os.path.join(temp_dir, "artifacts", prefix)
        filepath = os.path.join(target_dir, f"{terminal.artifact_id}.json")
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write("not-json")
        with pytest.raises(ReplayGraphCorruptedError):
            ReplayService(store=file_store).replay(terminal.artifact_id)

    def test_wrong_terminal_type_rejected(self, store: InMemoryArtifactStore) -> None:
        upstream = ArtifactEnvelope.create(
            payload={"value": 1.0},
            artifact_type=ArtifactType.BACKTEST_SUMMARY,
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=_research_run_provenance(),
        )
        store.put(upstream)
        with pytest.raises(ReplayInvalidTerminalError):
            ReplayService(store=store).replay(upstream.artifact_id)

    def test_provenance_extracted_from_envelope(self, store: InMemoryArtifactStore) -> None:
        root, middle, terminal = _put_chain(store)
        assert store.get_direct_dependencies(terminal.artifact_id) == (middle.artifact_id,)
        assert store.get_direct_dependencies(middle.artifact_id) == (root.artifact_id,)

    def test_manifest_persistence_and_restart(self, temp_dir: str) -> None:
        file_store = FileArtifactStore(temp_dir)
        _, _, terminal = _put_chain(file_store)
        service = ReplayService(store=file_store)
        manifest, _ = service.create_manifest(terminal.artifact_id, RESEARCH_RUN_ID)
        assert manifest.terminal_artifact_id == terminal.artifact_id
        assert manifest.research_run_id == RESEARCH_RUN_ID
        reloaded = FileArtifactStore(temp_dir)
        restored_graph = ReplayService(store=reloaded).replay(terminal.artifact_id)
        assert restored_graph.ordered_artifact_ids == service.replay(terminal.artifact_id).ordered_artifact_ids
