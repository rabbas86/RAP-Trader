"""Phase 16C Historical Decision Orchestrator tests."""

from __future__ import annotations

import ast
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
from app.domain.models.historical_decision import (
    HistoricalDecisionStep,
    HistoricalDecisionStepStatus,
)
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.services.artifacts.errors import ArtifactConflictError, ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.historical.boundary import PointInTimeDataBoundary
from app.services.historical.clock import HistoricalClock
from app.services.historical.decision_errors import (
    CorruptedSourceArtifactError,
    FutureSnapshotError,
    LookaheadContaminationError,
    SnapshotReplaySpecificationMismatchError,
    UnsupportedHistoricalModeError,
)
from app.services.historical.orchestrator import HistoricalDecisionOrchestrator
from app.services.historical.snapshot import build_snapshot

AS_OF = datetime(2025, 5, 1, tzinfo=UTC)
RECORDED = datetime(2025, 5, 2, tzinfo=UTC)
SPEC_ID = "a" * 64


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16c tests",
            producer="phase16c-tests",
            producer_version="1.0",
        ),
    )


def _specification(
    *,
    start_time: datetime = AS_OF,
    end_time: datetime = RECORDED,
) -> HistoricalReplaySpecification:
    return HistoricalReplaySpecification.create(
        start_time=start_time,
        end_time=end_time,
        instruments=["AAPL", "BRK.B"],
        timeframes=["1d", "1h"],
        decision_cadence="window_close",
        data_boundary_description="event_time_only; no availability boundary available",
        point_in_time_policy="event_time_only",
        strategy_identities=["strategy:v1"],
        model_identities=["model:v1"],
        config_fingerprints=["cfg:v1"],
        execution_methodology="research_simulation_v1",
        cost_methodology="fixed_bps_v1",
        initial_capital=100_000.0,
        base_currency="USD",
        logical_as_of=AS_OF,
        recorded_at=RECORDED,
        producer="phase16c-tests",
        producer_version="1.0",
        methodology_version="methodology-16c-1.0",
    )


def _snapshot(
    *,
    specification=None,
    clock=None,
    store=None,
):
    if specification is None:
        specification = _specification()
    if clock is None:
        clock = HistoricalClock(
            now=specification.end_time,
            start=specification.start_time,
            end=specification.end_time,
        )
    boundary = PointInTimeDataBoundary(clock=clock, specification=specification)
    snapshot = build_snapshot(
        clock=clock,
        specification=specification,
        boundary=boundary,
        record_identities=("record.1",),
        input_fingerprints=("a" * 64,),
    )
    if store is not None:
        store.put(snapshot.to_envelope(producer_version="1.0", provenance_references=_provenance()))
    return snapshot


def _persisted_snapshot(*, specification=None, clock=None, store=None):
    snapshot = _snapshot(specification=specification, clock=clock, store=store)
    if store is None:
        store = InMemoryArtifactStore()
        store.put(snapshot.to_envelope(producer_version="1.0", provenance_references=_provenance()))
    return snapshot, store


def _orchestrator(*, store=None, specification=None, clock=None, snapshot=None):
    if specification is None:
        specification = _specification()
    if snapshot is None:
        snapshot, store = _persisted_snapshot(specification=specification, clock=clock, store=store)
    if clock is None:
        clock = HistoricalClock(
            now=snapshot.simulated_at,
            start=specification.start_time,
            end=specification.end_time,
        )
    if store is None:
        store = InMemoryArtifactStore()
    orchestrator = HistoricalDecisionOrchestrator(
        clock=clock,
        specification=specification,
        snapshot=snapshot,
        store=store,
    )
    return orchestrator, snapshot, store


class TestHistoricalDecisionStepContracts:
    def test_frozen_step(self) -> None:
        step = HistoricalDecisionStep(
            schema_version="1.0",
            research_only=True,
            paper_trading_only=True,
            suitable_for_live_trading=False,
            step_id="0" * 64,
            replay_specification_id="a" * 64,
            replay_run_id=UUID("b" * 32),
            step_sequence=1,
            simulated_at=AS_OF,
            point_in_time_snapshot_id="c" * 64,
            snapshot_simulated_at=AS_OF,
            research_run_id="d" * 64,
            methodology_version="methodology-16c-1.0",
            execution_mode="DETERMINISTIC_RECOMPUTE",
            producer_version="1.0",
            input_fingerprints=("a" * 64,),
            lineage_artifact_ids=("e" * 64,),
            terminal_artifact_id="f" * 64,
            trade_decision_artifact_id="g" * 64,
            decision_run_manifest_id="h" * 64,
            decision_journal_entry_id="i" * 64,
            status=HistoricalDecisionStepStatus.COMPLETED.value,
        )
        with pytest.raises(ValidationError):
            HistoricalDecisionStep.create_completed(
                replay_specification_id="a" * 64,
                replay_run_id=UUID("b" * 32),
                step_sequence=1,
                simulated_at=AS_OF,
                point_in_time_snapshot_id="c" * 64,
                snapshot_simulated_at=AS_OF,
                methodology_version="methodology-16c-1.0",
                execution_mode="DETERMINISTIC_RECOMPUTE",
                producer_version="1.0",
                input_fingerprints=("a" * 64,),
                lineage_artifact_ids=("e" * 64,),
                terminal_artifact_id="f" * 64,
                trade_decision_artifact_id="",
                decision_run_manifest_id="h" * 64,
                decision_journal_entry_id="i" * 64,
            )
        mutation = step.model_copy(update={"status": HistoricalDecisionStepStatus.FAILED.value})
        assert mutation.status == HistoricalDecisionStepStatus.FAILED.value
        assert step.status == HistoricalDecisionStepStatus.COMPLETED.value

    def test_create_completed_rejects_incomplete_linkage(self) -> None:
        with pytest.raises(ValidationError):
            HistoricalDecisionStep.create_completed(
                replay_specification_id="a" * 64,
                replay_run_id=UUID("b" * 32),
                step_sequence=1,
                simulated_at=AS_OF,
                point_in_time_snapshot_id="c" * 64,
                snapshot_simulated_at=AS_OF,
                methodology_version="methodology-16c-1.0",
                execution_mode="DETERMINISTIC_RECOMPUTE",
                producer_version="1.0",
                input_fingerprints=("a" * 64,),
                lineage_artifact_ids=("e" * 64,),
                terminal_artifact_id="f" * 64,
                trade_decision_artifact_id="",
                decision_run_manifest_id="h" * 64,
                decision_journal_entry_id="i" * 64,
            )

    def test_create_failed_does_not_require_completed_linkage(self) -> None:
        step = HistoricalDecisionStep.create_failed(
            replay_specification_id="a" * 64,
            replay_run_id=UUID("b" * 32),
            step_sequence=1,
            simulated_at=AS_OF,
            point_in_time_snapshot_id="c" * 64,
            snapshot_simulated_at=AS_OF,
            methodology_version="methodology-16c-1.0",
            execution_mode="DETERMINISTIC_RECOMPUTE",
            producer_version="1.0",
            failure_reference="deterministic-pipeline-error",
            input_fingerprints=("a" * 64,),
            lineage_artifact_ids=("e" * 64,),
        )
        assert step.status == HistoricalDecisionStepStatus.FAILED.value
        assert step.trade_decision_artifact_id is None

    def test_snapshot_and_specification_mismatch_rejected(self) -> None:
        specification = _specification()
        snapshot = _snapshot(
            specification=specification,
            clock=HistoricalClock(now=AS_OF, start=AS_OF, end=RECORDED),
        )
        other = HistoricalReplaySpecification.create(
            start_time=AS_OF,
            end_time=RECORDED,
            instruments=tuple(specification.instruments),
            timeframes=tuple(specification.timeframes),
            decision_cadence=specification.decision_cadence,
            data_boundary_description=specification.data_boundary_description,
            point_in_time_policy=specification.point_in_time_policy,
            strategy_identities=tuple(specification.strategy_identities),
            model_identities=tuple(specification.model_identities),
            config_fingerprints=("other-cfg",),
            execution_methodology=specification.execution_methodology,
            cost_methodology=specification.cost_methodology,
            initial_capital=specification.initial_capital,
            base_currency=specification.base_currency,
            logical_as_of=specification.logical_as_of,
            recorded_at=specification.recorded_at,
            producer=specification.producer,
            producer_version=specification.producer_version,
            methodology_version=specification.methodology_version,
        )
        with pytest.raises(SnapshotReplaySpecificationMismatchError):
            HistoricalDecisionOrchestrator(
                clock=HistoricalClock(
                    now=AS_OF,
                    start=specification.start_time,
                    end=specification.end_time,
                ),
                specification=other,
                snapshot=snapshot,
                store=InMemoryArtifactStore(),
            )


class TestHistoricalDecisionExecution:
    def test_execute_returns_step_and_envelope(self) -> None:
        orchestrator, _, _store = _orchestrator()
        step, envelope = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        assert isinstance(step, HistoricalDecisionStep)
        assert envelope.artifact_type is ArtifactType.HISTORICAL_DECISION_STEP
        assert envelope.payload["step_id"] == step.step_id

    def test_snapshot_bound_decision_step(self) -> None:
        orchestrator, _, _store = _orchestrator()
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        assert step.point_in_time_snapshot_id == orchestrator.snapshot.snapshot_id
        assert step.trade_decision_artifact_id is not None
        assert step.decision_run_manifest_id is not None
        assert step.decision_journal_entry_id is not None
        assert step.status == HistoricalDecisionStepStatus.COMPLETED.value

    def test_trade_decision_lineage_direction(self) -> None:
        orchestrator, _, store = _orchestrator()
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        decision_envelope = store.get(step.trade_decision_artifact_id)
        assert decision_envelope.artifact_type is ArtifactType.TRADE_DECISION
        payload = decision_envelope.payload if isinstance(decision_envelope.payload, dict) else {}
        assert "step_id" not in payload
        assert payload["ticker"] == str(orchestrator.specification.instruments[0])
        assert payload["action"] == "WAIT"

    def test_decision_run_manifest_linkage(self) -> None:
        orchestrator, _, store = _orchestrator()
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        manifest_envelope = store.get(step.decision_run_manifest_id)
        assert manifest_envelope.artifact_type is ArtifactType.DECISION_RUN_MANIFEST
        assert manifest_envelope.payload["terminal_artifact_id"] == step.trade_decision_artifact_id

    def test_decision_journal_linkage(self) -> None:
        orchestrator, _, store = _orchestrator()
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        journal_envelope = store.get(step.decision_journal_entry_id)
        assert journal_envelope.artifact_type is ArtifactType.DECISION_JOURNAL_ENTRY
        assert journal_envelope.payload["decision_artifact_id"] == step.trade_decision_artifact_id

    def test_replay_dag_linkage_from_decision_artifact(self) -> None:
        orchestrator, _, _store = _orchestrator()
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        graph = orchestrator.replay_service.build_graph(step.trade_decision_artifact_id)
        assert graph.terminal_artifact_id == step.trade_decision_artifact_id

    def test_step_ordering_multiple_points(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(
            start_time=datetime(2025, 1, 1, tzinfo=UTC),
            end_time=datetime(2025, 1, 3, tzinfo=UTC),
        )
        clock_t1 = HistoricalClock(
            now=datetime(2025, 1, 1, tzinfo=UTC),
            start=specification.start_time,
            end=specification.end_time,
        )
        clock_t2 = HistoricalClock(
            now=datetime(2025, 1, 2, tzinfo=UTC),
            start=specification.start_time,
            end=specification.end_time,
        )
        snapshot_t1, _ = _persisted_snapshot(specification=specification, clock=clock_t1, store=store)
        snapshot_t2, _ = _persisted_snapshot(specification=specification, clock=clock_t2, store=store)
        step_t1 = HistoricalDecisionOrchestrator(
            clock=clock_t1,
            specification=specification,
            snapshot=snapshot_t1,
            store=store,
        ).execute_decision_point(snapshot_t1.simulated_at)[0]
        step_t2 = HistoricalDecisionOrchestrator(
            clock=clock_t2,
            specification=specification,
            snapshot=snapshot_t2,
            store=store,
        ).execute_decision_point(snapshot_t2.simulated_at)[0]
        assert step_t1.simulated_at < step_t2.simulated_at
        assert step_t1.step_id != step_t2.step_id

    def test_later_clock_does_not_mutate_earlier_step(self) -> None:
        orchestrator, _, _store = _orchestrator(store=InMemoryArtifactStore())
        step, persisted = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        reloaded = orchestrator.get_step(persisted.artifact_id)
        assert reloaded.snapshot_simulated_at == step.snapshot_simulated_at
        assert reloaded.trade_decision_artifact_id == step.trade_decision_artifact_id


class TestHistoricalDecisionIdempotency:
    def test_idempotent_decision_point(self) -> None:
        orchestrator, _, store = _orchestrator(store=InMemoryArtifactStore())
        first, first_envelope = orchestrator.idempotent_decision_point(orchestrator.snapshot.simulated_at)
        second, second_envelope = orchestrator.idempotent_decision_point(orchestrator.snapshot.simulated_at)
        assert first.step_id == second.step_id
        assert first_envelope.artifact_id == second_envelope.artifact_id
        assert store.exists(first_envelope.artifact_id) is True

    def test_persisted_step_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            orchestrator, _, _ = _orchestrator(store=store)
            step, persisted = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
            restarted = FileArtifactStore(temp_dir)
            reloaded = HistoricalDecisionOrchestrator(
                clock=orchestrator.clock,
                specification=orchestrator.specification,
                snapshot=orchestrator.snapshot,
                store=restarted,
            ).get_step(persisted.artifact_id)
            assert reloaded.step_id == step.step_id


class TestFutureOutcomeSeparation:
    def test_future_outcome_evaluation_rejected(self) -> None:
        orchestrator, _, store = _orchestrator(store=InMemoryArtifactStore())
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        future_outcome_envelope = ArtifactEnvelope.create(
            payload={
                "decision_artifact_id": step.trade_decision_artifact_id,
                "evaluation_horizon": "1d",
            },
            artifact_type=ArtifactType.OUTCOME_EVALUATION,
            logical_as_of=orchestrator.snapshot.simulated_at + timedelta(days=1),
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        store.put(future_outcome_envelope)
        with pytest.raises(LookaheadContaminationError):
            orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)

    def test_future_attribution_record_rejected(self) -> None:
        orchestrator, _, store = _orchestrator(store=InMemoryArtifactStore())
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        attribution_envelope = ArtifactEnvelope.create(
            payload={"decision_artifact_id": step.trade_decision_artifact_id},
            artifact_type=ArtifactType.ATTRIBUTION_RECORD,
            logical_as_of=orchestrator.snapshot.simulated_at + timedelta(days=1),
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        store.put(attribution_envelope)
        with pytest.raises(LookaheadContaminationError):
            orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)

    def test_past_outcome_artifacts_do_not_contaminate(self) -> None:
        orchestrator, _, store = _orchestrator(store=InMemoryArtifactStore())
        step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        past_outcome_envelope = ArtifactEnvelope.create(
            payload={"decision_artifact_id": step.trade_decision_artifact_id},
            artifact_type=ArtifactType.OUTCOME_OBSERVATION,
            logical_as_of=orchestrator.snapshot.simulated_at - timedelta(days=1),
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        store.put(past_outcome_envelope)
        new_step, _ = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
        assert new_step.step_id == step.step_id


class TestFailureAndSafety:
    def test_unsupported_mode_rejected(self) -> None:
        with pytest.raises(UnsupportedHistoricalModeError):
            HistoricalDecisionOrchestrator(
                clock=HistoricalClock(now=AS_OF, start=AS_OF, end=RECORDED),
                specification=_specification(),
                snapshot=_snapshot(),
                store=InMemoryArtifactStore(),
                mode="LIVE",
            )

    def test_future_snapshot_rejected(self) -> None:
        snapshot = _snapshot(clock=HistoricalClock(now=RECORDED, start=AS_OF, end=RECORDED))
        with pytest.raises(FutureSnapshotError):
            HistoricalDecisionOrchestrator(
                clock=HistoricalClock(now=AS_OF, start=AS_OF, end=RECORDED),
                specification=_specification(),
                snapshot=snapshot,
                store=InMemoryArtifactStore(),
            )

    def test_record_failed_step(self) -> None:
        orchestrator, _, _ = _orchestrator()
        failed_step = orchestrator.record_failed_step(orchestrator.snapshot.simulated_at, "deterministic-pipeline-error")
        assert failed_step.status == HistoricalDecisionStepStatus.FAILED.value
        assert failed_step.trade_decision_artifact_id is None

    def test_corruption_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            orchestrator, _, _ = _orchestrator(store=store)
            _, persisted = orchestrator.execute_decision_point(orchestrator.snapshot.simulated_at)
            prefix = persisted.artifact_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            target_dir.mkdir(parents=True, exist_ok=True)
            filepath = target_dir / f"{persisted.artifact_id}.json"
            filepath.write_text("not-json", encoding="utf-8")
            FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                orchestrator.get_step(persisted.artifact_id)

    def test_wrong_artifact_type_rejected(self) -> None:
        snapshot, store = _persisted_snapshot()
        snapshot_envelope = None
        for artifact_id in store.list_ids(filters={"artifact_type": ArtifactType.POINT_IN_TIME_DATA_SNAPSHOT}):
            candidate = store.get(artifact_id)
            if candidate.payload if isinstance(candidate.payload, dict) else {}.get("snapshot_id") == snapshot.snapshot_id:
                snapshot_envelope = candidate
                break
        assert snapshot_envelope is not None
        payload = snapshot_envelope.payload if isinstance(snapshot_envelope.payload, dict) else {}
        bad_payload = dict(payload)
        bad_payload["snapshot_id"] = snapshot.snapshot_id
        bad_envelope = ArtifactEnvelope(
            artifact_id=snapshot_envelope.artifact_id,
            artifact_type=ArtifactType.BACKTEST_RUN_MANIFEST,
            schema_version=snapshot_envelope.schema_version,
            logical_as_of=snapshot_envelope.logical_as_of,
            producer_version=snapshot_envelope.producer_version,
            payload_hash=sha256_fingerprint(bad_payload),
            provenance_references=snapshot_envelope.provenance_references,
            payload=bad_payload,
        )
        with pytest.raises(ArtifactConflictError):
            store.put(bad_envelope)
        with tempfile.TemporaryDirectory() as temp_dir:
            corrupted_store = FileArtifactStore(temp_dir)
            corrupted_store.put(snapshot_envelope)
            snapshot_path = corrupted_store._filepath(snapshot_envelope.artifact_id)
            Path(snapshot_path).write_text(bad_envelope.model_dump_json(), encoding="utf-8")
            orchestrator = HistoricalDecisionOrchestrator(
                clock=HistoricalClock(
                    now=snapshot.simulated_at,
                    start=_specification().start_time,
                    end=_specification().end_time,
                ),
                specification=_specification(),
                snapshot=snapshot,
                store=corrupted_store,
            )
            with pytest.raises(CorruptedSourceArtifactError):
                orchestrator.load_snapshot_envelope()


class TestIntegrationTAndT1:
    def test_t_snapshot_decision_and_t1_separation(self) -> None:
        specification = _specification(
            start_time=datetime(2025, 1, 1, tzinfo=UTC),
            end_time=datetime(2025, 1, 3, tzinfo=UTC),
        )
        store = InMemoryArtifactStore()
        clock_t1 = HistoricalClock(
            now=datetime(2025, 1, 2, tzinfo=UTC),
            start=specification.start_time,
            end=specification.end_time,
        )
        clock_t2 = HistoricalClock(
            now=datetime(2025, 1, 3, tzinfo=UTC),
            start=specification.start_time,
            end=specification.end_time,
        )
        snapshot_t1, _ = _persisted_snapshot(specification=specification, clock=clock_t1, store=store)
        snapshot_t2, _ = _persisted_snapshot(specification=specification, clock=clock_t2, store=store)
        orchestrator_t1 = HistoricalDecisionOrchestrator(
            clock=clock_t1,
            specification=specification,
            snapshot=snapshot_t1,
            store=store,
        )
        step_t1, persisted_t1 = orchestrator_t1.execute_decision_point(snapshot_t1.simulated_at)
        orchestrator_t2 = HistoricalDecisionOrchestrator(
            clock=clock_t2,
            specification=specification,
            snapshot=snapshot_t2,
            store=store,
        )
        step_t2, _ = orchestrator_t2.execute_decision_point(snapshot_t2.simulated_at)
        assert snapshot_t1.snapshot_id != snapshot_t2.snapshot_id
        assert step_t1.step_id != step_t2.step_id
        assert step_t1.trade_decision_artifact_id != step_t2.trade_decision_artifact_id
        assert datetime.fromisoformat(store.get(step_t1.trade_decision_artifact_id).payload["created_at"]) == snapshot_t1.simulated_at
        future_outcome = ArtifactEnvelope.create(
            payload={"decision_artifact_id": step_t1.trade_decision_artifact_id},
            artifact_type=ArtifactType.OUTCOME_EVALUATION,
            logical_as_of=snapshot_t2.simulated_at,
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        store.put(future_outcome)
        reloaded_t1 = orchestrator_t1.get_step(persisted_t1.artifact_id)
        assert reloaded_t1.trade_decision_artifact_id == step_t1.trade_decision_artifact_id
        assert all("outcome" not in key for key in (store.get(step_t1.trade_decision_artifact_id).payload or {}))


class TestRegressionStack:
    def test_prior_phase_regression_stack_green(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        required_modules = [
            "app.domain.models.historical_replay",
            "app.services.replay.service",
            "app.services.historical.boundary",
            "app.services.historical.snapshot",
        ]
        for module in required_modules:
            source = (repo_root / module.replace(".", "/")).with_suffix(".py")
            assert source.exists(), f"missing regression module: {module}"
            ast.parse(source.read_text(encoding="utf-8"), str(source), "exec")
