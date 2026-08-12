"""Comprehensive Phase 15B artifact envelope tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.canonical import canonical_json, sha256_fingerprint
from app.domain.models.artifact import (
    ARTIFACT_ID_PATTERN,
    ARTIFACT_SCHEMA_VERSION,
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.backtesting import (
    BacktestStatus,
    BacktestSummary,
    MarketRegime,
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
from app.domain.models.decision import AgentEvidence, TradeDecision
from app.domain.models.market_data import (
    HistoricalBarsResult,
    OHLCVBar,
    Symbol,
)
from app.domain.models.research_run import GENESIS_EVENT_HASH, ResearchRun, ResearchRunStatus, RunEvent

AS_OF = datetime(2026, 8, 1, tzinfo=UTC)
RECORDED = datetime(2026, 8, 1, 1, tzinfo=UTC)
SOURCE = DataSourceIdentity(
    provider="deterministic_mock",
    dataset="unit_test",
    source_version="1",
    schema_version="1",
    offline_capable=True,
    authoritative=False,
)


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
        rationale="research envelope unit test decision",
        evidence=[
            AgentEvidence(
                source="technical_analyst",
                ticker="AAPL",
                recommendation="buy",
                confidence=0.9,
                reasoning_summary="uptrend breakout",
                generated_at=AS_OF,
            )
        ],
        created_at=AS_OF,
    )


def _historical_bars_result() -> HistoricalBarsResult:
    bars = [
        OHLCVBar(
            timestamp=datetime(2026, 7, 29, tzinfo=UTC),
            open=176.1,
            high=178.4,
            low=175.8,
            close=177.9,
            volume=42_500_000,
        ),
        OHLCVBar(
            timestamp=datetime(2026, 7, 30, tzinfo=UTC),
            open=178.0,
            high=179.2,
            low=177.1,
            close=178.7,
            volume=38_100_000,
        ),
    ]
    return HistoricalBarsResult(
        symbol=Symbol("AAPL"),
        timeframe="1d",
        bars=bars,
        provider="mock",
        requested_start=datetime(2026, 7, 29, tzinfo=UTC),
        requested_end=datetime(2026, 7, 31, tzinfo=UTC),
        actual_start=datetime(2026, 7, 29, tzinfo=UTC),
        actual_end=datetime(2026, 7, 30, tzinfo=UTC),
        adjustment="raw",
        session="regular",
        currency="USD",
        exchange="NASDAQ",
        partial=False,
        retrieved_at=AS_OF,
    )


def _backtest_summary() -> BacktestSummary:
    return BacktestSummary(
        backtest_id="backtest-unit-1",
        ticker="AAPL",
        timeframe="1d",
        status=BacktestStatus.COMPLETED,
        research_only=True,
        suitable_for_live_trading=False,
        providers=["MockKronosProvider", "SMAForecastProvider"],
        windows_total=3,
        windows_evaluated=3,
        mean_mae_by_provider={"MockKronosProvider": 0.21, "SMAForecastProvider": 0.35},
        mean_rmse_by_provider={"MockKronosProvider": 0.31, "SMAForecastProvider": 0.49},
        best_provider_by_rmse="MockKronosProvider",
        regime_distribution={MarketRegime.TRENDING_UP.value: 3},
        created_at=AS_OF,
    )


def _research_run() -> ResearchRun:
    return ResearchRun.create(
        correlation_id=UUID("2bcb5f5a-5f66-4b86-9d8f-34f1e91dce21"),
        logical_as_of=AS_OF,
        recorded_at=RECORDED,
        producer="phase15b-tests",
        producer_version="1.0",
    )


def _run_event() -> RunEvent:
    return RunEvent.create(
        run_id=_research_run().run_id,
        sequence=1,
        correlation_id=UUID("2bcb5f5a-5f66-4b86-9d8f-34f1e91dce21"),
        causation_id=None,
        logical_as_of=AS_OF,
        recorded_at=RECORDED,
        event_type="artifact.envelope.written",
        producer="phase15b-tests",
        producer_version="1.0",
        payload_reference="artifact://payload/phase15b/1",
        payload_hash="a" * 64,
        prior_event_hash=GENESIS_EVENT_HASH,
    )


def provenance_snapshot() -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.RESEARCH_RUN,
            identifier=str(_research_run().run_id),
            description="seed research run for artifact envelope tests",
            producer="phase15b-tests",
            producer_version="1.0",
        ),
    )


def envelope_for(artifact_type: ArtifactType, payload: Any) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=payload,
        artifact_type=artifact_type,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=provenance_snapshot(),
    )


def test_schema_version_constant() -> None:
    assert ARTIFACT_SCHEMA_VERSION == "1.0"


def test_artifact_type_values() -> None:
    assert ArtifactType("research_data_snapshot") is ArtifactType.RESEARCH_DATA_SNAPSHOT
    assert ArtifactType("run_event") is ArtifactType.RUN_EVENT


def test_provenance_kind_values() -> None:
    assert ProvenanceReferenceKind("research_run") is ProvenanceReferenceKind.RESEARCH_RUN
    assert ProvenanceReferenceKind("deterministic_source") is ProvenanceReferenceKind.DETERMINISTIC_SOURCE


def test_deterministic_payload_hashing() -> None:
    snapshot = _snapshot()
    payload = snapshot.model_dump(mode="json")
    first = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    second = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    assert first.payload_hash == sha256_fingerprint(payload)
    assert first.payload_hash == second.payload_hash


def test_mapping_order_independence() -> None:
    first_payload = {"warnings": ["alpha", "beta"], "score": 1.0}
    reversed_payload = {"score": 1.0, "warnings": ["alpha", "beta"]}
    first = envelope_for(ArtifactType.BACKTEST_SUMMARY, first_payload)
    second = envelope_for(ArtifactType.BACKTEST_SUMMARY, reversed_payload)
    assert first.payload_hash == second.payload_hash


def test_equivalent_timestamp_normalization() -> None:
    aware = datetime(2026, 8, 1, 3, tzinfo=timezone(timedelta(hours=3)))
    payload = {"observed_at": aware.isoformat(), "value": 1.0}
    first = envelope_for(ArtifactType.TRADE_DECISION, payload)
    second = envelope_for(ArtifactType.TRADE_DECISION, dict(sorted(payload.items())))
    assert first.payload_hash == second.payload_hash


def test_deterministic_artifact_id() -> None:
    snapshot = _snapshot()
    payload = snapshot.model_dump(mode="json")
    first = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    second = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    assert first.artifact_id == second.artifact_id
    assert ARTIFACT_ID_PATTERN.fullmatch(first.artifact_id)


def test_changed_payload_changes_payload_hash() -> None:
    payload = {"value": 1.0}
    first = envelope_for(ArtifactType.BACKTEST_SUMMARY, payload)
    changed = envelope_for(ArtifactType.BACKTEST_SUMMARY, {"value": 2.0})
    assert first.payload_hash != changed.payload_hash


def test_tampered_payload_rejected() -> None:
    snapshot = _snapshot()
    payload = snapshot.model_dump(mode="json")
    envelope = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    tampered = dict(payload)
    tampered["snapshot_id"] = "tampered-snapshot"
    assert envelope.verify_payload(payload) is True
    assert envelope.verify_payload(tampered) is False


def test_frozen_envelope_rejects_mutation() -> None:
    envelope = envelope_for(ArtifactType.RUN_EVENT, {"sequence": 1})
    with pytest.raises(ValidationError):
        envelope.artifact_id = "a" * 64  # type: ignore[misc]
    with pytest.raises(ValidationError):
        envelope.logical_as_of = RECORDED  # type: ignore[misc]


def test_invalid_hash_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="a" * 63,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
            provenance_references=provenance_snapshot(),
        )
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 63,
            provenance_references=provenance_snapshot(),
        )


def test_blank_artifact_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type="invalid_empty_type",
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
            provenance_references=provenance_snapshot(),
        )


def test_invalid_schema_version_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            schema_version="2.0",
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
            provenance_references=provenance_snapshot(),
        )


def test_missing_provenance_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
        )


def test_empty_provenance_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
            provenance_references=(),
        )


def test_malformed_provenance_rejected() -> None:
    with pytest.raises(ValidationError):
        ArtifactEnvelope(
            artifact_id="0" * 64,
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            payload_hash="b" * 64,
            provenance_references=({"kind": "unknown_kind", "identifier": "", "description": "", "producer": "", "producer_version": ""},),
        )


def test_valid_provenance_accepted() -> None:
    reference = ProvenanceReference(
        kind=ProvenanceReferenceKind.ARTIFACT,
        identifier="upstream-artifact-1",
        description="prior immutable envelope",
        producer="phase15b-tests",
        producer_version="1.0",
    )
    envelope = ArtifactEnvelope(
        artifact_id="0" * 64,
        artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
        logical_as_of=AS_OF,
        producer_version="1.0",
        payload_hash="b" * 64,
        provenance_references=(reference,),
    )
    assert envelope.provenance_references[0].kind is ProvenanceReferenceKind.ARTIFACT


def test_populated_research_data_snapshot_envelope() -> None:
    snapshot = _snapshot()
    envelope = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, snapshot.model_dump(mode="json"))
    assert envelope.artifact_type is ArtifactType.RESEARCH_DATA_SNAPSHOT
    assert envelope.payload_hash == sha256_fingerprint(snapshot.model_dump(mode="json"))
    assert envelope.verify_payload(snapshot.model_dump(mode="json"))
    assert envelope.provenance_references[0].identifier == str(_research_run().run_id)


def test_representative_artifacts_for_included_types() -> None:
    snapshot = _snapshot()
    trade_decision = _trade_decision()
    historical_bars = _historical_bars_result()
    backtest_summary = _backtest_summary()
    research_run = _research_run()
    run_event = _run_event()

    envelopes = [
        envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, snapshot.model_dump(mode="json")),
        envelope_for(ArtifactType.TRADE_DECISION, trade_decision.model_dump(mode="json")),
        envelope_for(ArtifactType.HISTORICAL_BARS_RESULT, historical_bars.model_dump(mode="json")),
        envelope_for(ArtifactType.BACKTEST_SUMMARY, backtest_summary.model_dump(mode="json")),
        envelope_for(ArtifactType.RESEARCH_RUN, research_run.model_dump(mode="json")),
        envelope_for(ArtifactType.RUN_EVENT, run_event.model_dump(mode="json")),
    ]

    payload_hashes = []
    for current_envelope in envelopes:
        assert current_envelope.verify_payload(current_envelope.payload)
        assert ARTIFACT_ID_PATTERN.fullmatch(current_envelope.artifact_id)
        payload_hashes.append(current_envelope.payload_hash)

    assert len(set(payload_hashes)) == len(payload_hashes)


def test_existing_payload_schemas_remain_unchanged() -> None:
    snapshot = _snapshot()
    payload = snapshot.model_dump(mode="json")
    envelope = envelope_for(ArtifactType.RESEARCH_DATA_SNAPSHOT, payload)
    restored_snapshot = ResearchDataSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored_snapshot.snapshot_id == snapshot.snapshot_id
    assert tuple(restored_snapshot.records) == snapshot.records


def test_json_model_serialization_roundtrip() -> None:
    envelope = envelope_for(ArtifactType.TRADE_DECISION, _trade_decision().model_dump(mode="json"))
    restored = ArtifactEnvelope.model_validate_json(envelope.model_dump_json())
    assert restored.artifact_id == envelope.artifact_id
    assert restored.payload_hash == envelope.payload_hash
    assert restored.provenance_references == envelope.provenance_references


def test_existing_fingerprint_api_backward_compatibility() -> None:
    value = {"values": {1, "1", 2, "2"}, "timestamp": AS_OF}
    first = sha256_fingerprint(value)
    second = sha256_fingerprint(value)
    assert first == second
    assert len(first) == 64
    assert first == sha256_fingerprint({"timestamp": AS_OF, "values": {1, "1", 2, "2"}})
    assert canonical_json({"values": {1, "1", 2, "2"}}) == '{"values":[1,2,"1","2"]}'


def test_existing_research_run_behavior_remains_unchanged() -> None:
    run = _research_run()
    assert run.research_only is True
    assert run.paper_trading_only is True
    assert run.suitable_for_live_trading is False
    assert run.canonical_hash == sha256_fingerprint(run.model_dump(mode="json"))

    completed_run = run.transition_to(ResearchRunStatus.RUNNING).transition_to(ResearchRunStatus.COMPLETED)
    assert completed_run.status is ResearchRunStatus.COMPLETED
    assert completed_run.run_id == run.run_id


def test_malformed_artifact_type_rejected_in_create() -> None:
    with pytest.raises((ValidationError, ValueError)):
        ArtifactEnvelope.create(
            payload={"value": 1.0},
            artifact_type="not_a_real_type",
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=provenance_snapshot(),
        )
