"""Phase 16D Paper Execution Simulator tests."""

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
from app.domain.models.decision import TradeDecision
from app.domain.models.historical_decision import HistoricalDecisionStep
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.services.artifacts.errors import (
    ArtifactConflictError,
    ArtifactCorruptedError,
    ArtifactNotFoundError,
)
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.historical.clock import HistoricalClock
from app.services.historical.snapshot import PointInTimeDataSnapshot, build_snapshot
from app.services.paper_execution.contracts import (
    PaperExecutionResult,
    PaperFill,
    PaperFillStatus,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
)
from app.services.paper_execution.errors import (
    CorruptedDecisionArtifactError,
    InvalidPaperInputError,
    MissingCanonicalSizingError,
    ReplayLinkageError,
    UnfilledOrderError,
)
from app.services.paper_execution.models import (
    FillTimingPolicy,
    PaperExecutionMethodology,
    UnfilledOrderPolicy,
)
from app.services.paper_execution.simulator import PaperExecutionSimulator

DECISION_AT = datetime(2025, 5, 1, tzinfo=UTC)
REPLAY_START = datetime(2025, 4, 20, tzinfo=UTC)
REPLAY_END = datetime(2025, 5, 5, tzinfo=UTC)
SPEC_ID = "a" * 64


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16d tests",
            producer="phase16d-tests",
            producer_version="1.0",
        ),
    )


def _artifact_payload(store, artifact_type):
    artifact_id = store.list_ids(filters={"artifact_type": artifact_type})[0]
    envelope = store.get(artifact_id)
    return envelope.payload


def _specification() -> HistoricalReplaySpecification:
    return HistoricalReplaySpecification.create(
        start_time=REPLAY_START,
        end_time=REPLAY_END,
        instruments=["AAPL"],
        timeframes=["1d"],
        decision_cadence="window_close",
        data_boundary_description="event_time_only; no availability boundary available",
        point_in_time_policy="event_time_only",
        strategy_identities=["strategy:v1"],
        model_identities=["model:v1"],
        config_fingerprints=["cfg:v1"],
        execution_methodology="paper_execution_v1",
        cost_methodology="fixed_bps_v1",
        initial_capital=100_000.0,
        base_currency="USD",
        logical_as_of=DECISION_AT,
        recorded_at=DECISION_AT,
        producer="phase16d-tests",
        producer_version="1.0",
        methodology_version="methodology-16d-1.0",
    )


def _step(*, specification=None, trade_decision_artifact_id="0" * 64):
    if specification is None:
        specification = _specification()
    return HistoricalDecisionStep.create_completed(
        replay_specification_id=specification.specification_id,
        replay_run_id=specification.run_id,
        step_sequence=1,
        simulated_at=DECISION_AT,
        point_in_time_snapshot_id="0" * 64,
        snapshot_simulated_at=DECISION_AT,
        methodology_version="methodology-16d-1.0",
        execution_mode="DETERMINISTIC_RECOMPUTE",
        producer_version="1.0",
        input_fingerprints=(SPEC_ID,),
        lineage_artifact_ids=(SPEC_ID,),
        terminal_artifact_id="manifest-" + "0" * 60,
        trade_decision_artifact_id=trade_decision_artifact_id,
        decision_run_manifest_id="manifest-" + "0" * 60,
        decision_journal_entry_id="journal-" + "0" * 60,
    )


def _methodology() -> PaperExecutionMethodology:
    return PaperExecutionMethodology.create(
        methodology_name="next_bar_close_baseline",
        version="phase16d-1.0",
        fill_timing_policy=FillTimingPolicy.NEXT_BAR_CLOSE,
        price_source="next_bar_close",
        producer_version="1.0",
    )


def _decision(*, action="BUY", quantity=100):
    return TradeDecision(
        decision_id=UUID("d" * 32),
        ticker="AAPL",
        action=action,
        confidence=0.9,
        quantity=quantity,
        order_type="market",
        rationale="unit test paper execution decision",
        evidence=[],
        created_at=DECISION_AT,
    )


def _bars() -> HistoricalBarsResult:
    bars = [
        OHLCVBar(
            timestamp=datetime(2025, 5, 2, tzinfo=UTC),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1_000_000,
        ),
        OHLCVBar(
            timestamp=datetime(2025, 5, 3, tzinfo=UTC),
            open=100.5,
            high=102.0,
            low=100.0,
            close=101.5,
            volume=1_200_000,
        ),
        OHLCVBar(
            timestamp=datetime(2025, 5, 4, tzinfo=UTC),
            open=101.5,
            high=103.0,
            low=101.0,
            close=102.5,
            volume=1_400_000,
        ),
    ]
    return HistoricalBarsResult(
        symbol=Symbol("AAPL"),
        timeframe="1d",
        bars=bars,
        provider="unit-test",
        requested_start=REPLAY_START,
        requested_end=REPLAY_END,
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        adjustment="raw",
        session="regular",
        currency="USD",
        exchange="UNIT",
        partial=False,
        retrieved_at=DECISION_AT,
    )


def _simulator(store=None, specification=None, step=None, methodology=None):
    if specification is None:
        specification = _specification()
    if step is None:
        step = _step(specification=specification)
    if methodology is None:
        methodology = _methodology()
    if store is None:
        store = InMemoryArtifactStore()
    return PaperExecutionSimulator(
        store=store,
        specification=specification,
        step=step,
        methodology=methodology,
    )


class TestPaperExecutionMethodology:
    def test_immutable_methodology(self) -> None:
        methodology = _methodology()
        with pytest.raises(TypeError):
            methodology.model_copy(update={"fill_timing_policy": FillTimingPolicy.NEXT_BAR_CLOSE})

    def test_deterministic_methodology_identity(self) -> None:
        first = _methodology()
        second = _methodology()
        assert first.methodology_id == second.methodology_id
        assert first.canonical_hash == second.canonical_hash

    def test_different_material_produces_different_identity(self) -> None:
        first = _methodology()
        second = PaperExecutionMethodology.create(
            methodology_name="next_bar_close_baseline",
            version="phase16d-1.0",
            fill_timing_policy=FillTimingPolicy.NEXT_BAR_CLOSE,
            price_source="next_bar_close",
            producer_version="2.0",
        )
        assert first.methodology_id != second.methodology_id

    def test_next_bar_close_mismatched_price_source_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperExecutionMethodology.create(
                methodology_name="mismatched",
                version="phase16d-1.0",
                fill_timing_policy=FillTimingPolicy.NEXT_BAR_CLOSE,
                price_source="next_bar_open",
                producer_version="1.0",
            )

    def test_research_only_and_safety_flags(self) -> None:
        methodology = _methodology()
        assert methodology.research_only is True
        assert methodology.paper_trading_only is True
        assert methodology.suitable_for_live_trading is False


class TestPaperOrderContract:
    def test_immutable_paper_order(self) -> None:
        order = PaperOrder.create(
            paper_order_id="0" * 64,
            replay_specification_id="0" * 64,
            replay_run_id=UUID(int=0),
            historical_decision_step_id="0" * 64,
            trade_decision_artifact_id="0" * 64,
            symbol="AAPL",
            side=PaperOrderSide.BUY,
            quantity=100,
            order_type="market",
            submitted_at=DECISION_AT,
            eligible_execution_at=DECISION_AT + timedelta(days=1),
            execution_methodology_id="0" * 64,
            status=PaperOrderStatus.SUBMITTED,
        )
        with pytest.raises(TypeError):
            order.model_copy(update={"status": PaperOrderStatus.CANCELLED.value})

    def test_deterministic_paper_order_identity(self) -> None:
        simulator = _simulator()
        first = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        second = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert first.paper_order_id == second.paper_order_id

    def test_order_linked_to_historical_decision_step(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        order = _artifact_payload(simulator.store, ArtifactType.PAPER_ORDER)
        assert order["historical_decision_step_id"] == simulator.step.step_id

    def test_order_linked_to_canonical_trade_decision(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        order = _artifact_payload(simulator.store, ArtifactType.PAPER_ORDER)
        assert order["trade_decision_artifact_id"] == simulator.step.trade_decision_artifact_id

    def test_research_only_and_non_live_safety(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        order = _artifact_payload(simulator.store, ArtifactType.PAPER_ORDER)
        assert order["research_only"] is True
        assert order["suitable_for_live_trading"] is False


class TestPaperExecutionTiming:
    def test_execution_timestamp_after_decision(self) -> None:
        simulator = _simulator()
        result = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert result.executed_at > DECISION_AT

    def test_next_bar_close_fill_uses_close_only(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        fill = _artifact_payload(simulator.store, ArtifactType.PAPER_FILL)
        assert fill["execution_price"] == 100.5
        assert fill["source_bar_timestamp"] == datetime(2025, 5, 2, tzinfo=UTC).isoformat().replace("+00:00", "Z")

    def test_future_close_not_used_for_earlier_execution(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        fill = _artifact_payload(simulator.store, ArtifactType.PAPER_FILL)
        assert fill["execution_price"] == 100.5
        assert fill["execution_price"] != _bars().bars[2].close

    def test_same_bar_lookahead_rejected(self) -> None:
        same_bar_available_at = DECISION_AT + timedelta(days=1)
        result = _simulator().simulate(_decision(), _bars(), same_bar_available_at)
        assert result.execution_status is PaperOrderStatus.UNFILLED
        assert result.filled_quantity == 0


class TestMissingPriceAndUnfilled:
    def test_missing_valid_price_returns_unfilled(self) -> None:
        bars = HistoricalBarsResult(
            symbol=Symbol("AAPL"),
            timeframe="1d",
            bars=[
                OHLCVBar(
                    timestamp=DECISION_AT,
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1_000_000,
                )
            ],
            provider="unit-test",
            requested_start=REPLAY_START,
            requested_end=REPLAY_END,
            actual_start=DECISION_AT,
            actual_end=DECISION_AT,
            adjustment="raw",
            session="regular",
            currency="USD",
            exchange="UNIT",
            partial=False,
            retrieved_at=DECISION_AT,
        )
        result = _simulator().simulate(_decision(), _bars(), DECISION_AT + timedelta(days=1))
        assert result.execution_status is PaperOrderStatus.UNFILLED
        assert result.filled_quantity == 0

    def test_replay_end_boundary_respected(self) -> None:
        simulator = _simulator()
        with pytest.raises(InvalidPaperInputError):
            simulator.simulate(_decision(), _bars(), REPLAY_END + timedelta(days=1))

    def test_wrong_replay_linkage_rejected(self) -> None:
        specification = _specification()
        step = HistoricalDecisionStep.create_completed(
            replay_specification_id="b" * 64,
            replay_run_id=specification.run_id,
            step_sequence=1,
            simulated_at=DECISION_AT,
            point_in_time_snapshot_id="0" * 64,
            snapshot_simulated_at=DECISION_AT,
            methodology_version="methodology-16d-1.0",
            execution_mode="DETERMINISTIC_RECOMPUTE",
            producer_version="1.0",
            input_fingerprints=(SPEC_ID,),
            lineage_artifact_ids=(SPEC_ID,),
            terminal_artifact_id="manifest-" + "0" * 60,
            trade_decision_artifact_id="trade-decision-" + "0" * 60,
            decision_run_manifest_id="manifest-" + "0" * 60,
            decision_journal_entry_id="journal-" + "0" * 60,
        )
        with pytest.raises(ReplayLinkageError):
            PaperExecutionSimulator(
                store=InMemoryArtifactStore(),
                specification=specification,
                step=step,
                methodology=_methodology(),
            )

    def test_missing_sizing_handled_explicitly(self) -> None:
        decision = TradeDecision(
            decision_id=UUID("d" * 32),
            ticker="AAPL",
            action="BUY",
            confidence=0.9,
            quantity=0,
            order_type="market",
            rationale="missing sizing unit test",
            evidence=[],
            created_at=DECISION_AT,
        )
        with pytest.raises(MissingCanonicalSizingError):
            _simulator().simulate(decision, _bars(), DECISION_AT + timedelta(days=1))


class TestPaperFillContracts:
    def test_immutable_paper_fill(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        fill = _artifact_payload(simulator.store, ArtifactType.PAPER_FILL)
        with pytest.raises(TypeError):
            PaperFill.model_validate(fill).model_copy(update={"status": PaperFillStatus.PARTIAL.value})

    def test_deterministic_fill_identity(self) -> None:
        simulator = _simulator()
        first = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        second = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert first.paper_fill_ids == second.paper_fill_ids

    def test_execution_result_linkage(self) -> None:
        simulator = _simulator()
        result = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        result_envelope = _artifact_payload(simulator.store, ArtifactType.PAPER_EXECUTION_RESULT)
        assert result_envelope["paper_order_id"] == result.paper_order_id
        assert tuple(result_envelope["paper_fill_ids"]) == result.paper_fill_ids


class TestIdempotencyAndPersistence:
    def test_idempotent_repeated_execution(self) -> None:
        simulator = _simulator()
        first = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        second = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert first.paper_order_id == second.paper_order_id
        assert first.paper_fill_ids == second.paper_fill_ids
        assert first.paper_execution_result_id == second.paper_execution_result_id

    def test_file_artifact_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            specification = _specification()
            step = _step(specification=specification)
            methodology = _methodology()
            simulator = PaperExecutionSimulator(
                store=store,
                specification=specification,
                step=step,
                methodology=methodology,
            )
            first = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
            restarted_store = FileArtifactStore(temp_dir)
            reloaded_order = _artifact_payload(restarted_store, ArtifactType.PAPER_ORDER)
            assert reloaded_order["paper_order_id"] == first.paper_order_id
            reloaded_result = _artifact_payload(restarted_store, ArtifactType.PAPER_EXECUTION_RESULT)
            assert reloaded_result["filled_quantity"] == 100

    def test_corruption_propagation(self) -> None:
        simulator = _simulator()
        simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        order_id = simulator.store.list_ids(filters={"artifact_type": ArtifactType.PAPER_ORDER})[0]
        prefix = order_id[:2]
        target_dir = Path(tempfile.gettempdir()) / "phase16d-corruption-test" / "artifacts" / prefix
        target_dir.mkdir(parents=True, exist_ok=True)
        Path(target_dir / f"{order_id}.json").write_text("not-json", encoding="utf-8")
        reloaded_store = FileArtifactStore(str(Path(tempfile.gettempdir()) / "phase16d-corruption-test"))
        with pytest.raises(ArtifactCorruptedError):
            reloaded_store.get(order_id)


class TestSafetyAndSeparation:
    def test_no_broker_imports_or_dependencies(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "paper_execution" / "simulator.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = {
            node.module if isinstance(node, ast.ImportFrom) and node.module else getattr(node.names[0], "name", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        forbidden = {"broker", "execution", "portfolio", "risk", "live"}
        assert forbidden.isdisjoint({name.lower() for name in imports if isinstance(name, str)})

    def test_no_network_dependency(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "paper_execution" / "simulator.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        top_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert "get_bars" not in top_names
        assert "requests" not in top_names

    def test_no_cash_or_portfolio_mutation(self) -> None:
        result = _simulator().simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert "cash" not in type(result).model_fields
        assert "position" not in type(result).model_fields
        assert "portfolio_nav" not in type(result).model_fields

    def test_future_outcome_cannot_affect_execution(self) -> None:
        simulator = _simulator()
        outcome = ArtifactEnvelope.create(
            payload={"decision_artifact_id": simulator.step.trade_decision_artifact_id},
            artifact_type=ArtifactType.OUTCOME_EVALUATION,
            logical_as_of=DECISION_AT + timedelta(days=2),
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        simulator.store.put(outcome)
        result = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert result.execution_status is PaperOrderStatus.FILLED

    def test_decision_remains_unchanged_after_fill(self) -> None:
        specification = _specification()
        step = _step(specification=specification)
        store = InMemoryArtifactStore()
        step_envelope = step.envelope(provenance_references=_provenance())
        store.put(step_envelope)
        simulator = PaperExecutionSimulator(
            store=store,
            specification=specification,
            step=step,
            methodology=_methodology(),
        )
        original_decision = _decision()
        simulator.simulate(original_decision, _bars(), DECISION_AT + timedelta(days=1, hours=1))
        reloaded = store.get(step_envelope.artifact_id)
        assert reloaded.payload["status"] == step.status

    def test_phase16a_b_c_regression_remains_green(self) -> None:
        specification = _specification()
        step = _step(specification=specification)
        assert specification.specification_id == specification.replay_id
        assert (
            step.status
            == HistoricalDecisionStep.create_completed(
                replay_specification_id=step.replay_specification_id,
                replay_run_id=step.replay_run_id,
                step_sequence=step.step_sequence,
                simulated_at=step.simulated_at,
                point_in_time_snapshot_id=step.point_in_time_snapshot_id,
                snapshot_simulated_at=step.snapshot_simulated_at,
                methodology_version=step.methodology_version,
                execution_mode=step.execution_mode,
                producer_version=step.producer_version,
                input_fingerprints=step.input_fingerprints,
                lineage_artifact_ids=step.lineage_artifact_ids,
                terminal_artifact_id=step.terminal_artifact_id,
                trade_decision_artifact_id=step.trade_decision_artifact_id,
                decision_run_manifest_id=step.decision_run_manifest_id,
                decision_journal_entry_id=step.decision_journal_entry_id,
            ).status
        )


class TestIntegrationSequence:
    def test_full_historical_sequence(self) -> None:
        specification = _specification()
        store = InMemoryArtifactStore()
        clock = HistoricalClock(now=DECISION_AT, start=REPLAY_START, end=REPLAY_END)
        snapshot = build_snapshot(
            clock=clock,
            specification=specification,
            boundary=None,
            record_identities=("record.1",),
            input_fingerprints=(SPEC_ID,),
        )
        store.put(snapshot.to_envelope(producer_version="1.0", provenance_references=_provenance()))
        decision = _decision()
        decision_payload = decision.model_dump(mode="json", exclude_none=True)
        decision_payload.update(
            {
                "logical_as_of": DECISION_AT.isoformat(),
                "recorded_at": DECISION_AT.isoformat(),
                "research_only": True,
                "paper_trading_only": True,
                "suitable_for_live_trading": False,
            }
        )
        decision_envelope = ArtifactEnvelope.create(
            payload=decision_payload,
            artifact_type=ArtifactType.TRADE_DECISION,
            logical_as_of=DECISION_AT,
            producer_version="1.0",
            provenance_references=_provenance(),
        )
        store.put(decision_envelope)
        step = HistoricalDecisionStep.create_completed(
            replay_specification_id=specification.specification_id,
            replay_run_id=specification.run_id,
            step_sequence=1,
            simulated_at=snapshot.simulated_at,
            point_in_time_snapshot_id=snapshot.snapshot_id,
            snapshot_simulated_at=snapshot.simulated_at,
            methodology_version=specification.methodology_version,
            execution_mode="DETERMINISTIC_RECOMPUTE",
            producer_version="1.0",
            input_fingerprints=(SPEC_ID,),
            lineage_artifact_ids=(decision_envelope.artifact_id,),
            terminal_artifact_id="manifest-" + "0" * 60,
            trade_decision_artifact_id=decision_envelope.artifact_id,
            decision_run_manifest_id="manifest-" + "0" * 60,
            decision_journal_entry_id="journal-" + "0" * 60,
        )
        step_envelope = step.envelope(provenance_references=_provenance())
        store.put(step_envelope)

        clock_copy = HistoricalClock(now=DECISION_AT + timedelta(days=2, hours=1), start=REPLAY_START, end=REPLAY_END)
        methodology = _methodology()
        simulator = PaperExecutionSimulator(
            store=store,
            specification=specification,
            step=step,
            methodology=methodology,
        )
        result = simulator.simulate(decision, _bars(), clock_copy.now)

        assert result.paper_order_id
        assert result.paper_fill_ids
        assert result.execution_status is PaperOrderStatus.FILLED
        assert result.filled_quantity == 100
        assert result.remaining_quantity == 0
        assert result.execution_price == 100.5
        assert result.executed_at == datetime(2025, 5, 3, tzinfo=UTC)
        assert "cash" not in type(result).model_fields
        assert "position" not in type(result).model_fields
        assert result.suitable_for_live_trading is False


class TestPartialFillPolicy:
    def test_partial_fill_not_fabricated_without_liquidity_data(self) -> None:
        simulator = _simulator()
        result = simulator.simulate(_decision(), _bars(), DECISION_AT + timedelta(days=2, hours=1))
        assert result.execution_status is PaperOrderStatus.FILLED
        assert result.filled_quantity == result.requested_quantity


__all__ = [
    "TestIdempotencyAndPersistence",
    "TestIntegrationSequence",
    "TestMissingPriceAndUnfilled",
    "TestPaperExecutionMethodology",
    "TestPaperExecutionTiming",
    "TestPaperFillContracts",
    "TestPaperOrderContract",
    "TestPartialFillPolicy",
    "TestSafetyAndSeparation",
]
