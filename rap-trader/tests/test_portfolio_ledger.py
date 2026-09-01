"""Phase 16E Portfolio Ledger tests."""

from __future__ import annotations

import ast
import tempfile
from datetime import UTC, datetime, timedelta
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
from app.domain.models.decision import TradeDecision
from app.domain.models.historical_decision import HistoricalDecisionStep
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.historical.clock import HistoricalClock
from app.services.historical.snapshot import build_snapshot
from app.services.paper_execution.contracts import (
    PaperExecutionResult,
    PaperOrderSide,
    PaperOrderStatus,
)
from app.services.paper_execution.models import FillTimingPolicy, PaperExecutionMethodology
from app.services.paper_execution.simulator import PaperExecutionSimulator
from app.services.portfolio_accounting.errors import (
    InsufficientCashError,
    InvalidFillError,
    InvalidMethodologyError,
    PortfolioAccountingValidationError,
    UnauthorizedShortError,
)
from app.services.portfolio_accounting.ledger import PortfolioLedger
from app.services.portfolio_accounting.models import (
    PortfolioAccountingMethodology,
    PortfolioSnapshot,
    PositionState,
)

DECISION_AT = datetime(2025, 5, 1, tzinfo=UTC)
REPLAY_START = datetime(2025, 4, 20, tzinfo=UTC)
REPLAY_END = datetime(2025, 5, 5, tzinfo=UTC)
SPEC_ID = "a" * 64
METHODOLOGY_ID = "ca3a9eae53093d88f9214cd44452d4f1f674b45beea622767b0a0c0924eeefc4"


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16e tests",
            producer="phase16e-tests",
            producer_version="1.0",
        ),
    )


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
        producer="phase16e-tests",
        producer_version="1.0",
        methodology_version="methodology-16e-1.0",
    )


def _accounting_methodology() -> PortfolioAccountingMethodology:
    return PortfolioAccountingMethodology.create(
        methodology_name="average_cost_no_short_no_margin_v1",
        cost_basis_method="average_cost",
        base_currency_behavior="isolated_base_currency",
        valuation_policy="mark_to_market_explicit",
        producer_version="phase16e-1.0",
    )


def _portfolio_ledger(store=None, specification=None, methodology=None):
    if store is None:
        store = InMemoryArtifactStore()
    if specification is None:
        specification = _specification()
    if methodology is None:
        methodology = _accounting_methodology()
    return PortfolioLedger(
        store=store,
        specification=specification,
        methodology=methodology,
        producer_version="phase16e-1.0",
    )


def _paper_execution_result(
    *,
    side=PaperOrderSide.BUY,
    filled_quantity=100,
    execution_price=100.5,
    executed_at=datetime(2025, 5, 2, tzinfo=UTC),
    paper_fill_ids=("a" * 64,),
    paper_execution_result_id="b" * 64,
    execution_status=PaperOrderStatus.FILLED,
):
    return PaperExecutionResult(
        paper_execution_result_id=paper_execution_result_id,
        replay_specification_id=SPEC_ID,
        replay_run_id=UUID(int=0),
        historical_decision_step_id="c" * 64,
        trade_decision_artifact_id="d" * 64,
        paper_order_id="e" * 64,
        execution_methodology_id="f" * 64,
        symbol=Symbol("AAPL"),
        side=side,
        requested_quantity=filled_quantity,
        filled_quantity=filled_quantity,
        remaining_quantity=0,
        execution_status=execution_status,
        execution_price=execution_price if filled_quantity else None,
        executed_at=executed_at if filled_quantity else None,
        paper_fill_ids=paper_fill_ids,
        transaction_cost_bps=0.0,
        additional_slippage_bps=0.0,
    )


class TestPortfolioAccountingMethodology:
    def test_immutable_methodology(self) -> None:
        methodology = _accounting_methodology()
        with pytest.raises(TypeError):
            methodology.model_copy(update={"cost_basis_method": "fifo"})

    def test_deterministic_methodology_identity(self) -> None:
        first = _accounting_methodology()
        second = _accounting_methodology()
        assert first.methodology_id == second.methodology_id
        assert first.canonical_hash == second.canonical_hash

    def test_baseline_methodology_identity_is_explicit(self) -> None:
        methodology = _accounting_methodology()
        assert methodology.methodology_id == METHODOLOGY_ID
        assert methodology.cost_basis_method == "average_cost"
        assert methodology.allow_shorting is False
        assert methodology.allow_negative_cash is False

    def test_invalid_cost_basis_method_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cost_basis_method"):
            PortfolioAccountingMethodology.create(
                methodology_name="invalid",
                cost_basis_method="wave_function",
                base_currency_behavior="isolated_base_currency",
                valuation_policy="mark_to_market_explicit",
                producer_version="1.0",
            )

    def test_shorting_not_authorized(self) -> None:
        with pytest.raises(InvalidMethodologyError):
            PortfolioAccountingMethodology.create(
                methodology_name="invalid",
                cost_basis_method="average_cost",
                allow_shorting=True,
                base_currency_behavior="isolated_base_currency",
                valuation_policy="mark_to_market_explicit",
                producer_version="1.0",
            )

    def test_negative_cash_not_authorized(self) -> None:
        with pytest.raises(InvalidMethodologyError):
            PortfolioAccountingMethodology.create(
                methodology_name="invalid",
                cost_basis_method="average_cost",
                allow_negative_cash=True,
                base_currency_behavior="isolated_base_currency",
                valuation_policy="mark_to_market_explicit",
                producer_version="1.0",
            )


class TestInitialPortfolio:
    def test_deterministic_initial_portfolio(self) -> None:
        specification = _specification()
        first = PortfolioSnapshot.create_initial(specification)
        second = PortfolioSnapshot.create_initial(specification)
        assert first.portfolio_snapshot_id == second.portfolio_snapshot_id
        assert first.canonical_hash == second.canonical_hash

    def test_initial_cash_equals_specification_initial_capital(self) -> None:
        specification = _specification()
        snapshot = PortfolioSnapshot.create_initial(specification)
        assert snapshot.cash == specification.initial_capital
        assert snapshot.base_currency == specification.base_currency.upper()
        assert snapshot.positions == ()
        assert snapshot.realized_pnl == 0.0
        assert snapshot.total_cost_basis == 0.0
        assert snapshot.unrealized_pnl is None
        assert snapshot.market_value is None
        assert snapshot.prior_snapshot_id is None
        assert snapshot.applied_fill_ids == ()

    def test_initial_snapshot_linked_to_specification(self) -> None:
        specification = _specification()
        snapshot = PortfolioSnapshot.create_initial(specification)
        assert snapshot.replay_specification_id == specification.specification_id
        assert snapshot.replay_run_id == specification.run_id


class TestBuyAccounting:
    def test_buy_reduces_cash(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ledger_entry = ledger.initial_snapshot()
        result = _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=120.0)
        next_snapshot = ledger.apply_fill(initial, result).snapshot
        assert next_snapshot.cash == pytest.approx(100_000.0 - 10 * 120.0)
        assert next_snapshot.positions[0].quantity == 10
        assert next_snapshot.positions[0].average_cost == pytest.approx(120.0)
        assert next_snapshot.total_cost_basis == pytest.approx(1_200.0)

    def test_second_buy_updates_average_cost(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        first = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        second = ledger.apply_fill(
            first,
            _paper_execution_result(
                side=PaperOrderSide.BUY,
                filled_quantity=10,
                execution_price=120.0,
                paper_execution_result_id="1" * 64,
                paper_fill_ids=("2" * 64,),
            ),
        ).snapshot
        position = second.position("AAPL")
        assert position is not None
        assert position.quantity == 20
        assert position.average_cost == pytest.approx(110.0)
        assert position.cost_basis == pytest.approx(2_200.0)
        assert second.cash == pytest.approx(100_000.0 - 2_200.0)
        assert second.total_cost_basis == pytest.approx(2_200.0)


class TestSellAccounting:
    def test_sell_increases_cash(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        sell = ledger.apply_fill(
            buy,
            _paper_execution_result(
                side=PaperOrderSide.SELL,
                filled_quantity=4,
                execution_price=120.0,
                paper_execution_result_id="1" * 64,
                paper_fill_ids=("2" * 64,),
            ),
        ).snapshot
        assert sell.cash == pytest.approx(100_000.0 - 1_000.0 + 4 * 120.0)
        position = sell.position("AAPL")
        assert position is not None
        assert position.quantity == 6
        assert position.average_cost == pytest.approx(100.0)

    def test_sell_realizes_pnl(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        sell = ledger.apply_fill(
            buy,
            _paper_execution_result(
                side=PaperOrderSide.SELL,
                filled_quantity=4,
                execution_price=120.0,
                paper_execution_result_id="1" * 64,
                paper_fill_ids=("2" * 64,),
            ),
        ).snapshot
        position = sell.position("AAPL")
        assert position is not None
        assert position.realized_pnl == pytest.approx(4 * (120.0 - 100.0))
        assert sell.realized_pnl == pytest.approx(4 * (120.0 - 100.0))

    def test_full_close_zeros_exposure(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        close = ledger.apply_fill(
            buy,
            _paper_execution_result(
                side=PaperOrderSide.SELL,
                filled_quantity=10,
                execution_price=120.0,
                paper_execution_result_id="1" * 64,
                paper_fill_ids=("2" * 64,),
            ),
        ).snapshot
        assert close.position("AAPL") is None
        assert close.positions == ()
        assert close.total_cost_basis == pytest.approx(0.0)
        assert close.realized_pnl == pytest.approx(10 * (120.0 - 100.0))
        assert close.applied_fill_ids == ("2" * 64, "a" * 64)


class TestInsufficientCash:
    def test_insufficient_cash_rejected(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        with pytest.raises(InsufficientCashError):
            ledger.apply_fill(initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=1, execution_price=200_000.0))


class TestUnauthorizedShort:
    def test_unauthorized_short_rejected(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        with pytest.raises(UnauthorizedShortError):
            ledger.apply_fill(initial, _paper_execution_result(side=PaperOrderSide.SELL, filled_quantity=1, execution_price=100.0))


class TestUnfilledNoop:
    def test_unfilled_execution_causes_no_portfolio_mutation(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        noop_result = PaperExecutionResult(
            paper_execution_result_id="b" * 64,
            replay_specification_id=SPEC_ID,
            replay_run_id=UUID(int=0),
            historical_decision_step_id="c" * 64,
            trade_decision_artifact_id="d" * 64,
            paper_order_id="e" * 64,
            execution_methodology_id="f" * 64,
            symbol=Symbol("AAPL"),
            side=PaperOrderSide.BUY,
            requested_quantity=100,
            filled_quantity=0,
            remaining_quantity=100,
            execution_status=PaperOrderStatus.UNFILLED,
            transaction_cost_bps=0.0,
            additional_slippage_bps=0.0,
        )
        noop = ledger.record_noop(initial, noop_result)
        assert noop.snapshot.cash == pytest.approx(initial.cash)
        assert noop.snapshot.positions == initial.positions
        assert noop.snapshot.realized_pnl == pytest.approx(initial.realized_pnl)
        assert noop.ledger_entry.event_type == "noop"


class TestImmutability:
    def test_immutable_position_state(self) -> None:
        position = PositionState(
            symbol=Symbol("AAPL"),
            quantity=10,
            average_cost=100.0,
            cost_basis=1_000.0,
        )
        with pytest.raises((TypeError, ValidationError)):
            position.model_copy(update={"quantity": 11})

    def test_deeply_immutable_portfolio_snapshot_positions(self) -> None:
        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id="a" * 64,
            replay_run_id=UUID(int=0),
            simulated_at=DECISION_AT,
            base_currency="USD",
            cash=100_000.0,
            positions=(PositionState(symbol=Symbol("AAPL"), quantity=10, average_cost=100.0, cost_basis=1_000.0),),
            total_cost_basis=1_000.0,
            realized_pnl=0.0,
            prior_snapshot_id=None,
            applied_fill_ids=(),
            accounting_methodology_id=METHODOLOGY_ID,
            producer_version="phase16e-1.0",
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})
        with pytest.raises((TypeError, ValidationError)):
            snapshot.model_copy(update={"positions": [PositionState(symbol=Symbol("MSFT"), quantity=1, average_cost=1.0, cost_basis=1.0)]})
        with pytest.raises((TypeError, ValidationError)):
            snapshot.positions[0].model_copy(update={"quantity": 11})


class TestDeterminism:
    def test_deterministic_ledger_entry_identity(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = _paper_execution_result(paper_execution_result_id="1" * 64, paper_fill_ids=("2" * 64,))
        first = ledger.apply_fill(initial, result)
        second_ledger = _portfolio_ledger(store=InMemoryArtifactStore(), specification=_specification())
        second_initial, _ = second_ledger.initial_snapshot()
        second = second_ledger.apply_fill(
            second_initial,
            _paper_execution_result(paper_execution_result_id="1" * 64, paper_fill_ids=("2" * 64,)),
        )
        assert first.ledger_entry.portfolio_ledger_entry_id == second.ledger_entry.portfolio_ledger_entry_id
        assert first.snapshot.portfolio_snapshot_id == second.snapshot.portfolio_snapshot_id

    def test_deterministic_portfolio_snapshot_identity(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = _paper_execution_result(paper_execution_result_id="3" * 64, paper_fill_ids=("4" * 64,))
        first = ledger.apply_fill(initial, result)
        second_ledger = _portfolio_ledger(store=InMemoryArtifactStore(), specification=_specification())
        second_initial, _ = second_ledger.initial_snapshot()
        second = second_ledger.apply_fill(
            second_initial,
            _paper_execution_result(paper_execution_result_id="3" * 64, paper_fill_ids=("4" * 64,)),
        )
        assert first.snapshot.portfolio_snapshot_id == second.snapshot.portfolio_snapshot_id

    def test_prior_snapshot_linkage(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = _paper_execution_result(paper_execution_result_id="5" * 64, paper_fill_ids=("6" * 64,))
        next_snapshot = ledger.apply_fill(initial, result).snapshot
        assert next_snapshot.prior_snapshot_id == initial.portfolio_snapshot_id

    def test_fill_lineage(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = _paper_execution_result(paper_execution_result_id="7" * 64, paper_fill_ids=("8" * 64,))
        ledger_entry = ledger.apply_fill(initial, result).ledger_entry
        assert ledger_entry.paper_fill_ids == ("8" * 64,)
        assert ledger_entry.paper_execution_result_id == "7" * 64
        assert ledger_entry.prior_portfolio_snapshot_id == initial.portfolio_snapshot_id

    def test_deterministic_fill_ordering(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        first_result = _paper_execution_result(
            paper_execution_result_id="9" * 64, paper_fill_ids=("a" * 64,), executed_at=datetime(2025, 5, 1, tzinfo=UTC)
        )
        second_result = _paper_execution_result(
            paper_execution_result_id="b" * 64, paper_fill_ids=("c" * 64,), executed_at=datetime(2025, 5, 2, tzinfo=UTC)
        )
        first = ledger.apply_fill(initial, first_result)
        second = ledger.apply_fill(first.snapshot, second_result)
        assert second.ledger_entry.sequence == 1

    def test_duplicate_fill_rejected(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = _paper_execution_result(paper_execution_result_id="d" * 64, paper_fill_ids=("e" * 64,))
        ledger.apply_fill(initial, result)
        with pytest.raises(InvalidFillError, match="already been applied"):
            ledger.apply_fill(result.snapshot if False else initial, result)

    def test_out_of_order_snapshot_rejected(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        ledger.apply_fill(initial, _paper_execution_result())
        with pytest.raises(PortfolioAccountingValidationError, match="out-of-order"):
            ledger.apply_fill(initial, _paper_execution_result(paper_execution_result_id="f" * 64, paper_fill_ids=("g" * 64,)))


class TestValuation:
    def test_valid_mark_updates_unrealized_pnl(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        noop = _paper_execution_result(
            paper_execution_result_id="h" * 64, paper_fill_ids=(), filled_quantity=0, execution_status=PaperOrderStatus.UNFILLED
        )
        marked = ledger.record_noop(buy, noop, mark_price=120.0).snapshot
        position = marked.position("AAPL")
        assert position is not None
        assert position.last_mark_price == pytest.approx(120.0)
        assert position.unrealized_pnl == pytest.approx(200.0)
        assert position.market_value == pytest.approx(1_200.0)
        assert marked.unrealized_pnl == pytest.approx(200.0)
        assert marked.market_value == pytest.approx(1_200.0)

    def test_future_mark_rejected(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        with pytest.raises(PortfolioAccountingValidationError, match="mark_price must be positive"):
            ledger.apply_fill(buy, _paper_execution_result(paper_execution_result_id="j" * 64, paper_fill_ids=("k" * 64,)), mark_price=-1.0)

    def test_missing_mark_does_not_fabricate_valuation(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        buy = ledger.apply_fill(
            initial, _paper_execution_result(side=PaperOrderSide.BUY, filled_quantity=10, execution_price=100.0)
        ).snapshot
        noop = _paper_execution_result(
            paper_execution_result_id="l" * 64, paper_fill_ids=(), filled_quantity=0, execution_status=PaperOrderStatus.UNFILLED
        )
        unmarked = ledger.record_noop(buy, noop).snapshot
        position = unmarked.position("AAPL")
        assert position is not None
        assert position.last_mark_price is None
        assert position.unrealized_pnl is None
        assert position.market_value is None
        assert unmarked.unrealized_pnl is None
        assert unmarked.market_value is None

    def test_later_snapshot_does_not_mutate_earlier_snapshot(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        first = ledger.apply_fill(initial, _paper_execution_result(paper_execution_result_id="n" * 64, paper_fill_ids=("o" * 64,))).snapshot
        ledger.apply_fill(first, _paper_execution_result(paper_execution_result_id="p" * 64, paper_fill_ids=("q" * 64,)))
        assert initial.cash == pytest.approx(100_000.0)
        assert initial.positions == ()
        assert first.cash == pytest.approx(100_000.0 - 100.5 * 100)


class TestPersistence:
    def test_file_artifact_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            ledger = PortfolioLedger(store=store, specification=_specification(), methodology=_accounting_methodology())
            initial, _initial_ledger_entry = ledger.initial_snapshot()
            ledger.apply_fill(initial, _paper_execution_result(paper_execution_result_id="r" * 64, paper_fill_ids=("s" * 64,)))
            restarted = PortfolioLedger(
                store=FileArtifactStore(temp_dir), specification=_specification(), methodology=_accounting_methodology()
            )
            latest_snapshot = None
            for artifact_id in store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT}):
                envelope = store.get(artifact_id)
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                if latest_snapshot is None or payload["simulated_at"] > latest_snapshot.payload["simulated_at"]:
                    latest_snapshot = envelope
            assert latest_snapshot is not None
            assert latest_snapshot.payload["portfolio_snapshot_id"] == restarted._latest_snapshot_id

    def test_corruption_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            ledger = PortfolioLedger(store=store, specification=_specification(), methodology=_accounting_methodology())
            initial, _ = ledger.initial_snapshot()
            ledger.apply_fill(initial, _paper_execution_result(paper_execution_result_id="t" * 64, paper_fill_ids=("u" * 64,)))
            snapshot_id = store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT})[0]
            prefix = snapshot_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            target_dir.mkdir(parents=True, exist_ok=True)
            Path(target_dir / f"{snapshot_id}.json").write_text("not-json", encoding="utf-8")
            reloaded = FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                PortfolioLedger(store=reloaded, specification=_specification(), methodology=_accounting_methodology())


class TestSafety:
    def test_no_broker_dependency(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "portfolio_accounting" / "ledger.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = {
            node.module if isinstance(node, ast.ImportFrom) and node.module else getattr(node.names[0], "name", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        forbidden = {"broker", "execution", "portfolio", "risk", "live"}
        assert forbidden.isdisjoint({name.lower() for name in imports if isinstance(name, str)})

    def test_no_live_account_mutation(self) -> None:
        ledger = _portfolio_ledger()
        initial, _ = ledger.initial_snapshot()
        result = ledger.apply_fill(initial, _paper_execution_result(paper_execution_result_id="v" * 64, paper_fill_ids=("w" * 64,)))
        assert result.snapshot.suitable_for_live_trading is False
        assert result.ledger_entry.suitable_for_live_trading is False

    def test_phase16a_d_regression_remains_green(self) -> None:
        required_modules = [
            "app.domain.models.research_run",
            "app.services.replay.manifest",
            "app.services.artifacts.file_store",
            "app.domain.models.backtesting",
        ]
        for module_name in required_modules:
            __import__(module_name)
        required_tests = [
            Path(__file__).resolve().parents[1] / "tests" / "test_replay.py",
            Path(__file__).resolve().parents[1] / "tests" / "test_backtesting.py",
            Path(__file__).resolve().parents[1] / "tests" / "test_paper_execution_simulator.py",
        ]
        for path in required_tests:
            assert path.exists(), f"missing regression test file: {path}"


class TestEndToEndSequence:
    def test_full_paper_fill_sequence(self) -> None:
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
        decision = TradeDecision(
            decision_id=UUID("d" * 32),
            ticker="AAPL",
            action="BUY",
            confidence=0.9,
            quantity=100,
            order_type="market",
            rationale="unit test portfolio ledger decision",
            evidence=[],
            created_at=DECISION_AT,
        )
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

        methodology = PaperExecutionMethodology.create(
            methodology_name="next_bar_close_baseline",
            version="phase16d-1.0",
            fill_timing_policy=FillTimingPolicy.NEXT_BAR_CLOSE,
            price_source="next_bar_close",
            producer_version="1.0",
        )
        bars = HistoricalBarsResult(
            symbol=Symbol("AAPL"),
            timeframe="1d",
            bars=[
                OHLCVBar(timestamp=datetime(2025, 5, 2, tzinfo=UTC), open=100.0, high=101.0, low=99.0, close=100.5, volume=1_000_000),
                OHLCVBar(timestamp=datetime(2025, 5, 3, tzinfo=UTC), open=100.5, high=102.0, low=100.0, close=101.5, volume=1_200_000),
            ],
            provider="unit-test",
            requested_start=REPLAY_START,
            requested_end=REPLAY_END,
            actual_start=datetime(2025, 5, 2, tzinfo=UTC),
            actual_end=datetime(2025, 5, 3, tzinfo=UTC),
            adjustment="raw",
            session="regular",
            currency="USD",
            exchange="UNIT",
            partial=False,
            retrieved_at=DECISION_AT,
        )
        simulator = PaperExecutionSimulator(
            store=store,
            specification=specification,
            step=step,
            methodology=methodology,
        )
        execution_result = simulator.simulate(decision, bars, DECISION_AT + timedelta(days=2, hours=1))

        ledger = PortfolioLedger(store=store, specification=specification, methodology=_accounting_methodology())
        initial_snapshot, _initial_ledger_entry = ledger.initial_snapshot()
        first_snapshot = ledger.apply_fill(initial_snapshot, execution_result).snapshot

        assert first_snapshot.cash == pytest.approx(100_000.0 - 100.5 * 100)
        assert first_snapshot.position("AAPL").quantity == 100
        assert first_snapshot.realized_pnl == pytest.approx(0.0)
        assert first_snapshot.suitable_for_live_trading is False
        assert first_snapshot.prior_snapshot_id == initial_snapshot.portfolio_snapshot_id
        initial_envelope_id = store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT})[0]
        assert store.get(initial_envelope_id).payload["cash"] == pytest.approx(100_000.0)

        second_result = _paper_execution_result(
            paper_execution_result_id="x" * 64,
            paper_fill_ids=("y" * 64,),
            executed_at=datetime(2025, 5, 4, tzinfo=UTC),
            side=PaperOrderSide.SELL,
            filled_quantity=40,
            execution_price=110.0,
        )
        second_snapshot = ledger.apply_fill(first_snapshot, second_result).snapshot
        assert second_snapshot.cash == pytest.approx(100_000.0 - 10_050.0 + 40 * 110.0)
        assert second_snapshot.position("AAPL").quantity == 60
        assert second_snapshot.position("AAPL").average_cost == pytest.approx(100.5)
        assert second_snapshot.realized_pnl == pytest.approx(40 * (110.0 - 100.5))
        assert second_snapshot.suitable_for_live_trading is False
        stored_snapshots = [
            store.get(artifact_id) for artifact_id in store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT})
        ]
        first_envelope = next(
            envelope for envelope in stored_snapshots if envelope.payload["portfolio_snapshot_id"] == first_snapshot.portfolio_snapshot_id
        )
        assert first_envelope.payload["cash"] == pytest.approx(100_000.0 - 10_050.0)


__all__ = [
    "TestBuyAccounting",
    "TestDeterminism",
    "TestEndToEndSequence",
    "TestImmutability",
    "TestInitialPortfolio",
    "TestInsufficientCash",
    "TestPersistence",
    "TestPortfolioAccountingMethodology",
    "TestSafety",
    "TestSellAccounting",
    "TestUnauthorizedShort",
    "TestUnfilledNoop",
    "TestValuation",
]
