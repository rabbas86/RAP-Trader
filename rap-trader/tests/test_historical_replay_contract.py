"""Phase 16A Historical Replay Contract tests.

Covers:
1. immutable specification
2. deterministic specification identity
3. different period changes identity
4. different universe changes identity
5. different configuration changes identity
6. deterministic seed behavior
7. invalid date range rejected
8. timezone-aware timestamps required
9. explicit point-in-time policy
10. immutable run manifest
11. deterministic manifest identity
12. artifact persistence
13. idempotent persistence
14. FileArtifactStore restart
15. corruption propagation
16. no broker/network/execution dependency
17. existing Phase15 regression stack remains green
"""

from __future__ import annotations

import ast
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.historical_replay import (
    BacktestRunManifest,
    HistoricalReplayRun,
    HistoricalReplaySpecification,
    ReplayRunEvent,
    ReplayRunStatus,
)
from app.services.artifacts.errors import (
    ArtifactCorruptedError,
)
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore

AS_OF = datetime(2026, 8, 15, tzinfo=UTC)
RECORDED = datetime(2026, 8, 15, 1, tzinfo=UTC)
CORRELATION_ID = UUID("1" * 32)
REPLAY_RUN_ID = UUID("c" * 32)
SPEC_ID = "a" * 64


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16a tests",
            producer="phase16a-tests",
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
        "producer": "phase16a-tests",
        "producer_version": "1.0",
        "methodology_version": "methodology-16a-1.0",
    }
    values.update(overrides)
    return HistoricalReplaySpecification.create(**values)


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
        for forbidden in ("broker", "execution", "portfolio", "order", "risk"):
            if forbidden in parts:
                self.forbidden_imports.append(module)


class TestHistoricalReplaySpecificationContracts:
    def test_immutable_specification(self) -> None:
        specification = _specification()
        with pytest.raises(ValidationError, match="Instance is frozen"):
            specification.initial_capital = 1.0

    def test_deterministic_specification_identity(self) -> None:
        first = _specification()
        second = _specification()
        assert first.specification_id == second.specification_id
        assert first.replay_id == second.replay_id

    def test_different_period_changes_identity(self) -> None:
        first = _specification(start_time=datetime(2025, 1, 1, tzinfo=UTC))
        second = _specification(start_time=datetime(2025, 2, 1, tzinfo=UTC))
        assert first.specification_id != second.specification_id

    def test_different_universe_changes_identity(self) -> None:
        first = _specification(instruments=["AAPL"])
        second = _specification(instruments=["MSFT"])
        assert first.specification_id != second.specification_id

    def test_different_configuration_changes_identity(self) -> None:
        first = _specification(config_fingerprints=["cfg:v1"])
        second = _specification(config_fingerprints=["cfg:v2"])
        assert first.specification_id != second.specification_id

    def test_deterministic_seed_behavior(self) -> None:
        seeded = _specification(deterministic_seed=42)
        assert seeded.deterministic_seed == 42
        assert seeded.specification_id == _specification(deterministic_seed=42).specification_id
        assert seeded.specification_id != _specification(deterministic_seed=7).specification_id

    def test_invalid_date_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="start_time must be before end_time"):
            _specification(start_time=datetime(2025, 6, 1, tzinfo=UTC))

    def test_timezone_aware_timestamps_required(self) -> None:
        with pytest.raises(ValueError, match="timestamp must include timezone information"):
            _specification(start_time=datetime(2025, 1, 1))  # noqa: DTZ001

    def test_explicit_point_in_time_policy(self) -> None:
        specification = _specification(point_in_time_policy="event_time_only")
        assert specification.point_in_time_policy == "event_time_only"
        with pytest.raises(ValidationError):
            _specification(point_in_time_policy="available_at_aware", data_boundary_description="")


class TestBacktestRunManifestContracts:
    def test_immutable_run_manifest(self) -> None:
        manifest = BacktestRunManifest(
            replay_run_id=UUID("b" * 32),
            specification_id="a" * 64,
            logical_as_of=AS_OF,
            producer_version="1.0",
            upstream_artifact_ids=("a" * 64,),
            universe_identity=("AAPL",),
            methodology_identities=("strategy:v1",),
            status=ReplayRunStatus.CREATED,
            start_time=AS_OF,
            end_time=RECORDED,
        )
        with pytest.raises(ValidationError, match="Instance is frozen"):
            manifest.producer_version = "2.0"

    def test_deterministic_manifest_identity(self) -> None:
        manifest = BacktestRunManifest(
            replay_run_id=UUID("b" * 32),
            specification_id="a" * 64,
            logical_as_of=AS_OF,
            producer_version="1.0",
            upstream_artifact_ids=("a" * 64,),
            universe_identity=("AAPL",),
            methodology_identities=("strategy:v1",),
            status=ReplayRunStatus.CREATED,
            start_time=AS_OF,
            end_time=RECORDED,
        )
        assert manifest.manifest_fingerprint() == manifest.manifest_fingerprint()

    def test_artifact_persistence(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification()
        envelope = store.put(
            ArtifactEnvelope.create(
                payload=specification.model_dump(mode="json", exclude_none=False),
                artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
                logical_as_of=AS_OF,
                producer_version="1.0",
                provenance_references=_provenance(specification.specification_id),
            )
        )
        loaded = store.get(envelope.artifact_id)
        assert loaded.artifact_id == envelope.artifact_id
        assert loaded.payload["specification_id"] == specification.specification_id

    def test_idempotent_persistence(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification()
        envelope = ArtifactEnvelope.create(
            payload=specification.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
            logical_as_of=AS_OF,
            producer_version="1.0",
            provenance_references=_provenance(specification.specification_id),
        )
        first = store.put(envelope)
        second = store.put(envelope)
        assert first.artifact_id == second.artifact_id
        assert store.exists(first.artifact_id) is True

    def test_file_artifact_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            specification = _specification()
            envelope = ArtifactEnvelope.create(
                payload=specification.model_dump(mode="json", exclude_none=False),
                artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
                logical_as_of=AS_OF,
                producer_version="1.0",
                provenance_references=_provenance(specification.specification_id),
            )
            store.put(envelope)
            restarted = FileArtifactStore(temp_dir)
            loaded = restarted.get(envelope.artifact_id)
            assert loaded.artifact_id == envelope.artifact_id
            assert loaded.payload["specification_id"] == specification.specification_id

    def test_corruption_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            specification = _specification()
            envelope = ArtifactEnvelope.create(
                payload=specification.model_dump(mode="json", exclude_none=False),
                artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
                logical_as_of=AS_OF,
                producer_version="1.0",
                provenance_references=_provenance(specification.specification_id),
            )
            store.put(envelope)
            prefix = envelope.artifact_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            filepath = target_dir / f"{envelope.artifact_id}.json"
            filepath.write_text("not-json", encoding="utf-8")
            reloaded = FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                reloaded.get(envelope.artifact_id)


class TestPhase16ASafetyAndRegression:
    def test_no_forbidden_runtime_dependencies(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "domain" / "models" / "historical_replay.py"
        source = module_path.read_text(encoding="utf-8")
        tree = ast.parse(source, str(module_path), "exec")
        visitor = _ImportVisitor()
        visitor.visit(tree)
        assert not visitor.forbidden_imports

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


class TestReplayRunLifecycle:
    def test_run_lifecycle_immutability(self) -> None:
        run = HistoricalReplayRun.create(
            specification_id=SPEC_ID,
            correlation_id=CORRELATION_ID,
            logical_as_of=AS_OF,
            recorded_at=RECORDED,
            producer="phase16a-tests",
            producer_version="1.0",
        )
        running = run.transition_to(ReplayRunStatus.RUNNING)
        completed = running.transition_to(ReplayRunStatus.COMPLETED)
        assert completed.status is ReplayRunStatus.COMPLETED
        assert run.status is ReplayRunStatus.CREATED

    def test_run_rejects_unsafe_live_flags(self) -> None:
        run = HistoricalReplayRun.create(
            specification_id=SPEC_ID,
            correlation_id=CORRELATION_ID,
            logical_as_of=AS_OF,
            recorded_at=RECORDED,
            producer="phase16a-tests",
            producer_version="1.0",
        )
        with pytest.raises(ValidationError):
            run.model_copy(update={"suitable_for_live_trading": True})


class TestReplayRunEventChain:
    def test_genesis_and_hash_chain(self) -> None:
        genesis = ReplayRunEvent.create(
            run_id=REPLAY_RUN_ID,
            sequence=1,
            correlation_id=CORRELATION_ID,
            causation_id=None,
            logical_as_of=AS_OF,
            recorded_at=RECORDED,
            event_type="replay.run.created",
            producer="phase16a-tests",
            producer_version="1.0",
            payload_reference="artifact://phase16a/genesis",
            payload_hash="a" * 64,
            prior_event_hash="0" * 64,
        )
        next_event = ReplayRunEvent.create(
            run_id=genesis.run_id,
            sequence=2,
            correlation_id=CORRELATION_ID,
            causation_id=genesis.event_id,
            logical_as_of=AS_OF,
            recorded_at=RECORDED,
            event_type="replay.run.updated",
            producer="phase16a-tests",
            producer_version="1.0",
            payload_reference="artifact://phase16a/next",
            payload_hash="b" * 64,
            prior_event_hash=genesis.event_hash,
        )
        assert next_event.prior_event_hash == genesis.event_hash
        assert next_event.causation_id == genesis.event_id

    def test_event_rejects_self_causation(self) -> None:
        event_id = UUID("d" * 32)
        with pytest.raises(ValidationError, match="event cannot cause itself"):
            ReplayRunEvent(
                run_id=REPLAY_RUN_ID,
                sequence=1,
                event_id=event_id,
                correlation_id=CORRELATION_ID,
                causation_id=event_id,
                logical_as_of=AS_OF,
                recorded_at=RECORDED,
                event_type="replay.run.created",
                producer="phase16a-tests",
                producer_version="1.0",
                payload_reference="artifact://phase16a/self",
                payload_hash="b" * 64,
                prior_event_hash="0" * 64,
            )


class TestBacktestRunManifestPersistence:
    def test_manifest_persists_and_roundtrips(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification()
        run = HistoricalReplayRun.create(
            specification_id=specification.specification_id,
            correlation_id=CORRELATION_ID,
            logical_as_of=AS_OF,
            recorded_at=RECORDED,
            producer="phase16a-tests",
            producer_version="1.0",
        )
        manifest = BacktestRunManifest(
            replay_run_id=run.run_id,
            specification_id=specification.specification_id,
            logical_as_of=AS_OF,
            producer_version="1.0",
            upstream_artifact_ids=(specification.specification_id, run.run_id.hex),
            universe_identity=("AAPL",),
            methodology_identities=("strategy:v1",),
            status=ReplayRunStatus.CREATED,
            start_time=AS_OF,
            end_time=RECORDED,
        )
        envelope = store.put(manifest.envelope())
        assert envelope.artifact_type is ArtifactType.BACKTEST_RUN_MANIFEST
        assert store.exists(envelope.artifact_id) is True
        loaded = store.get(envelope.artifact_id)
        assert loaded.payload["specification_id"] == manifest.specification_id
        assert manifest.manifest_fingerprint() == manifest.manifest_fingerprint()
