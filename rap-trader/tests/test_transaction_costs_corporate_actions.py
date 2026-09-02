"""Phase 16F transaction-cost and corporate-action tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.domain.models.artifact import ArtifactType
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.paper_execution.contracts import (
    PaperExecutionResult,
    PaperFill,
    PaperOrderSide,
    PaperOrderStatus,
)
from app.services.portfolio_accounting.costs import (
    build_execution_cost_assessment,
    compute_commission,
    compute_effective_price,
    compute_side_cost_bps,
    compute_total_transaction_cost,
)
from app.services.portfolio_accounting.errors import (
    DuplicateCorporateActionError,
    DuplicateCostApplicationError,
    FutureCorporateActionError,
    IncompatiblePriceAdjustmentError,
    InvalidCostInputError,
    PortfolioAccountingValidationError,
    UnauthorizedShortError,
    UnsupportedCorporateActionError,
)
from app.services.portfolio_accounting.ledger import PortfolioLedger
from app.services.portfolio_accounting.models import (
    PortfolioAccountingMethodology,
    PortfolioSnapshot,
    PositionState,
)
from app.services.portfolio_accounting.phase16f import Phase16FService
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    CorporateActionStatus,
    CorporateActionType,
    TransactionCostMethodology,
)

REPLAY_START = datetime(2025, 4, 20, tzinfo=UTC)
REPLAY_END = datetime(2025, 5, 5, tzinfo=UTC)
DECISION_AT = datetime(2025, 5, 1, tzinfo=UTC)
FILL_TIMESTAMP = datetime(2025, 4, 21, tzinfo=UTC)


def _specification() -> HistoricalReplaySpecification:
    if not hasattr(_specification, "value"):
        _specification.value = HistoricalReplaySpecification.create(
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
            producer="phase16f-tests",
            producer_version="1.0",
            methodology_version="methodology-16f-1.0",
        )
    return _specification.value


def _accounting_methodology() -> PortfolioAccountingMethodology:
    return PortfolioAccountingMethodology.create(
        methodology_name="average_cost_no_short_no_margin_v1",
        cost_basis_method="average_cost",
        base_currency_behavior="isolated_base_currency",
        valuation_policy="mark_to_market_explicit",
        producer_version="phase16e-1.0",
    )


def _ledger(store=None) -> PortfolioLedger:
    return PortfolioLedger(
        store=store or InMemoryArtifactStore(),
        specification=_specification(),
        methodology=_accounting_methodology(),
        producer_version="phase16e-1.0",
    )


def _methodology(
    *,
    methodology_id: str | None = None,
    spread_bps: float = 10.0,
    slippage_bps: float = 5.0,
    fixed_commission: float = 1.0,
    per_unit_commission: float = 0.01,
    commission_bps: float = 0.5,
    minimum_commission: float | None = 0.5,
) -> TransactionCostMethodology:
    return TransactionCostMethodology.create(
        methodology_id=methodology_id or ("m" * 64),
        methodology_name="baseline",
        version="1.0",
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        fixed_commission=fixed_commission,
        per_unit_commission=per_unit_commission,
        commission_bps=commission_bps,
        minimum_commission=minimum_commission,
        producer_version="phase16f-1.0",
    )


def _paper_fill(
    *,
    quantity: int = 100,
    execution_price: float = 100.0,
    side: PaperOrderSide = PaperOrderSide.BUY,
) -> PaperFill:
    return PaperFill.create(
        paper_fill_id=f"f{_paper_fill._counter:063x}",
        paper_order_id="o" * 64,
        replay_specification_id="a" * 64,
        symbol="AAPL",
        side=side,
        quantity=quantity,
        execution_price=execution_price,
        executed_at=FILL_TIMESTAMP,
        source_bar_timestamp=FILL_TIMESTAMP,
        methodology_id="m" * 64,
        status="full",
    )


_paper_fill._counter = 0


_REPLAY_RUN_ID = UUID(int=0x16F)


def _execution_result_filled(
    *,
    side: PaperOrderSide = PaperOrderSide.BUY,
    quantity: int = 100,
    execution_price: float = 100.0,
    executed_at: datetime | None = None,
    paper_execution_result_id: str | None = None,
    paper_fill_ids: tuple[str, ...] | None = None,
    replay_specification_id: str = "a" * 64,
    replay_run_id: UUID = _REPLAY_RUN_ID,
) -> PaperExecutionResult:
    now = executed_at or FILL_TIMESTAMP
    execution_id = paper_execution_result_id or f"r{_execution_result_filled._counter:063x}"
    _execution_result_filled._counter += 1
    fill_ids = paper_fill_ids or (f"f{_execution_result_filled._counter:063x}",)
    return PaperExecutionResult.create(
        paper_execution_result_id=execution_id,
        replay_specification_id=replay_specification_id,
        replay_run_id=replay_run_id,
        historical_decision_step_id="d" * 64,
        trade_decision_artifact_id="t" * 64,
        paper_order_id="o" * 64,
        execution_methodology_id="m" * 64,
        symbol="AAPL",
        side=side,
        requested_quantity=quantity,
        filled_quantity=quantity,
        remaining_quantity=0,
        execution_status=PaperOrderStatus.FILLED,
        execution_price=execution_price,
        executed_at=now,
        paper_fill_ids=fill_ids,
        transaction_cost_bps=0.0,
        additional_slippage_bps=0.0,
    )


_execution_result_filled._counter = 0


def _corporate_action(
    *,
    action_type: str = CorporateActionType.STOCK_SPLIT.value,
    effective_at: datetime | None = None,
    ex_date: datetime | None = None,
    payment_at: datetime | None = None,
    split_ratio: tuple[int, int] | None = None,
    dividend_per_share: float | None = None,
    price_adjustment_convention: str = "raw",
    replay_specification_id: str | None = None,
    replay_run_id: Any | None = None,
) -> CorporateActionEvent:
    if action_type == CorporateActionType.STOCK_SPLIT.value and split_ratio is None:
        split_ratio = (2, 1)
    if action_type == CorporateActionType.CASH_DIVIDEND.value and dividend_per_share is None:
        dividend_per_share = 1.5
    return CorporateActionEvent.create(
        symbol="AAPL",
        action_type=action_type,
        announced_at=effective_at or FILL_TIMESTAMP,
        effective_at=effective_at or FILL_TIMESTAMP,
        ex_date=ex_date or FILL_TIMESTAMP,
        payment_at=payment_at or FILL_TIMESTAMP,
        split_ratio=split_ratio,
        dividend_per_share=dividend_per_share,
        currency="USD",
        status=CorporateActionStatus.ANNOUNCED.value,
        price_adjustment_convention=price_adjustment_convention,
        replay_specification_id=replay_specification_id or "a" * 64,
        replay_run_id=replay_run_id or UUID(int=0x16F),
        methodology_version="1.0",
        producer_version="phase16f-1.0",
    )


def _snapshot_position(snapshot: PortfolioSnapshot, symbol: str) -> PositionState | None:
    for position in snapshot.positions:
        if str(position.symbol) == symbol:
            return position
    return None


class TestTransactionCostMethodology:
    def test_immutable_methodology(self) -> None:
        methodology = _methodology()
        with pytest.raises((TypeError, ValueError)):
            methodology.methodology_name = "mutated"

    def test_deterministic_methodology_id(self) -> None:
        first = _methodology(methodology_id="a" * 64)
        second = _methodology(methodology_id="a" * 64)
        assert first.methodology_id == second.methodology_id

    def test_zero_cost_methodology(self) -> None:
        methodology = _methodology(
            spread_bps=0.0, slippage_bps=0.0, fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None
        )
        assert compute_side_cost_bps(methodology=methodology) == 0.0
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.BUY, methodology=methodology) == 100.0
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.SELL, methodology=methodology) == 100.0
        assert compute_commission(notional=10000.0, quantity=100, methodology=methodology) == 0.0
        assert compute_total_transaction_cost(reference_notional=10000.0, effective_notional=10000.0, commission=0.0) == 0.0

    def test_fixed_commission(self) -> None:
        methodology = _methodology(fixed_commission=2.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None)
        assert compute_commission(notional=10000.0, quantity=100, methodology=methodology) == 2.0

    def test_per_unit_commission(self) -> None:
        methodology = _methodology(fixed_commission=0.0, per_unit_commission=0.1, commission_bps=0.0, minimum_commission=None)
        assert compute_commission(notional=10000.0, quantity=100, methodology=methodology) == 10.0

    def test_bps_commission(self) -> None:
        methodology = _methodology(fixed_commission=0.0, per_unit_commission=0.0, commission_bps=1.0, minimum_commission=None)
        assert compute_commission(notional=10000.0, quantity=100, methodology=methodology) == 1.0

    def test_minimum_commission(self) -> None:
        methodology = _methodology(fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.1, minimum_commission=1.0)
        assert compute_commission(notional=10000.0, quantity=100, methodology=methodology) == 1.0

    def test_buy_spread_direction(self) -> None:
        methodology = _methodology(
            spread_bps=20.0, slippage_bps=0.0, fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None
        )
        assert compute_side_cost_bps(methodology=methodology) == 10.0
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.BUY, methodology=methodology) == 100.1
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.SELL, methodology=methodology) == 99.9

    def test_sell_spread_direction(self) -> None:
        methodology = _methodology(
            spread_bps=0.0, slippage_bps=10.0, fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None
        )
        assert compute_side_cost_bps(methodology=methodology) == 10.0
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.BUY, methodology=methodology) == 100.1
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.SELL, methodology=methodology) == 99.9

    def test_deterministic_slippage(self) -> None:
        methodology = _methodology(
            spread_bps=0.0, slippage_bps=20.0, fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None
        )
        assert compute_side_cost_bps(methodology=methodology) == 20.0
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.BUY, methodology=methodology) == 100.2
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.SELL, methodology=methodology) == 99.8

    def test_deterministic_effective_price(self) -> None:
        methodology = _methodology(
            spread_bps=10.0, slippage_bps=5.0, fixed_commission=0.0, per_unit_commission=0.0, commission_bps=0.0, minimum_commission=None
        )
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.BUY, methodology=methodology) == 100.1
        assert compute_effective_price(reference_price=100.0, side=PaperOrderSide.SELL, methodology=methodology) == 99.9

    def test_deterministic_assessment_id(self) -> None:
        paper_fill = _paper_fill()
        first = build_execution_cost_assessment(
            paper_fill=paper_fill,
            paper_order_id="o" * 64,
            methodology=_methodology(),
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id="a" * 64,
            replay_run_id=UUID(int=0x16F),
        )
        second = build_execution_cost_assessment(
            paper_fill=paper_fill,
            paper_order_id="o" * 64,
            methodology=_methodology(),
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id="a" * 64,
            replay_run_id=UUID(int=0x16F),
        )
        assert first.assessment_id == second.assessment_id

    def test_immutable_assessment(self) -> None:
        assessment = build_execution_cost_assessment(
            paper_fill=_paper_fill(),
            paper_order_id="o" * 64,
            methodology=_methodology(),
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id="a" * 64,
            replay_run_id=UUID(int=0x16F),
        )
        with pytest.raises((TypeError, ValueError)):
            assessment.commission = 999.0

    def test_negative_fixed_commission_rejected(self) -> None:
        with pytest.raises(InvalidCostInputError):
            _methodology(fixed_commission=-1.0)


class TestCostPortfolioIntegration:
    def test_original_paper_fill_unchanged(self) -> None:
        fill = _paper_fill(quantity=100, execution_price=100.0)
        original_price = fill.execution_price
        original_quantity = fill.quantity
        build_execution_cost_assessment(
            paper_fill=fill,
            paper_order_id="o" * 64,
            methodology=_methodology(),
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id="a" * 64,
            replay_run_id=UUID(int=0x16F),
        )
        assert fill.execution_price == original_price
        assert fill.quantity == original_quantity

    def test_prior_snapshot_unchanged(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled())
        prior_snapshot = applied.snapshot
        prior_snapshot_id = prior_snapshot.portfolio_snapshot_id
        prior_cash = prior_snapshot.cash

        methodology = _methodology()
        assessment = service.assess_execution_cost(
            paper_fill=_paper_fill(),
            paper_order_id="o" * 64,
            methodology=methodology,
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
        )
        applied_cost = service.apply_cost_assessment(prior_snapshot, assessment)
        assert prior_snapshot.cash == pytest.approx(prior_cash)
        persisted_snapshot_ids = [
            artifact_id
            for artifact_id in ledger.store.list_ids()
            if ledger.store.get(artifact_id).artifact_type == ArtifactType.PORTFOLIO_SNAPSHOT
            and ledger.store.get(artifact_id).payload.get("portfolio_snapshot_id") == prior_snapshot_id
        ]
        assert persisted_snapshot_ids, "prior snapshot artifact should still be persisted"
        persisted = ledger.store.get(persisted_snapshot_ids[-1])
        assert persisted.payload["cash"] == pytest.approx(prior_cash)
        assert applied_cost.snapshot.cash == pytest.approx(prior_cash - assessment.total_transaction_cost)

    def test_cost_application_cannot_apply_twice(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled())
        prior_snapshot = applied.snapshot

        methodology = _methodology()
        assessment = service.assess_execution_cost(
            paper_fill=_paper_fill(),
            paper_order_id="o" * 64,
            methodology=methodology,
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
        )
        applied_cost = service.apply_cost_assessment(prior_snapshot, assessment)
        assert applied_cost.applied is True
        with pytest.raises(DuplicateCostApplicationError):
            service.apply_cost_assessment(applied_cost.snapshot, assessment)


class TestCorporateActions:
    def test_future_corporate_action_not_applied_early(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        with pytest.raises(FutureCorporateActionError):
            service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP - timedelta(seconds=1))

    def test_two_for_one_split_quantity_and_cost_basis(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot
        prior_position = _snapshot_position(prior_snapshot, "AAPL")
        assert prior_position is not None
        assert prior_position.quantity == 100
        assert prior_position.cost_basis == pytest.approx(10000.0)

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        applied = service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)
        new_position = _snapshot_position(applied.snapshot, "AAPL")
        assert new_position is not None
        assert new_position.quantity == 200
        assert new_position.average_cost == pytest.approx(50.0)
        assert new_position.cost_basis == pytest.approx(10000.0)

    def test_split_preserves_total_cost_basis(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot
        prior_position = _snapshot_position(prior_snapshot, "AAPL")
        assert prior_position is not None
        total_cost_basis_before = prior_snapshot.total_cost_basis

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(3, 1),
        )
        applied = service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)
        new_position = _snapshot_position(applied.snapshot, "AAPL")
        assert new_position is not None
        assert new_position.quantity == 300
        assert applied.snapshot.total_cost_basis == pytest.approx(total_cost_basis_before)
        assert new_position.cost_basis == pytest.approx(total_cost_basis_before)

    def test_split_does_not_fabricate_pnl(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        applied = service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)
        assert applied.snapshot.realized_pnl == pytest.approx(0.0)
        assert applied.snapshot.unrealized_pnl is None

    def test_duplicate_split_prevented(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        applied_split = service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)
        assert applied_split.applied is True
        with pytest.raises(DuplicateCorporateActionError):
            service.apply_corporate_action(applied_split.snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)

    def test_incompatible_adjusted_price_rejected(self) -> None:
        ledger = PortfolioLedger(
            store=InMemoryArtifactStore(),
            specification=_specification(),
            methodology=_accounting_methodology(),
            price_adjustment_convention="split_adjusted",
            producer_version="phase16e-1.0",
        )
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        with pytest.raises(IncompatiblePriceAdjustmentError):
            service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)

    def test_unsupported_action_type_rejected(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type="merger",
            effective_at=FILL_TIMESTAMP,
        )
        with pytest.raises(UnsupportedCorporateActionError):
            service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)

    def test_dividend_entitlement_uses_ex_date_position(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.CASH_DIVIDEND.value,
            effective_at=FILL_TIMESTAMP,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
            dividend_per_share=1.5,
        )
        entitlement = service.create_dividend_entitlement(
            corporate_action=corporate_action,
            snapshot_id=prior_snapshot.portfolio_snapshot_id,
            symbol="AAPL",
            entitled_quantity=100,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
        )
        assert entitlement.entitled_quantity == 100
        assert entitlement.gross_cash_amount == pytest.approx(150.0)

    def test_dividend_not_paid_before_payment_date(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.CASH_DIVIDEND.value,
            effective_at=FILL_TIMESTAMP,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
            dividend_per_share=1.5,
        )
        entitlement = service.create_dividend_entitlement(
            corporate_action=corporate_action,
            snapshot_id=prior_snapshot.portfolio_snapshot_id,
            symbol="AAPL",
            entitled_quantity=100,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP + __import__("datetime").timedelta(days=1),
        )
        with pytest.raises(FutureCorporateActionError):
            service.apply_dividend_payment(prior_snapshot, entitlement, simulated_at=FILL_TIMESTAMP)

    def test_dividend_cash_credited_at_payment_date(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot
        prior_cash = prior_snapshot.cash

        corporate_action = _corporate_action(
            action_type=CorporateActionType.CASH_DIVIDEND.value,
            effective_at=FILL_TIMESTAMP,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
            dividend_per_share=1.5,
        )
        entitlement = service.create_dividend_entitlement(
            corporate_action=corporate_action,
            snapshot_id=prior_snapshot.portfolio_snapshot_id,
            symbol="AAPL",
            entitled_quantity=100,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
        )
        applied = service.apply_dividend_payment(prior_snapshot, entitlement, simulated_at=FILL_TIMESTAMP)
        assert applied.snapshot.cash == pytest.approx(prior_cash + entitlement.gross_cash_amount)

    def test_later_position_changes_do_not_alter_prior_entitlement(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.CASH_DIVIDEND.value,
            effective_at=FILL_TIMESTAMP,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
            dividend_per_share=1.5,
        )
        entitlement = service.create_dividend_entitlement(
            corporate_action=corporate_action,
            snapshot_id=prior_snapshot.portfolio_snapshot_id,
            symbol="AAPL",
            entitled_quantity=100,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
        )
        ledger.apply_fill(prior_snapshot, _execution_result_filled(quantity=50, execution_price=100.0))
        assert entitlement.entitled_quantity == 100

    def test_dividend_cannot_be_paid_twice(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.CASH_DIVIDEND.value,
            effective_at=FILL_TIMESTAMP,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
            dividend_per_share=1.5,
        )
        entitlement = service.create_dividend_entitlement(
            corporate_action=corporate_action,
            snapshot_id=prior_snapshot.portfolio_snapshot_id,
            symbol="AAPL",
            entitled_quantity=100,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
            ex_date=FILL_TIMESTAMP,
            payment_at=FILL_TIMESTAMP,
        )
        applied_payment = service.apply_dividend_payment(prior_snapshot, entitlement, simulated_at=FILL_TIMESTAMP)
        assert applied_payment.applied is True
        with pytest.raises(DuplicateCostApplicationError):
            service.apply_dividend_payment(applied_payment.snapshot, entitlement, simulated_at=FILL_TIMESTAMP)

    def test_fractional_share_policy_fails_closed(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=1, execution_price=100.0))
        prior_snapshot = applied.snapshot

        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(3, 2),
        )
        with pytest.raises((PortfolioAccountingValidationError, ValueError)):
            service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)


class TestPersistenceAndSafety:
    def test_file_artifact_store_restart(self, tmp_path: str) -> None:
        store = FileArtifactStore(str(Path(tmp_path) / "store"))

        ledger = PortfolioLedger(store=store, specification=_specification(), methodology=_accounting_methodology())
        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))

        reopened = PortfolioLedger(store=store, specification=_specification(), methodology=_accounting_methodology())
        latest_snapshot_id = reopened._latest_snapshot_id
        assert latest_snapshot_id == applied.snapshot.portfolio_snapshot_id

    def test_corruption_propagation(self, tmp_path: str) -> None:
        store = FileArtifactStore(str(Path(tmp_path) / "store"))

        ledger = PortfolioLedger(store=store, specification=_specification(), methodology=_accounting_methodology())
        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))

        persisted_snapshot_ids = [
            artifact_id
            for artifact_id in store.list_ids()
            if store.get(artifact_id).artifact_type == ArtifactType.PORTFOLIO_SNAPSHOT
            and store.get(artifact_id).payload.get("portfolio_snapshot_id") == applied.snapshot.portfolio_snapshot_id
        ]
        assert persisted_snapshot_ids, "persisted portfolio snapshot artifact was not written"
        artifact_path = Path(store._filepath(persisted_snapshot_ids[-1]))
        envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
        envelope["payload"]["cash"] = -1.0
        artifact_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")

        with pytest.raises(ArtifactCorruptedError):
            store.get(persisted_snapshot_ids[-1])


class TestPhase16ERegressions:
    def test_phase16e_baseline_unchanged(self) -> None:
        ledger = _ledger()
        snapshot, ledger_entry = ledger.initial_snapshot()
        assert snapshot.cash == pytest.approx(100_000.0)
        assert snapshot.positions == ()
        assert ledger_entry.event_type == "initial"

    def test_standard_buy_sell_average_cost(self) -> None:
        ledger = _ledger()
        snapshot, _ = ledger.initial_snapshot()
        first = ledger.apply_fill(
            snapshot,
            _execution_result_filled(quantity=100, execution_price=100.0),
        )
        second = ledger.apply_fill(
            first.snapshot,
            _execution_result_filled(
                quantity=100,
                execution_price=110.0,
                paper_execution_result_id="r" * 63 + "1",
                paper_fill_ids=("f" * 63 + "1",),
            ),
        )
        position = _snapshot_position(second.snapshot, "AAPL")
        assert position is not None
        assert position.quantity == 200
        assert position.average_cost == pytest.approx(105.0)
        assert position.cost_basis == pytest.approx(21000.0)

        sell = ledger.apply_fill(
            second.snapshot,
            _execution_result_filled(
                side=PaperOrderSide.SELL,
                quantity=80,
                execution_price=120.0,
                paper_execution_result_id="r" * 63 + "2",
                paper_fill_ids=("f" * 63 + "2",),
            ),
        )
        position = _snapshot_position(sell.snapshot, "AAPL")
        assert position is not None
        assert position.quantity == 120
        assert sell.snapshot.realized_pnl == pytest.approx((120.0 - 105.0) * 80)

    def test_unauthorized_short_rejected(self) -> None:
        ledger = _ledger()
        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        with pytest.raises(UnauthorizedShortError):
            ledger.apply_fill(
                applied.snapshot,
                _execution_result_filled(
                    side=PaperOrderSide.SELL,
                    quantity=150,
                    execution_price=110.0,
                    paper_execution_result_id="r" * 63 + "1",
                    paper_fill_ids=("f" * 63 + "1",),
                ),
            )


class TestPhase16FIntegration:
    def test_full_chain_cost_integration(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot
        assessment = service.assess_execution_cost(
            paper_fill=_paper_fill(),
            paper_order_id="o" * 64,
            methodology=_methodology(),
            simulated_at=FILL_TIMESTAMP,
            replay_specification_id=ledger.specification.specification_id,
            replay_run_id=ledger.specification.run_id,
        )
        cost_applied = service.apply_cost_assessment(prior_snapshot, assessment)
        assert cost_applied.snapshot.cash == pytest.approx(prior_snapshot.cash - assessment.total_transaction_cost)

    def test_full_chain_corporate_action_integration(self) -> None:
        ledger = _ledger()
        service = Phase16FService(ledger=ledger)

        snapshot, _ = ledger.initial_snapshot()
        applied = ledger.apply_fill(snapshot, _execution_result_filled(quantity=100, execution_price=100.0))
        prior_snapshot = applied.snapshot
        corporate_action = _corporate_action(
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=FILL_TIMESTAMP,
            split_ratio=(2, 1),
        )
        applied = service.apply_corporate_action(prior_snapshot, corporate_action, simulated_at=FILL_TIMESTAMP)
        position = _snapshot_position(applied.snapshot, "AAPL")
        assert position is not None
        assert position.quantity == 200
        assert position.cost_basis == pytest.approx(10000.0)
        assert applied.snapshot.realized_pnl == pytest.approx(0.0)
