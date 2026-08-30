"""Phase 15E Decision Journal tests."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.decision import TradeDecision
from app.domain.models.market_data import Symbol
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.decision_journal import (
    DecisionJournalEntry,
    DecisionJournalEntryNotFoundError,
    DecisionJournalService,
    DecisionJournalValidationError,
)
from app.services.replay.manifest import DecisionRunManifest
from app.services.replay.service import ReplayService

SYMBOL = Symbol("AAPL")
MSFT = Symbol("MSFT")
AS_OF = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
DECISION_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
LATER_DECISION_AT = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)
RESEARCH_RUN_ID = "c" * 64
JOURNAL_ENTRY_ID = "d" * 64


def _trade_decision(ticker="AAPL", action="BUY") -> TradeDecision:
    return TradeDecision(
        decision_id=UUID("12345678-1234-5678-1234-567812345678"),
        ticker=ticker,
        action=action,
        confidence=0.85,
        quantity=100,
        order_type="market",
        rationale="journal unit test decision",
        evidence=[],
        created_at=DECISION_AT,
    )


def _put_artifact(store, payload, artifact_type, logical_as_of=AS_OF):
    envelope = ArtifactEnvelope.create(
        payload=payload,
        artifact_type=artifact_type,
        logical_as_of=logical_as_of,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier="0" * 64,
                description="unit test artifact",
                producer="decision-journal-tests",
                producer_version="1.0",
            ),
        ),
    )
    return store.put(envelope)


def _journal_entry_payload(
    decision_artifact_id, manifest_id, graph_fingerprint, symbol="AAPL", direction="BUY", decision_at=DECISION_AT, logical_as_of=AS_OF
):
    return {
        "journal_entry_id": JOURNAL_ENTRY_ID,
        "journal_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_run_manifest_id": manifest_id,
        "research_run_id": RESEARCH_RUN_ID,
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "logical_as_of": logical_as_of.isoformat(),
        "direction": direction,
        "confidence": 0.85,
        "producer_version": "1.0",
        "graph_fingerprint": graph_fingerprint,
    }


def test_valid_construction():
    decision_envelope = _put_artifact(InMemoryArtifactStore(), _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(InMemoryArtifactStore(), manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    entry = DecisionJournalEntry(**_journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint))
    assert entry.journal_entry_id == JOURNAL_ENTRY_ID
    assert entry.symbol.root == "AAPL"
    assert entry.direction == "BUY"
    assert entry.confidence == 0.85


def test_immutable_behavior():
    entry = DecisionJournalEntry(**_journal_entry_payload("a" * 64, "b" * 64, sha256_fingerprint({"nodes": [], "edges": []})))
    with pytest.raises(ValidationError):
        entry.symbol = Symbol("MSFT")


def test_deterministic_identity():
    payload = _journal_entry_payload("a" * 64, "b" * 64, sha256_fingerprint({"nodes": [], "edges": []}))
    first = DecisionJournalEntry(**payload)
    second = DecisionJournalEntry(**payload)
    assert first.fingerprint() == second.fingerprint()
    assert first.envelope().artifact_id == second.envelope().artifact_id


def test_trade_decision_linkage_missing():
    store = InMemoryArtifactStore()
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": "a" * 64,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    with pytest.raises(DecisionJournalValidationError, match="decision artifact"):
        service.record_entry(
            DecisionJournalEntry(**_journal_entry_payload("0" * 63 + "1", manifest_envelope.artifact_id, graph_fingerprint))
        )


def test_manifest_linkage_missing():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    service = DecisionJournalService(store=store)
    graph_fingerprint = sha256_fingerprint({"nodes": [], "edges": []})
    with pytest.raises(DecisionJournalValidationError, match="decision run manifest"):
        service.record_entry(
            DecisionJournalEntry(**_journal_entry_payload(decision_envelope.artifact_id, "0" * 63 + "1", graph_fingerprint))
        )


def test_wrong_artifact_type_rejected():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.BACKTEST_SUMMARY)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    with pytest.raises(DecisionJournalValidationError, match="wrong artifact type"):
        service.record_entry(
            DecisionJournalEntry(**_journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint))
        )


def test_manifest_terminal_mismatch_rejected():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    other_decision_envelope = _put_artifact(
        store, _trade_decision(ticker="MSFT", action="SELL").model_dump(mode="json"), ArtifactType.TRADE_DECISION
    )
    assert decision_envelope.artifact_id != other_decision_envelope.artifact_id
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": other_decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    payload = _journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint)
    service = DecisionJournalService(store=store)
    with pytest.raises(DecisionJournalValidationError, match="manifest terminal artifact does not match"):
        service.record_entry(DecisionJournalEntry(**payload))


def test_graph_fingerprint_mismatch_rejected():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    payload = _journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint)
    payload["graph_fingerprint"] = "0" * 64
    with pytest.raises(DecisionJournalValidationError, match="graph fingerprint does not match"):
        service.record_entry(DecisionJournalEntry(**payload))


def test_corruption_rejected():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    persisted = service.record_entry(
        DecisionJournalEntry(**_journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint))
    )
    store._entries[persisted.artifact_id] = "not-json"
    with pytest.raises(ArtifactCorruptedError):
        service.get_entry(decision_envelope.artifact_id)


def test_persistence_and_idempotent_retrieval():
    store = InMemoryArtifactStore()
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    payload = _journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint)
    first = service.record_entry(DecisionJournalEntry(**payload))
    second = service.record_entry(DecisionJournalEntry(**payload))
    assert first.artifact_id == second.artifact_id
    assert service.get_entry(decision_envelope.artifact_id).decision_artifact_id == decision_envelope.artifact_id


def test_file_store_restart_persistence():
    temp_dir = tempfile.mkdtemp(prefix="rap-journal-")
    store = FileArtifactStore(temp_dir)
    decision_envelope = _put_artifact(store, _trade_decision().model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    service.record_entry(
        DecisionJournalEntry(**_journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint))
    )
    reloaded = FileArtifactStore(temp_dir)
    restored = DecisionJournalService(store=reloaded)
    assert restored.get_entry(decision_envelope.artifact_id).decision_artifact_id == decision_envelope.artifact_id


def test_queries_and_ordering():
    store = InMemoryArtifactStore()
    aa_decision = _put_artifact(store, _trade_decision(ticker="AAPL", action="BUY").model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    msft_decision = _put_artifact(store, _trade_decision(ticker="MSFT", action="SELL").model_dump(mode="json"), ArtifactType.TRADE_DECISION)
    early_manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": aa_decision.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    late_manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": msft_decision.artifact_id,
        "logical_as_of": LATER_DECISION_AT.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    early_manifest = _put_artifact(store, early_manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    late_manifest = _put_artifact(store, late_manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    early_graph = DecisionRunManifest.model_validate(early_manifest_payload).graph_fingerprint()
    late_graph = DecisionRunManifest.model_validate(late_manifest_payload).graph_fingerprint()
    service = DecisionJournalService(store=store)
    service.record_entry(
        DecisionJournalEntry(
            **_journal_entry_payload(
                aa_decision.artifact_id,
                early_manifest.artifact_id,
                early_graph,
                symbol="AAPL",
                direction="BUY",
                decision_at=DECISION_AT,
                logical_as_of=AS_OF,
            )
        )
    )
    service.record_entry(
        DecisionJournalEntry(
            **_journal_entry_payload(
                msft_decision.artifact_id,
                late_manifest.artifact_id,
                late_graph,
                symbol="MSFT",
                direction="SELL",
                decision_at=LATER_DECISION_AT,
                logical_as_of=LATER_DECISION_AT,
            )
        )
    )

    assert service.query(symbol="AAPL") == [
        DecisionJournalEntry(
            **_journal_entry_payload(
                aa_decision.artifact_id,
                early_manifest.artifact_id,
                early_graph,
                symbol="AAPL",
                direction="BUY",
                decision_at=DECISION_AT,
                logical_as_of=AS_OF,
            )
        )
    ]
    assert service.query(direction="SELL") == [
        DecisionJournalEntry(
            **_journal_entry_payload(
                msft_decision.artifact_id,
                late_manifest.artifact_id,
                late_graph,
                symbol="MSFT",
                direction="SELL",
                decision_at=LATER_DECISION_AT,
                logical_as_of=LATER_DECISION_AT,
            )
        )
    ]
    assert service.query(research_run_id=RESEARCH_RUN_ID) == [
        DecisionJournalEntry(
            **_journal_entry_payload(
                aa_decision.artifact_id,
                early_manifest.artifact_id,
                early_graph,
                symbol="AAPL",
                direction="BUY",
                decision_at=DECISION_AT,
                logical_as_of=AS_OF,
            )
        ),
        DecisionJournalEntry(
            **_journal_entry_payload(
                msft_decision.artifact_id,
                late_manifest.artifact_id,
                late_graph,
                symbol="MSFT",
                direction="SELL",
                decision_at=LATER_DECISION_AT,
                logical_as_of=LATER_DECISION_AT,
            )
        ),
    ]
    assert service.query(decision_at=DECISION_AT) == [
        DecisionJournalEntry(
            **_journal_entry_payload(
                aa_decision.artifact_id,
                early_manifest.artifact_id,
                early_graph,
                symbol="AAPL",
                direction="BUY",
                decision_at=DECISION_AT,
                logical_as_of=AS_OF,
            )
        )
    ]
    assert service.get_entry(aa_decision.artifact_id).decision_artifact_id == aa_decision.artifact_id
    assert service.get_entry(msft_decision.artifact_id).decision_artifact_id == msft_decision.artifact_id
    with pytest.raises(DecisionJournalEntryNotFoundError):
        service.get_entry("0" * 63 + "1")


def test_no_outcome_information():
    entry = DecisionJournalEntry(**_journal_entry_payload("a" * 64, "b" * 64, sha256_fingerprint({"nodes": [], "edges": []})))
    assert not hasattr(entry, "realized_return")
    assert not hasattr(entry, "outcome_score")
    assert not hasattr(entry, "future_price")


def test_existing_phase_stack_unchanged():
    store = InMemoryArtifactStore()
    _, _, terminal = _setup_replay_chain(store)
    replay_service = ReplayService(store=store)
    graph = replay_service.replay(terminal.artifact_id)
    assert graph.terminal_artifact_id == terminal.artifact_id
    assert graph.node_count == 3


def _setup_replay_chain(store):
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

    source = DataSourceIdentity(
        provider="deterministic_mock",
        dataset="unit_test",
        source_version="1",
        schema_version="1",
        offline_capable=True,
        authoritative=False,
    )
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
        source=source,
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
    snapshot = ResearchDataSnapshot(
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
    root = ArtifactEnvelope.create(
        payload=snapshot.model_dump(mode="json"),
        artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.RESEARCH_RUN,
                identifier="0" * 64,
                description="unit-test research run",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(root)
    middle = ArtifactEnvelope.create(
        payload={"value": 2.0},
        artifact_type=ArtifactType.BACKTEST_SUMMARY,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=root.artifact_id,
                description="unit-test upstream",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(middle)
    terminal = ArtifactEnvelope.create(
        payload=_trade_decision().model_dump(mode="json"),
        artifact_type=ArtifactType.TRADE_DECISION,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=middle.artifact_id,
                description="unit-test upstream",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(terminal)
    return root, middle, terminal


def test_replay_dependencies_are_deterministic():
    store = InMemoryArtifactStore()
    root, middle, terminal = _setup_replay_chain(store)
    assert store.get_direct_dependencies(terminal.artifact_id) == (middle.artifact_id,)
    assert store.get_direct_dependencies(middle.artifact_id) == (root.artifact_id,)
    assert store.get_direct_dependencies(root.artifact_id) == ()
