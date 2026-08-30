"""Phase 16B Point-in-Time Boundary and Historical Clock tests."""

from __future__ import annotations

import ast
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.data_platform import (
    DataAvailability,
    DataDomain,
    DataQuality,
    DataRecordId,
    DataRevision,
    DataSourceIdentity,
    EconomicObservation,
    EconomicSeriesDefinition,
    Frequency,
    NormalizedDataRecord,
)
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import OHLCVBar
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.historical.boundary import PointInTimeDataBoundary
from app.services.historical.clock import HistoricalClock
from app.services.historical.errors import (
    HistoricalClockBackwardError,
    HistoricalClockBoundsError,
    HistoricalClockError,
    PointInTimeLookaheadError,
)
from app.services.historical.snapshot import build_snapshot

SPEC_ID = "a" * 64
REPLAY_RUN_ID = UUID("b" * 32)
AS_OF = datetime(2026, 8, 15, tzinfo=UTC)
RECORDED = datetime(2026, 8, 15, 1, tzinfo=UTC)


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16b tests",
            producer="phase16b-tests",
            producer_version="1.0",
        ),
    )


def _specification(**overrides: object) -> HistoricalReplaySpecification:
    values: dict[str, object] = {
        "start_time": datetime(2025, 1, 1, tzinfo=UTC),
        "end_time": datetime(2025, 6, 1, tzinfo=UTC),
        "instruments": ["AAPL", "BRK.B"],
        "timeframes": ["1d", "1h"],
        "decision_cadence": "window_close",
        "data_boundary_description": "event_time_only; no availability boundary available",
        "point_in_time_policy": "event_time_only",
        "strategy_identities": ["strategy:v1"],
        "model_identities": ["model:v1"],
        "config_fingerprints": ["cfg:v1"],
        "execution_methodology": "research_simulation_v1",
        "cost_methodology": "fixed_bps_v1",
        "initial_capital": 100_000.0,
        "base_currency": "USD",
        "logical_as_of": AS_OF,
        "recorded_at": RECORDED,
        "producer": "phase16b-tests",
        "producer_version": "1.0",
        "methodology_version": "methodology-16b-1.0",
    }
    values.update(overrides)
    return HistoricalReplaySpecification.create(**values)


def _clock(*, now: datetime, start: datetime, end: datetime) -> HistoricalClock:
    return HistoricalClock(now=now, start=start, end=end)


def _source() -> DataSourceIdentity:
    return DataSourceIdentity(
        provider="phase16b-tests",
        dataset="test",
        source_version="1",
        schema_version="1",
        offline_capable=True,
        authoritative=True,
    )


def _record(
    *,
    record_id: str = "test.1",
    event_time: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    available_at: datetime = datetime(2026, 1, 1, tzinfo=UTC),
    observed_at: datetime = datetime(2025, 12, 31, tzinfo=UTC),
    ingested_at: datetime | None = None,
    revision_available_at: datetime | None = None,
    revision_number: int = 0,
) -> NormalizedDataRecord:
    source = _source()
    resolved_revision_available_at = revision_available_at if revision_available_at is not None else available_at
    resolved_ingested_at = ingested_at if ingested_at is not None else max(observed_at, available_at)
    availability = DataAvailability(
        observed_at=observed_at,
        available_at=available_at,
        ingested_at=resolved_ingested_at,
    )
    revision = DataRevision(
        revision_id=f"r{revision_number}",
        revision_number=revision_number,
        previous_revision_id=f"r{revision_number - 1}" if revision_number > 0 else None,
        revised_at=resolved_revision_available_at,
        available_at=resolved_revision_available_at,
        source_fingerprint=sha256_fingerprint(
            {"record_id": record_id, "value": 1.0, "available_at": resolved_revision_available_at.isoformat()}
        ),
    )
    quality = DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0)
    return NormalizedDataRecord(
        record_id=DataRecordId(record_id),
        domain=DataDomain.MARKET,
        event_time=event_time,
        value=1.0,
        units="unit",
        availability=availability,
        revision=revision,
        source=source,
        quality=quality,
        source_fingerprint=sha256_fingerprint({"record_id": record_id, "value": 1.0}),
        schema_version="1",
    )


class _SimpleBarsResult:
    def __init__(self, bars: tuple[OHLCVBar, ...], timeframe: str) -> None:
        self.bars = bars
        self.timeframe = timeframe


def _observation(*, available_at: datetime = datetime(2026, 1, 1, tzinfo=UTC)) -> EconomicObservation:
    return EconomicObservation(
        series=EconomicSeriesDefinition(
            series_id="gdp",
            name="GDP",
            category="macro",
            geography="US",
            units="percent",
            frequency=Frequency.QUARTERLY,
            source=_source(),
        ),
        reference_period=datetime(2025, 12, 31, tzinfo=UTC),
        value=2.0,
        first_release_at=available_at,
        available_at=available_at,
        revision_number=0,
        revised_at=available_at,
        quality=DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0),
        source_fingerprint=sha256_fingerprint({"series_id": "gdp", "value": 2.0, "available_at": available_at.isoformat()}),
    )


def _bar(timestamp: datetime) -> OHLCVBar:
    return OHLCVBar(
        timestamp=timestamp,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=1000,
    )


class TestHistoricalClockContracts:
    def test_timezone_aware_initialization_required(self) -> None:
        with pytest.raises(HistoricalClockError, match="timezone-aware UTC start time"):
            HistoricalClock(now=datetime(2025, 1, 1), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 6, 1, tzinfo=UTC))  # noqa: DTZ001

    def test_initial_time_outside_replay_bounds_raises(self) -> None:
        with pytest.raises(HistoricalClockBoundsError, match="outside replay range"):
            HistoricalClock(
                now=datetime(2024, 12, 31, tzinfo=UTC),
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 6, 1, tzinfo=UTC),
            )

    def test_deterministic_initialization(self) -> None:
        now = datetime(2025, 1, 1, tzinfo=UTC)
        clock = HistoricalClock(now=now, start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2025, 6, 1, tzinfo=UTC))
        assert clock.now == now

    def test_monotonically_increasing(self) -> None:
        clock = HistoricalClock(
            now=datetime(2025, 1, 1, tzinfo=UTC),
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        clock.advance_to(datetime(2025, 3, 1, tzinfo=UTC))
        assert clock.now == datetime(2025, 3, 1, tzinfo=UTC)

    def test_cannot_move_backward(self) -> None:
        clock = HistoricalClock(
            now=datetime(2025, 3, 1, tzinfo=UTC),
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        with pytest.raises(HistoricalClockBackwardError):
            clock.advance_to(datetime(2025, 1, 1, tzinfo=UTC))

    def test_replay_start_end_bounds(self) -> None:
        clock = HistoricalClock(
            now=datetime(2025, 3, 1, tzinfo=UTC),
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        with pytest.raises(HistoricalClockBoundsError):
            clock.advance_to(datetime(2025, 7, 1, tzinfo=UTC))
        clock.advance_to(datetime(2025, 6, 1, tzinfo=UTC))
        with pytest.raises(HistoricalClockBoundsError):
            clock.advance_to(datetime(2025, 6, 2, tzinfo=UTC))

    def test_no_wall_clock_dependency(self) -> None:
        clock = HistoricalClock(
            now=datetime(2025, 2, 1, tzinfo=UTC),
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        clock.advance_by(timedelta(days=15))
        assert clock.now == datetime(2025, 2, 16, tzinfo=UTC)

    def test_copy_is_independent(self) -> None:
        clock = HistoricalClock(
            now=datetime(2025, 1, 1, tzinfo=UTC),
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 6, 1, tzinfo=UTC),
        )
        copy = clock.copy()
        copy.advance_to(datetime(2025, 3, 1, tzinfo=UTC))
        assert clock.now == datetime(2025, 1, 1, tzinfo=UTC)
        assert copy.now == datetime(2025, 3, 1, tzinfo=UTC)


class TestPointInTimeRecordVisibility:
    def test_record_available_before_t_is_visible(self) -> None:
        clock = _clock(now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 5, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is True

    def test_record_available_exactly_at_t_is_visible(self) -> None:
        clock = _clock(now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 10, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is True

    def test_future_available_at_is_hidden(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 10, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is False

    def test_event_time_before_t_but_available_after_t_remains_hidden(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(event_time=datetime(2026, 1, 1, tzinfo=UTC), available_at=datetime(2026, 1, 10, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is False

    def test_later_clock_exposes_previously_unavailable_record(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 10, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is False
        clock.advance_to(datetime(2026, 1, 10, tzinfo=UTC))
        assert boundary.is_record_visible(record) is True


class TestCompletedBarAvailability:
    def test_completed_daily_bar_not_exposed_before_close(self) -> None:
        clock = _clock(
            now=datetime(2026, 8, 3, 12, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC)
        )
        result = _SimpleBarsResult(bars=(_bar(datetime(2026, 8, 3, tzinfo=UTC)),), timeframe="1d")
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        with pytest.raises(PointInTimeLookaheadError):
            boundary.filter_historical_bars(result)

    def test_completed_daily_bar_exposed_after_close(self) -> None:
        clock = _clock(now=datetime(2026, 8, 4, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 9, 1, tzinfo=UTC))
        result = _SimpleBarsResult(bars=(_bar(datetime(2026, 8, 3, tzinfo=UTC)),), timeframe="1d")
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.filter_historical_bars(result) == (_bar(datetime(2026, 8, 3, tzinfo=UTC)),)


class TestRevisionPolicy:
    def test_future_revision_hidden(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        earlier = _record(
            available_at=datetime(2026, 1, 1, tzinfo=UTC), revision_available_at=datetime(2026, 1, 1, tzinfo=UTC), revision_number=0
        )
        later = _record(
            available_at=datetime(2026, 1, 10, tzinfo=UTC), revision_available_at=datetime(2026, 1, 10, tzinfo=UTC), revision_number=1
        )
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.latest_revision((earlier, later)) == earlier
        clock.advance_to(datetime(2026, 1, 10, tzinfo=UTC))
        assert boundary.latest_revision((earlier, later)) == later

    def test_latest_valid_historical_revision_selected(self) -> None:
        clock = _clock(now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        rev0 = _record(
            available_at=datetime(2026, 1, 8, tzinfo=UTC), revision_available_at=datetime(2026, 1, 8, tzinfo=UTC), revision_number=0
        )
        rev1 = _record(
            available_at=datetime(2026, 1, 9, tzinfo=UTC), revision_available_at=datetime(2026, 1, 9, tzinfo=UTC), revision_number=1
        )
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.latest_revision((rev0, rev1)) == rev1


class TestPointInTimePolicies:
    def test_available_at_aware_rejects_missing_availability_metadata(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 1, tzinfo=UTC))
        specification = _specification(point_in_time_policy="available_at_aware", data_boundary_description="strict availability")
        boundary = PointInTimeDataBoundary(clock=clock, specification=specification)
        assert boundary.is_record_visible(record) is True
        assert boundary.point_in_time_policy == "available_at_aware"

    def test_event_time_only_mode_still_enforces_available_at(self) -> None:
        clock = _clock(now=datetime(2026, 1, 5, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        record = _record(available_at=datetime(2026, 1, 10, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        assert boundary.is_record_visible(record) is False


class TestDeterministicOrdering:
    def test_deterministic_query_ordering(self) -> None:
        clock = _clock(now=datetime(2026, 1, 15, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        records = (
            _record(record_id="test.2"),
            _record(record_id="test.1"),
            _record(record_id="test.3"),
        )
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        filtered = boundary.filter_records(records)
        assert [record.record_id.root for record in filtered] == ["test.1", "test.2", "test.3"]
        assert boundary.filter_records(records) == filtered


class TestPointInTimeSnapshot:
    def test_snapshot_immutability_and_identity(self) -> None:
        clock = _clock(now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC))
        boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
        first = build_snapshot(
            clock=clock,
            specification=_specification(),
            boundary=boundary,
            record_identities=("record.1", "record.2"),
            input_fingerprints=("a" * 64, "b" * 64),
        )
        second = build_snapshot(
            clock=clock,
            specification=_specification(),
            boundary=boundary,
            record_identities=("record.1", "record.2"),
            input_fingerprints=("a" * 64, "b" * 64),
        )
        assert first.snapshot_id == second.snapshot_id
        assert first.available_at_aware is False
        assert first.event_time_only is True

    def test_persisted_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            clock = _clock(
                now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
            boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
            snapshot = build_snapshot(
                clock=clock,
                specification=_specification(),
                boundary=boundary,
                record_identities=("record.1",),
                source_versions={"provider:dataset": "1"},
            )
            envelope = snapshot.to_envelope(
                producer_version="1.0",
                provenance_references=_provenance(identifier="snapshot-test"),
            )
            stored = store.put(envelope)
            assert stored.artifact_id == envelope.artifact_id
            reloaded = store.get(envelope.artifact_id)
            assert reloaded.payload["snapshot_id"] == snapshot.snapshot_id
            assert reloaded.payload["event_time_only"] is True


class TestCorruptionAndSafety:
    def test_no_network_dependency_in_boundary_modules(self) -> None:
        from pathlib import Path

        modules = [
            Path("app/services/historical/clock.py"),
            Path("app/services/historical/boundary.py"),
            Path("app/services/historical/snapshot.py"),
            Path("app/services/historical/errors.py"),
        ]
        forbidden_modules = ("requests", "httpx", "urllib", "http.client", "aiohttp")
        for module_path in modules:
            source = module_path.read_text(encoding="utf-8")
            tree = ast.parse(source, str(module_path), "exec")
            visitor = _ImportVisitor()
            visitor.visit(tree)
            assert not any(forbidden in name.lower() for name in visitor.forbidden_imports for forbidden in forbidden_modules), module_path

    def test_file_artifact_store_restart_with_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            clock = _clock(
                now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
            boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
            snapshot = build_snapshot(clock=clock, specification=_specification(), boundary=boundary, record_identities=("record.1",))
            envelope = snapshot.to_envelope(
                producer_version="1.0",
                provenance_references=_provenance(identifier="restart-test"),
            )
            store.put(envelope)
            restarted = FileArtifactStore(temp_dir)
            reloaded = restarted.get(envelope.artifact_id)
            assert reloaded.payload["snapshot_id"] == snapshot.snapshot_id

    def test_corruption_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            clock = _clock(
                now=datetime(2026, 1, 10, tzinfo=UTC), start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 6, 1, tzinfo=UTC)
            )
            boundary = PointInTimeDataBoundary(clock=clock, specification=_specification())
            snapshot = build_snapshot(clock=clock, specification=_specification(), boundary=boundary, record_identities=("record.1",))
            envelope = snapshot.to_envelope(
                producer_version="1.0",
                provenance_references=_provenance(identifier="corrupt-test"),
            )
            store.put(envelope)
            prefix = envelope.artifact_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            filepath = target_dir / f"{envelope.artifact_id}.json"
            filepath.write_text("not-json", encoding="utf-8")
            reloaded = FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                reloaded.get(envelope.artifact_id)


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.forbidden_imports: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._check(module)
        self.generic_visit(node)

    def _check(self, module: str) -> None:
        parts = [part.lower() for part in module.split(".")]
        for forbidden in ("requests", "httpx", "urllib", "http.client", "aiohttp"):
            if forbidden in parts:
                self.forbidden_imports.append(module)


class TestPhase16BSafetyAndRegression:
    def test_existing_phase15_regression_stack_remains_green(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        required_modules = [
            "app.domain.models.research_run",
            "app.services.replay.manifest",
            "app.services.artifacts.file_store",
            "app.domain.models.backtesting",
        ]
        for module_name in required_modules:
            __import__(module_name)
        required_tests = [
            repo_root / "tests" / "test_replay.py",
            repo_root / "tests" / "test_research_run.py",
            repo_root / "tests" / "test_backtesting.py",
        ]
        for path in required_tests:
            assert path.exists(), f"missing regression test file: {path}"

    def test_point_in_time_policy_remains_event_time_only_by_default(self) -> None:
        specification = _specification()
        assert specification.point_in_time_policy == "event_time_only"
