"""Phase 15F Outcome Journal tests."""

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
from app.services.decision_journal import DecisionJournalEntry, DecisionJournalService
from app.services.outcome_journal import (
    OUTCOME_SCHEMA_VERSION,
    FuturePriceMethodology,
    OutcomeEvaluation,
    OutcomeJournalService,
    OutcomeJournalValidationError,
    OutcomeObservation,
    OutcomeStatus,
    ReferencePriceMethodology,
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
        rationale="outcome unit test decision",
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
                producer="outcome-journal-tests",
                producer_version="1.0",
            ),
        ),
    )
    return store.put(envelope)


def _journal_entry_payload(
    decision_artifact_id,
    manifest_id,
    graph_fingerprint,
    symbol="AAPL",
    direction="BUY",
    decision_at=DECISION_AT,
    logical_as_of=AS_OF,
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


def _setup_decision_chain(store, ticker="AAPL", action="BUY"):
    decision_envelope = _put_artifact(
        store, _trade_decision(ticker=ticker, action=action).model_dump(mode="json"), ArtifactType.TRADE_DECISION
    )
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
    journal_service = DecisionJournalService(store=store)
    journal_entry = DecisionJournalEntry(
        **_journal_entry_payload(
            decision_envelope.artifact_id,
            manifest_envelope.artifact_id,
            graph_fingerprint,
            symbol=ticker,
            direction=action,
            decision_at=DECISION_AT,
            logical_as_of=AS_OF,
        )
    )
    persisted_journal = journal_service.record_entry(journal_entry)
    return decision_envelope, manifest_envelope, persisted_journal


def _observation_payload(
    decision_artifact_id,
    journal_entry_id,
    symbol="AAPL",
    horizon=1,
    outcome_status=OutcomeStatus.COMPLETED,
    observed_future_price=105.0,
    decision_at=DECISION_AT,
    observation_at=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
    adjustment="raw",
    session="regular",
):
    return {
        "observation_id": sha256_fingerprint(
            {
                "schema_version": OUTCOME_SCHEMA_VERSION,
                "decision_artifact_id": decision_artifact_id,
                "journal_entry_id": journal_entry_id,
                "horizon": horizon,
                "decision_at": decision_at.isoformat(),
                "observation_at": observation_at.isoformat(),
                "adjustment": adjustment,
                "session": session,
            }
        ),
        "outcome_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_journal_entry_id": journal_entry_id,
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "observation_at": observation_at.isoformat(),
        "horizon": horizon,
        "evaluation_timeframe": "1d",
        "reference_price_methodology": ReferencePriceMethodology.DECISION_BAR_CLOSE.value,
        "observed_future_price_methodology": FuturePriceMethodology.OBSERVATION_BAR_CLOSE.value,
        "reference_price_at_decision": 100.0,
        "observed_future_price": observed_future_price,
        "market_data_provider": "mock",
        "adjustment": adjustment,
        "session": session,
        "outcome_status": outcome_status.value,
    }


def test_valid_outcome_observation_construction():
    payload = _observation_payload("a" * 64, "b" * 64)
    observation = OutcomeObservation(**payload)
    assert observation.symbol.root == "AAPL"
    assert observation.horizon == 1
    assert observation.outcome_status == OutcomeStatus.COMPLETED
    assert observation.observed_future_price == 105.0


def test_immutability():
    payload = _observation_payload("a" * 64, "b" * 64)
    observation = OutcomeObservation(**payload)
    with pytest.raises(ValidationError):
        observation.symbol = Symbol("MSFT")


def test_deterministic_identity():
    payload = _observation_payload("a" * 64, "b" * 64)
    first = OutcomeObservation(**payload)
    second = OutcomeObservation(**payload)
    assert first.fingerprint() == second.fingerprint()
    assert first.envelope().artifact_id == second.envelope().artifact_id


def test_decision_journal_linkage():
    store = InMemoryArtifactStore()
    decision, _manifest, journal = _setup_decision_chain(store)
    payload = _observation_payload(decision.artifact_id, journal.artifact_id)
    observation = OutcomeObservation(**payload)
    assert observation.decision_artifact_id == decision.artifact_id
    assert observation.decision_journal_entry_id == journal.artifact_id


def test_completed_evaluation():
    store = InMemoryArtifactStore()
    decision, _manifest, journal = _setup_decision_chain(store)
    observation_payload = _observation_payload(decision.artifact_id, journal.artifact_id)
    observation = OutcomeObservation(**observation_payload)
    service = OutcomeJournalService(store=store)
    persisted_observation = service.record_observation(observation)
    evaluation_envelope = service.evaluate_observation(persisted_observation.artifact_id, "BUY")
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)
    assert evaluation.outcome_status == OutcomeStatus.COMPLETED
    assert evaluation.raw_return == pytest.approx(0.05)
    assert evaluation.signed_return == pytest.approx(0.05)
    assert evaluation.directionally_correct is True


def test_direction_aware_positive_case():
    store = InMemoryArtifactStore()
    observation_payload = _observation_payload("a" * 64, "b" * 64)
    observation = OutcomeObservation(**observation_payload)
    service = OutcomeJournalService(store=store)
    persisted_observation = service.record_observation(observation)
    evaluation_envelope = service.evaluate_observation(persisted_observation.artifact_id, "BUY")
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)
    assert evaluation.signed_return > 0
    assert evaluation.directionally_correct is True


def test_direction_aware_negative_case():
    store = InMemoryArtifactStore()
    observation_payload = _observation_payload("a" * 64, "b" * 64, observed_future_price=95.0)
    observation = OutcomeObservation(**observation_payload)
    service = OutcomeJournalService(store=store)
    persisted_observation = service.record_observation(observation)
    evaluation_envelope = service.evaluate_observation(persisted_observation.artifact_id, "BUY")
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)
    assert evaluation.signed_return < 0
    assert evaluation.directionally_correct is False


def test_neutral_semantics():
    store = InMemoryArtifactStore()
    observation_payload = _observation_payload("a" * 64, "b" * 64)
    observation = OutcomeObservation(**observation_payload)
    service = OutcomeJournalService(store=store)
    persisted_observation = service.record_observation(observation)
    evaluation_envelope = service.evaluate_observation(persisted_observation.artifact_id, "WAIT")
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)
    assert evaluation.signed_return == 0.0
    assert evaluation.directionally_correct is True


def test_configurable_horizons():
    store = InMemoryArtifactStore()
    payload = _observation_payload("a" * 64, "b" * 64, horizon=20)
    observation = OutcomeObservation(**payload)
    assert observation.horizon == 20
    service = OutcomeJournalService(store=store)
    persisted = service.record_observation(observation)
    queried = service.get_observation(persisted.artifact_id)
    assert queried.horizon == 20
    assert service.query_observations(horizon=20) == [queried]


def test_pending_future_horizon():
    store = InMemoryArtifactStore()
    payload = _observation_payload(
        "a" * 64,
        "b" * 64,
        outcome_status=OutcomeStatus.PENDING,
        observed_future_price=None,
    )
    observation = OutcomeObservation(**payload)
    assert observation.outcome_status == OutcomeStatus.PENDING
    assert observation.observed_future_price is None
    service = OutcomeJournalService(store=store)
    persisted = service.record_observation(observation)
    queried = service.get_observation(persisted.artifact_id)
    assert queried.outcome_status == OutcomeStatus.PENDING


def test_missing_market_data():
    payload = _observation_payload(
        "a" * 64,
        "b" * 64,
        outcome_status=OutcomeStatus.DATA_UNAVAILABLE,
        observed_future_price=None,
    )
    observation = OutcomeObservation(**payload)
    assert observation.outcome_status == OutcomeStatus.DATA_UNAVAILABLE
    assert observation.observed_future_price is None


def test_temporal_violation():
    payload = _observation_payload(
        "a" * 64,
        "b" * 64,
        decision_at=datetime(2026, 8, 2, tzinfo=UTC),
        observation_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="observation_at must be after decision_at"):
        OutcomeObservation(**payload)


def test_wrong_artifact_type():
    store = InMemoryArtifactStore()
    observation = OutcomeObservation(**_observation_payload("a" * 64, "b" * 64))
    wrong_envelope = ArtifactEnvelope.create(
        payload=observation.model_dump(mode="json"),
        artifact_type=ArtifactType.TRADE_DECISION,
        logical_as_of=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier="0" * 64,
                description="unit test",
                producer="outcome-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(wrong_envelope)
    service = OutcomeJournalService(store=store)
    with pytest.raises(OutcomeJournalValidationError, match="wrong artifact type"):
        service._load_verified_artifact(wrong_envelope.artifact_id, ArtifactType.OUTCOME_OBSERVATION, "observation")


def test_corruption_propagation():
    store = InMemoryArtifactStore()
    observation = OutcomeObservation(**_observation_payload("a" * 64, "b" * 64))
    service = OutcomeJournalService(store=store)
    persisted = service.record_observation(observation)
    store._entries[persisted.artifact_id] = "not-json"
    with pytest.raises(ArtifactCorruptedError):
        service.get_observation(persisted.artifact_id)


def test_artifact_store_persistence():
    temp_dir = tempfile.mkdtemp(prefix="rap-outcome-")
    store = FileArtifactStore(temp_dir)
    decision, _manifest, journal = _setup_decision_chain(store)
    observation = OutcomeObservation(**_observation_payload(decision.artifact_id, journal.artifact_id))
    service = OutcomeJournalService(store=store)
    persisted_observation = service.record_observation(observation)
    persisted_evaluation = service.evaluate_observation(persisted_observation.artifact_id, "BUY")
    evaluation = OutcomeEvaluation.model_validate(persisted_evaluation.payload)

    reloaded = FileArtifactStore(temp_dir)
    restored_service = OutcomeJournalService(store=reloaded)
    assert restored_service.get_observation(persisted_observation.artifact_id).observation_id == observation.observation_id
    assert restored_service.get_evaluation(evaluation.evaluation_id).evaluation_id == evaluation.evaluation_id


def test_idempotency():
    store = InMemoryArtifactStore()
    observation = OutcomeObservation(**_observation_payload("a" * 64, "b" * 64))
    service = OutcomeJournalService(store=store)
    first = service.record_observation(observation)
    second = service.record_observation(observation)
    assert first.artifact_id == second.artifact_id


def test_deterministic_queries():
    store = InMemoryArtifactStore()
    _setup_decision_chain(store, ticker="AAPL", action="BUY")
    decision2, _manifest2, journal2 = _setup_decision_chain(store, ticker="MSFT", action="SELL")
    obs1 = OutcomeObservation(**_observation_payload("a" * 64, "b" * 64, symbol="AAPL"))
    obs2 = OutcomeObservation(
        **_observation_payload(decision2.artifact_id, journal2.artifact_id, symbol="MSFT", observed_future_price=90.0)
    )
    service = OutcomeJournalService(store=store)
    service.record_observation(obs1)
    service.record_observation(obs2)
    assert service.query_observations(symbol="AAPL") == [obs1]
    assert service.query_observations(symbol="MSFT") == [obs2]
    assert service.query_evaluations(outcome_status=OutcomeStatus.COMPLETED) == []


def test_restart_persistence():
    temp_dir = tempfile.mkdtemp(prefix="rap-outcome-restart-")
    store = FileArtifactStore(temp_dir)
    decision, _manifest, journal = _setup_decision_chain(store)
    observation = OutcomeObservation(**_observation_payload(decision.artifact_id, journal.artifact_id))
    service = OutcomeJournalService(store=store)
    persisted = service.record_observation(observation)
    reloaded = FileArtifactStore(temp_dir)
    restored = OutcomeJournalService(store=reloaded)
    assert restored.get_observation(persisted.artifact_id).observation_id == observation.observation_id


def test_adjusted_price_methodology():
    payload = _observation_payload("a" * 64, "b" * 64, adjustment="split_adjusted")
    observation = OutcomeObservation(**payload)
    assert observation.adjustment == "split_adjusted"
    assert observation.reference_price_methodology == ReferencePriceMethodology.DECISION_BAR_CLOSE


def test_t0_decision_unchanged_after_evaluation():
    store = InMemoryArtifactStore()
    decision, _manifest, journal = _setup_decision_chain(store)
    original_decision_payload = store.get(decision.artifact_id).payload
    observation = OutcomeObservation(**_observation_payload(decision.artifact_id, journal.artifact_id))
    service = OutcomeJournalService(store=store)
    persisted = service.record_observation(observation)
    service.evaluate_observation(persisted.artifact_id, "BUY")
    reloaded_decision = store.get(decision.artifact_id)
    assert reloaded_decision.payload == original_decision_payload


def test_no_direct_network_dependency():
    from app.services.outcome_journal import service as outcome_service_module

    with open(outcome_service_module.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "import yfinance" not in source


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
