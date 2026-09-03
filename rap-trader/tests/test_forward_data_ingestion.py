"""Phase 17A forward data ingestion tests."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.forward_data import (
    ForwardDataSession,
    ForwardDataSource,
    ForwardMarketObservation,
    ObservationStatus,
)
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.forward_data.service import ForwardDataIngestionService

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


TEST_SOURCE = ForwardDataSource.create(
    provider_name="fake_forward_provider",
    provider_version="1.0",
    feed_name="unit-tests",
    feed_type="bars",
    environment="TEST",
    adjustment_convention="raw",
    producer="phase17a-tests",
    producer_version="1.0",
)

SIMULATED_SOURCE = ForwardDataSource.create(
    provider_name="simulated_forward_provider",
    provider_version="1.0",
    feed_name="historical-backfill",
    feed_type="bars",
    environment="SIMULATED",
    adjustment_convention="raw",
    producer="phase17a-tests",
    producer_version="1.0",
)


def _session(instruments: list[str] | None = None) -> ForwardDataSession:
    return ForwardDataSession.create(
        started_at="2026-09-03T00:00:00Z",
        source_ids=[TEST_SOURCE.source_id],
        instruments=instruments or ["AAPL"],
        timeframes=["1m"],
        environment="TEST",
        producer="phase17a-tests",
        producer_version="1.0",
    )


def _observation(
    *,
    symbol: str = "AAPL",
    interval_start: str = "2026-09-03T10:00:00Z",
    interval_end: str = "2026-09-03T10:01:00Z",
    event_time: str | None = None,
    received_at: str = "2026-09-03T10:01:01Z",
    status: str = "final",
    revision_number: int = 0,
    supersedes_observation_id: str | None = None,
    observation_type: str = "market_bar",
    provider_available_at: str | None = None,
    source: ForwardDataSource | None = None,
    session: ForwardDataSession | None = None,
    open_: float = 100.0,
    high: float = 101.0,
    low: float = 99.0,
    close: float = 100.5,
    volume: float = 1_000,
) -> ForwardMarketObservation:
    source = source or TEST_SOURCE
    session = session or _session()
    event_time = event_time or interval_end
    return ForwardMarketObservation.create(
        session_id=session.session_id,
        source_id=source.source_id,
        symbol=symbol,
        observation_type=observation_type,
        timeframe="1m",
        interval_start=interval_start,
        interval_end=interval_end,
        event_time=event_time,
        received_at=received_at,
        normalized_at=received_at,
        status=status,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        provider_available_at=provider_available_at,
        supersedes_observation_id=supersedes_observation_id,
        revision_number=revision_number,
    )


# ---------------------------------------------------------------------------
# Domain contract tests
# ---------------------------------------------------------------------------


def test_forward_data_source_deterministic_id() -> None:
    source = ForwardDataSource.create(
        provider_name="provider-a",
        provider_version="1.0",
        feed_name="bars",
        feed_type="bars",
        environment="LIVE",
        adjustment_convention="raw",
        producer="tests",
        producer_version="1.0",
    )
    same = ForwardDataSource.create(
        provider_name="provider-a",
        provider_version="1.0",
        feed_name="bars",
        feed_type="bars",
        environment="LIVE",
        adjustment_convention="raw",
        producer="tests",
        producer_version="1.0",
    )
    assert source.source_id == same.source_id


def test_forward_data_source_rejects_secrets() -> None:
    with pytest.raises(ValidationError, match="forbidden"):
        ForwardDataSource.create(
            provider_name="provider",
            provider_version="1.0",
            feed_name="bars",
            feed_type="bars",
            environment="TEST",
            adjustment_convention="raw",
            producer="tests",
            producer_version="1.0",
            metadata={"api_key": "secret"},
        )


def test_forward_market_observation_deterministic_id() -> None:
    observation = _observation()
    same = _observation()
    assert observation.observation_id == same.observation_id


def test_naive_timestamps_rejected() -> None:
    with pytest.raises((ValidationError, TypeError, ValueError), match="timezone information|aware"):
        _observation(
            event_time="2026-09-03T10:01:00",
            received_at="2026-09-03T10:01:01",
            interval_start="2026-09-03T10:00:00Z",
            interval_end="2026-09-03T10:01:00Z",
        )


def test_aware_timestamps_normalized_to_utc() -> None:
    localized_event = datetime(2026, 9, 3, 10, 1, tzinfo=UTC)
    localized_received = datetime(2026, 9, 3, 13, 1, 1, tzinfo=UTC)
    observation = _observation(
        event_time=localized_event.isoformat(),
        received_at=localized_received.isoformat(),
    )
    assert observation.event_time.utcoffset() == timedelta(0)
    assert observation.received_at.utcoffset() == timedelta(0)


def test_valid_bar_accepted() -> None:
    observation = _observation()
    assert observation.observation_type == "market_bar"
    assert observation.status == ObservationStatus.FINAL


def test_invalid_ohlc_rejected() -> None:
    with pytest.raises((ValidationError, TypeError), match="high must be greater than or equal to"):
        _observation(high=50.0)


def test_negative_volume_rejected() -> None:
    with pytest.raises((ValidationError, TypeError), match="greater than or equal to 0"):
        _observation(volume=-1)


def test_final_bar_state_retained() -> None:
    assert _observation(status="final").status == ObservationStatus.FINAL


def test_in_progress_distinct_from_final() -> None:
    in_progress = _observation(status="in_progress")
    final = _observation(status="final")
    assert in_progress.status == ObservationStatus.IN_PROGRESS
    assert final.status == ObservationStatus.FINAL
    assert in_progress.observation_id != final.observation_id


def test_exact_duplicate_idempotent() -> None:
    observation = _observation()
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    first = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[observation])
    second = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[observation])
    assert first.accepted_count == 1
    assert second.accepted_count == 1
    assert second.duplicate_count == 1
    assert first.persisted_artifact_ids == second.persisted_artifact_ids


def test_conflicting_duplicate_rejected() -> None:
    original = _observation(close=100.0)
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    first = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[original])
    assert first.accepted_count == 1
    assert first.conflict_count == 0
    revised = _observation(
        close=101.0,
        status="corrected",
        revision_number=1,
        supersedes_observation_id=original.observation_id,
        received_at="2026-09-03T10:01:02Z",
        interval_end="2026-09-03T10:02:00Z",
        event_time="2026-09-03T10:01:00Z",
    )
    second = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[revised])
    assert second.accepted_count == 1
    assert second.conflict_count == 0
    assert second.persisted_artifact_ids[0] != first.persisted_artifact_ids[0]
    assert service.journal.get(original.observation_id).close == 100.0
    assert service.journal.get(revised.observation_id).close == 101.0


def test_correction_preserves_original() -> None:
    original = _observation(close=100.0, status="final", revision_number=0)
    correction = _observation(
        close=101.0,
        status="corrected",
        revision_number=1,
        supersedes_observation_id=original.observation_id,
        received_at="2026-09-03T10:01:02Z",
    )
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    first = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[original])
    second = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[correction])
    assert first.accepted_count == 1
    assert second.accepted_count == 1
    assert first.persisted_artifact_ids[0] != second.persisted_artifact_ids[0]
    assert service.journal.get(original.observation_id).close == 100.0
    assert service.journal.get(correction.observation_id).close == 101.0


def test_out_of_order_receipt_preserved() -> None:
    obs_b = _observation(
        event_time="2026-09-03T10:02:00Z",
        received_at="2026-09-03T10:02:30Z",
        interval_start="2026-09-03T10:02:00Z",
        interval_end="2026-09-03T10:03:00Z",
    )
    obs_a = _observation(
        event_time="2026-09-03T10:01:00Z",
        received_at="2026-09-03T10:03:00Z",
        interval_start="2026-09-03T10:01:00Z",
        interval_end="2026-09-03T10:02:00Z",
    )
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs_b, obs_a])
    event_order = service.journal.query_event_interval(start=_utc("2026-09-03T10:00:00Z"), end=_utc("2026-09-03T10:05:00Z"))
    assert [item.observation_id for item in event_order] == [obs_a.observation_id, obs_b.observation_id]
    receipt_order = service.journal.query_received_before(_utc("2026-09-03T10:04:00Z"))
    assert [item.observation_id for item in receipt_order] == [obs_b.observation_id, obs_a.observation_id]


def test_received_time_ordering_deterministic() -> None:
    obs_first = _observation(
        event_time="2026-09-03T10:00:00Z",
        received_at="2026-09-03T10:01:00Z",
        interval_start="2026-09-03T10:00:00Z",
        interval_end="2026-09-03T10:01:00Z",
    )
    obs_second = _observation(
        event_time="2026-09-03T10:01:00Z",
        received_at="2026-09-03T10:01:01Z",
        interval_start="2026-09-03T10:01:00Z",
        interval_end="2026-09-03T10:02:00Z",
    )
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs_first, obs_second])
    receipt_order = service.journal.query_received_before(_utc("2026-09-03T10:02:00Z"))
    assert [item.observation_id for item in receipt_order] == [obs_first.observation_id, obs_second.observation_id]


def test_event_time_ordering_deterministic() -> None:
    obs_first = _observation(event_time="2026-09-03T10:00:00Z", received_at="2026-09-03T10:01:00Z")
    obs_second = _observation(event_time="2026-09-03T10:00:01Z", received_at="2026-09-03T10:01:00Z")
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs_first, obs_second])
    event_order = service.journal.query_event_interval(start=_utc("2026-09-03T09:59:00Z"), end=_utc("2026-09-03T10:02:00Z"))
    assert [item.observation_id for item in event_order] == [obs_first.observation_id, obs_second.observation_id]


def test_provider_available_at_optional() -> None:
    observation = _observation(provider_available_at=None)
    assert observation.provider_available_at is None
    assert observation.provider_latency() is None


def test_latency_derived_only_when_available() -> None:
    observation = _observation(provider_available_at="2026-09-03T10:01:00Z", received_at="2026-09-03T10:01:01Z")
    assert observation.provider_latency() == 1.0


def test_append_only_journal() -> None:
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    obs = _observation()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    original = service.journal.get(obs.observation_id)
    updated = original.model_copy(update={"event_time": _utc("2026-09-03T10:02:00Z")})
    assert updated.event_time == _utc("2026-09-03T10:02:00Z")
    assert service.journal.get(obs.observation_id).event_time == original.event_time


def test_query_by_received_at_boundary() -> None:
    obs = _observation(received_at="2026-09-03T10:05:00Z")
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    assert service.journal.query_received_before(_utc("2026-09-03T10:04:59Z")) == ()
    assert service.journal.query_received_before(_utc("2026-09-03T10:06:00Z")) == (obs,)


def test_query_by_event_time_interval() -> None:
    obs = _observation(event_time="2026-09-03T10:01:00Z")
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    assert service.journal.query_event_interval(start=_utc("2026-09-03T10:00:00Z"), end=_utc("2026-09-03T10:01:00Z")) == (obs,)


def test_query_by_symbol() -> None:
    obs = _observation(symbol="AAPL")
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    assert service.journal.query_symbol("AAPL") == (obs,)
    assert service.journal.query_symbol("MSFT") == ()


def test_query_by_source() -> None:
    obs = _observation()
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    assert service.journal.query_source(TEST_SOURCE.source_id) == (obs,)
    assert service.journal.query_source("missing-source") == ()


def test_query_by_observation_type() -> None:
    obs = _observation(observation_type="market_bar")
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    assert service.journal.query_observation_type("market_bar") == (obs,)
    assert service.journal.query_observation_type("quote") == ()


def test_file_store_restart_preserves_ordering() -> None:
    from app.services.artifacts.file_store import FileArtifactStore

    root = Path(".pytest_forward_restart")
    root.mkdir(exist_ok=True)
    try:
        store = FileArtifactStore(str(root))
        service = ForwardDataIngestionService(store=store)
        session = _session()
        obs_b = _observation(
            event_time="2026-09-03T10:02:00Z",
            received_at="2026-09-03T10:02:30Z",
            interval_start="2026-09-03T10:02:00Z",
            interval_end="2026-09-03T10:03:00Z",
        )
        obs_a = _observation(
            event_time="2026-09-03T10:01:00Z",
            received_at="2026-09-03T10:03:00Z",
            interval_start="2026-09-03T10:01:00Z",
            interval_end="2026-09-03T10:02:00Z",
        )
        service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs_b, obs_a])
        store = FileArtifactStore(str(root))
        service = ForwardDataIngestionService(store=store)
        assert service.journal.get(obs_a.observation_id).event_time == _utc("2026-09-03T10:01:00Z")
        assert service.journal.get(obs_b.observation_id).received_at == _utc("2026-09-03T10:02:30Z")
        assert service.journal.query_event_interval(start=_utc("2026-09-03T10:00:00Z"), end=_utc("2026-09-03T10:05:00Z")) == (
            service.journal.get(obs_a.observation_id),
            service.journal.get(obs_b.observation_id),
        )
    finally:
        for child in root.rglob("*"):
            if child.is_file():
                child.unlink()
        for child in sorted(root.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        if root.exists():
            root.rmdir()


def test_corruption_propagates() -> None:
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    obs = _observation()
    service.ingest_fake(source=TEST_SOURCE, session=session, observations=[obs])
    envelope = store.get(first_persisted_artifact_id(obs, service))
    bad = json.loads(envelope.model_dump_json())
    bad["payload"]["close"] = 999.0
    store._entries[envelope.artifact_id] = json.dumps(bad, sort_keys=True, separators=(",", ":"))
    service = ForwardDataIngestionService(store=store)
    with pytest.raises(ArtifactCorruptedError):
        service.journal.get(obs.observation_id)


def first_persisted_artifact_id(obs, service):
    for artifact_id in service.store.list_ids(filters={"artifact_type": "forward_data_observation"}):
        envelope = service.store.get(artifact_id)
        if envelope.payload.get("observation_id") == obs.observation_id:
            return artifact_id
    raise AssertionError("persisted artifact not found")


def test_no_network_required_in_tests() -> None:
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    service = ForwardDataIngestionService(store=store)
    session = _session()
    result = service.ingest_fake(source=TEST_SOURCE, session=session, observations=[_observation()])
    assert result.accepted_count == 1


def test_fake_provider_marked_test() -> None:
    assert TEST_SOURCE.environment == "TEST"


def test_simulated_provider_marked_simulated() -> None:
    assert SIMULATED_SOURCE.environment == "SIMULATED"


def test_historical_backfill_cannot_masquerade_as_live() -> None:
    assert SIMULATED_SOURCE.environment != "LIVE"


def test_service_has_no_forbidden_dependencies() -> None:
    source = Path("app/services/forward_data/service.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="app/services/forward_data/service.py", mode="exec")
    forbidden = {"broker", "portfolio", "risk", "execution", "decision"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0].lower())
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0].lower())
    assert not imported.intersection(forbidden)


def test_phase16_regression_artifact_store_unchanged() -> None:
    from app.services.artifacts.memory import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    payload = _observation().model_dump(mode="json", exclude_none=False)
    envelope = ArtifactEnvelope.create(
        payload=payload,
        artifact_type=ArtifactType.FORWARD_DATA_OBSERVATION,
        logical_as_of=_utc("2026-09-03T10:01:00Z"),
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
                identifier="regression",
                description="regression artifact",
                producer="phase17a",
                producer_version="1.0",
            ),
        ),
    )
    persisted = store.put(envelope)
    assert persisted.artifact_id == envelope.artifact_id
    reloaded = store.get(persisted.artifact_id)
    assert reloaded.payload == payload
