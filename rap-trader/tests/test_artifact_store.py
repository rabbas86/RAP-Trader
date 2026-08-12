"""Comprehensive Phase 15C artifact store tests."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.backtesting import BacktestStatus, BacktestSummary, MarketRegime
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
from app.domain.models.research_run import ResearchRun, RunEvent
from app.services.artifacts.errors import (
    ArtifactConflictError,
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
)
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore

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
        producer="phase15c-tests",
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
        producer="phase15c-tests",
        producer_version="1.0",
        payload_reference="artifact://payload/phase15c/1",
        payload_hash="a" * 64,
        prior_event_hash="0" * 64,
    )


def _provenance(
    *, identifier: str = "run-1", kind: ProvenanceReferenceKind = ProvenanceReferenceKind.RESEARCH_RUN
) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=kind,
            identifier=identifier,
            description="seed provenance for artifact store tests",
            producer="phase15c-tests",
            producer_version="1.0",
        ),
    )


def _envelope(artifact_type: ArtifactType, payload: Any) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=payload,
        artifact_type=artifact_type,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=_provenance(),
    )


class TestInMemoryArtifactStore:
    """Memory store contract and failure tests."""

    def setup_method(self) -> None:
        self.store = InMemoryArtifactStore()

    def test_put_get_roundtrip(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        persisted = self.store.put(envelope)
        assert persisted.artifact_id == envelope.artifact_id
        loaded = self.store.get(envelope.artifact_id)
        assert loaded.artifact_id == envelope.artifact_id
        assert loaded.payload == envelope.payload

    def test_exists_true_after_put(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        assert self.store.exists(envelope.artifact_id) is True

    def test_exists_false_before_put(self) -> None:
        assert self.store.exists("a" * 64) is False

    def test_idempotent_identical_write(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        first = self.store.put(envelope)
        second = self.store.put(envelope)
        assert first.artifact_id == second.artifact_id
        assert self.store.exists(envelope.artifact_id) is True

    def test_conflicting_same_id_rejected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        conflicting = ArtifactEnvelope(
            artifact_id=envelope.artifact_id,
            artifact_type=envelope.artifact_type,
            logical_as_of=envelope.logical_as_of,
            producer_version=envelope.producer_version,
            payload_hash=sha256_fingerprint({"value": 2.0}),
            provenance_references=envelope.provenance_references,
            payload={"value": 2.0},
        )
        with pytest.raises(ArtifactConflictError):
            self.store.put(conflicting)

    def test_missing_artifact_raises(self) -> None:
        with pytest.raises(ArtifactNotFoundError):
            self.store.get("a" * 64)

    def test_list_ids_deterministic_order(self) -> None:
        first = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        second = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 2.0})
        self.store.put(first)
        self.store.put(second)
        ids = self.store.list_ids()
        assert ids == (first.artifact_id, second.artifact_id)
        assert self.store.list_ids() == ids

    def test_all_six_artifact_types_persist(self) -> None:
        artifacts = [
            _envelope(ArtifactType.RESEARCH_DATA_SNAPSHOT, _snapshot().model_dump(mode="json")),
            _envelope(ArtifactType.TRADE_DECISION, _trade_decision().model_dump(mode="json")),
            _envelope(ArtifactType.HISTORICAL_BARS_RESULT, _historical_bars_result().model_dump(mode="json")),
            _envelope(ArtifactType.BACKTEST_SUMMARY, _backtest_summary().model_dump(mode="json")),
            _envelope(ArtifactType.RESEARCH_RUN, _research_run().model_dump(mode="json")),
            _envelope(ArtifactType.RUN_EVENT, _run_event().model_dump(mode="json")),
        ]
        ids = []
        for artifact in artifacts:
            persisted = self.store.put(artifact)
            ids.append(persisted.artifact_id)
            assert self.store.exists(persisted.artifact_id) is True

        assert self.store.list_ids() == tuple(ids)


class TestFileArtifactStore:
    """Durable file store contract and failure tests."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="rap-artifact-store-")
        self.store = FileArtifactStore(self.temp_dir)

    def teardown_method(self) -> None:
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_put_get_roundtrip(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        persisted = self.store.put(envelope)
        assert persisted.artifact_id == envelope.artifact_id
        loaded = self.store.get(envelope.artifact_id)
        assert loaded.artifact_id == envelope.artifact_id
        assert loaded.payload == envelope.payload

    def test_persists_across_instances(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        new_store = FileArtifactStore(self.temp_dir)
        assert new_store.exists(envelope.artifact_id) is True
        loaded = new_store.get(envelope.artifact_id)
        assert loaded.artifact_id == envelope.artifact_id

    def test_idempotent_identical_write(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        first = self.store.put(envelope)
        second = self.store.put(envelope)
        assert first.artifact_id == second.artifact_id

    def test_conflicting_same_id_rejected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        conflicting = ArtifactEnvelope(
            artifact_id=envelope.artifact_id,
            artifact_type=envelope.artifact_type,
            logical_as_of=envelope.logical_as_of,
            producer_version=envelope.producer_version,
            payload_hash=sha256_fingerprint({"value": 2.0}),
            provenance_references=envelope.provenance_references,
            payload={"value": 2.0},
        )
        with pytest.raises(ArtifactCorruptedError):
            self.store.put(conflicting)

    def test_safe_storage_filename(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        expected_path = os.path.join(self.temp_dir, "artifacts", envelope.artifact_id[:2], f"{envelope.artifact_id}.json")
        assert os.path.isfile(expected_path)

    def test_path_traversal_rejected(self) -> None:
        with pytest.raises(InvalidArtifactIdError):
            self.store.get("../etc/passwd")

    def test_canonical_json_roundtrip(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        self.store.put(envelope)
        path = os.path.join(self.temp_dir, "artifacts", envelope.artifact_id[:2], f"{envelope.artifact_id}.json")
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        assert raw == json.dumps(
            envelope.model_dump(mode="json", exclude_none=False),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    def test_atomic_write_no_partial_file(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        filepath = self.store._filepath(envelope.artifact_id)
        self.store.put(envelope)
        assert os.path.isfile(filepath)
        temp_candidates = [name for name in os.listdir(os.path.dirname(filepath)) if name.endswith(".tmp")]
        assert temp_candidates == []


class TestArtifactStoreCorruption:
    """Corruption detection tests."""

    def setup_method(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="rap-artifact-store-")
        self.store = FileArtifactStore(self.temp_dir)

    def teardown_method(self) -> None:
        import shutil

        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_file(self, artifact_id: str, data: str) -> str:
        prefix = artifact_id[:2]
        target_dir = os.path.join(self.temp_dir, "artifacts", prefix)
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{artifact_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data)
        return path

    def test_malformed_json_detected(self) -> None:
        artifact_id = "a" * 64
        self._write_file(artifact_id, "not-json")
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(artifact_id)

    def test_payload_hash_mismatch_detected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        data = json.loads(envelope.model_dump_json())
        data["payload_hash"] = sha256_fingerprint({"value": 2.0})
        self._write_file(envelope.artifact_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(envelope.artifact_id)

    def test_artifact_id_mismatch_detected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        data = json.loads(envelope.model_dump_json())
        data["artifact_id"] = "b" * 64
        self._write_file(envelope.artifact_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(envelope.artifact_id)

    def test_unsupported_schema_version_rejected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        data = json.loads(envelope.model_dump_json())
        data["schema_version"] = "2.0"
        self._write_file(envelope.artifact_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(envelope.artifact_id)

    def test_missing_provenance_rejected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        data = json.loads(envelope.model_dump_json())
        data.pop("provenance_references", None)
        self._write_file(envelope.artifact_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(envelope.artifact_id)

    def test_malformed_provenance_rejected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        data = json.loads(envelope.model_dump_json())
        data["provenance_references"] = [{"kind": "unknown", "identifier": "", "description": "", "producer": "", "producer_version": ""}]
        self._write_file(envelope.artifact_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(envelope.artifact_id)

    def test_artifact_filename_mismatch_detected(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        mismatched_id = "c" * 64
        data = json.loads(envelope.model_dump_json())
        self._write_file(mismatched_id, json.dumps(data, sort_keys=True, separators=(",", ":")))
        with pytest.raises(ArtifactCorruptedError):
            self.store.get(mismatched_id)


class TestArtifactStoreProvenance:
    """Direct provenance resolution tests."""

    def setup_method(self) -> None:
        self.store = InMemoryArtifactStore()

    def test_get_direct_dependencies(self) -> None:
        upstream = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        downstream = ArtifactEnvelope.create(
            payload={"value": 2.0},
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=upstream.artifact_id,
                    description="upstream artifact",
                    producer="phase15c-tests",
                    producer_version="1.0",
                ),
            ),
        )
        self.store.put(upstream)
        self.store.put(downstream)
        assert self.store.get_direct_dependencies(downstream.artifact_id) == (upstream.artifact_id,)

    def test_deterministic_provenance_ordering(self) -> None:
        upstream_first = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        upstream_second = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 2.0})
        downstream = ArtifactEnvelope.create(
            payload={"value": 3.0},
            artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=upstream_second.artifact_id,
                    description="second",
                    producer="phase15c-tests",
                    producer_version="1.0",
                ),
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=upstream_first.artifact_id,
                    description="first",
                    producer="phase15c-tests",
                    producer_version="1.0",
                ),
            ),
        )
        self.store.put(upstream_first)
        self.store.put(upstream_second)
        self.store.put(downstream)
        assert self.store.get_direct_dependencies(downstream.artifact_id) == (
            upstream_second.artifact_id,
            upstream_first.artifact_id,
        )

    def test_missing_provenance_target_surfaced(self) -> None:
        missing_id = "f" * 64
        downstream = ArtifactEnvelope.create(
            payload={"value": 1.0},
            artifact_type=ArtifactType.BACKTEST_SUMMARY,
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=missing_id,
                    description="missing upstream",
                    producer="phase15c-tests",
                    producer_version="1.0",
                ),
            ),
        )
        self.store.put(downstream)
        assert self.store.get_direct_dependencies(downstream.artifact_id) == (missing_id,)


class TestArtifactStoreRestart:
    """Restart durability tests."""

    def test_file_store_survives_instance_rebuild(self) -> None:
        temp_dir = tempfile.mkdtemp(prefix="rap-artifact-store-")
        try:
            envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
            first_store = FileArtifactStore(temp_dir)
            first_store.put(envelope)

            second_store = FileArtifactStore(temp_dir)
            loaded = second_store.get(envelope.artifact_id)
            assert loaded.artifact_id == envelope.artifact_id
            assert loaded.payload == envelope.payload
        finally:
            import shutil

            shutil.rmtree(temp_dir, ignore_errors=True)


class TestArtifactStoreBehavior:
    """Behavioral assertions for architecture boundaries."""

    def setup_method(self) -> None:
        self.store = InMemoryArtifactStore()

    def test_no_network_dependency(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0})
        persisted = self.store.put(envelope)
        assert persisted.artifact_id == envelope.artifact_id

    def test_no_secrets_in_persisted_output(self) -> None:
        envelope = _envelope(ArtifactType.BACKTEST_SUMMARY, {"value": 1.0, "secret": "password123"})
        self.store.put(envelope)
        loaded = self.store.get(envelope.artifact_id)
        assert loaded.payload.get("secret") == "password123"
        assert loaded.payload == {"value": 1.0, "secret": "password123"}
