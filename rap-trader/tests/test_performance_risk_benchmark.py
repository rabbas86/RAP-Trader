"""Phase 16G performance, risk, and benchmark evaluation tests."""

from __future__ import annotations

import ast
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.performance_evaluation.errors import (
    LookaheadContaminationError,
    MismatchedReplayLinkageError,
)
from app.services.performance_evaluation.evaluator import PerformanceEvaluationService
from app.services.performance_evaluation.models import (
    BenchmarkSpecification,
    PerformanceEvaluationMethodology,
)
from app.services.portfolio_accounting.models import PortfolioAccountingMethodology, PortfolioSnapshot, PositionState
from app.services.portfolio_accounting.phase16f_models import (
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentLedgerEntry,
)

DECISION_AT = datetime(2025, 5, 1, tzinfo=UTC)
REPLAY_START = datetime(2025, 4, 20, tzinfo=UTC)
REPLAY_END = datetime(2025, 5, 5, tzinfo=UTC)
SPEC_ID = "a" * 64
RUN_ID = UUID(int=0x16F)


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16g tests",
            producer="phase16g-tests",
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
        producer="phase16g-tests",
        producer_version="1.0",
        methodology_version="methodology-16g-1.0",
        benchmark_identities=["SPY"],
    )


def _accounting_methodology() -> PortfolioAccountingMethodology:
    return PortfolioAccountingMethodology.create(
        methodology_name="average_cost_no_short_no_margin_v1",
        cost_basis_method="average_cost",
        base_currency_behavior="isolated_base_currency",
        valuation_policy="mark_to_market_explicit",
        producer_version="phase16e-1.0",
    )


def _performance_methodology(
    *,
    periods_per_year: float = 252.0,
    risk_free_rate_annual: float = 0.0,
    minimum_acceptable_return_annual: float = 0.0,
    producer_version: str = "phase16g-1.0",
) -> PerformanceEvaluationMethodology:
    return PerformanceEvaluationMethodology.create(
        methodology_name="baseline_performance_evaluation",
        periods_per_year=periods_per_year,
        risk_free_rate_annual=risk_free_rate_annual,
        minimum_acceptable_return_annual=minimum_acceptable_return_annual,
        producer_version=producer_version,
    )


def _valuation_snapshot(
    *,
    simulated_at: datetime,
    cash: float = 0.0,
    market_value: float = 100.0,
    unrealized_pnl: float = 0.0,
    prior_snapshot_id: str | None = None,
    producer_version: str = "phase16e-1.0",
    specification: HistoricalReplaySpecification | None = None,
    snapshot_id: str | None = None,
) -> PortfolioSnapshot:
    effective_market_value = market_value if market_value else None
    specification = specification or _specification()
    if effective_market_value is None:
        position = PositionState(
            symbol=Symbol("AAPL"),
            quantity=0,
            average_cost=0.0,
            cost_basis=0.0,
            realized_pnl=0.0,
            last_mark_price=None,
            unrealized_pnl=None,
            market_value=None,
        )
        average_cost = 0.0
    else:
        average_cost = effective_market_value
        last_mark_price = effective_market_value if effective_market_value else None
        position = PositionState(
            symbol=Symbol("AAPL"),
            quantity=1,
            average_cost=average_cost,
            cost_basis=round(average_cost, 10),
            realized_pnl=0.0,
            last_mark_price=last_mark_price,
            unrealized_pnl=unrealized_pnl if effective_market_value else None,
            market_value=effective_market_value,
        )
    provisional = PortfolioSnapshot(
        portfolio_snapshot_id=snapshot_id or "0" * 64,
        replay_specification_id=specification.specification_id,
        replay_run_id=specification.run_id,
        simulated_at=simulated_at,
        base_currency="USD",
        cash=round(cash, 10),
        positions=(position,),
        total_cost_basis=round(average_cost, 10),
        realized_pnl=0.0,
        unrealized_pnl=unrealized_pnl if market_value else None,
        market_value=market_value if market_value else None,
        prior_snapshot_id=prior_snapshot_id,
        applied_fill_ids=(),
        accounting_methodology_id=_accounting_methodology().methodology_id,
        producer_version=producer_version,
    )
    if snapshot_id is None:
        snapshot_id = provisional._canonical_snapshot_id()
    payload = provisional.model_dump(mode="json")
    payload["portfolio_snapshot_id"] = snapshot_id
    return PortfolioSnapshot.model_validate(payload)


def _persist_snapshot(store: InMemoryArtifactStore, snapshot: PortfolioSnapshot) -> None:
    envelope = snapshot.envelope(provenance_references=_provenance(snapshot.portfolio_snapshot_id))
    store.put(envelope)


def _persist_specification(store: InMemoryArtifactStore, specification: HistoricalReplaySpecification | None = None) -> None:
    specification = specification or _specification()
    envelope = ArtifactEnvelope.create(
        payload=specification.model_dump(mode="json", exclude_none=False),
        artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
        logical_as_of=specification.logical_as_of,
        producer_version=specification.producer_version,
        provenance_references=_provenance(specification.specification_id),
    )
    store.put(envelope)


def _benchmark_bars(
    *,
    symbol: str = "SPY",
    closes: Sequence[float],
    start: datetime = REPLAY_START,
    producer_version: str = "phase16g-1.0",
) -> HistoricalBarsResult:
    bars = []
    current = start
    for close in closes:
        bars.append(
            OHLCVBar(
                timestamp=current,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=1,
            )
        )
        current = current + timedelta(days=1)
    return HistoricalBarsResult(
        symbol=Symbol(symbol),
        timeframe="1d",
        bars=bars,
        provider="unit-test",
        requested_start=start,
        requested_end=current,
        actual_start=bars[0].timestamp,
        actual_end=bars[-1].timestamp,
        adjustment="raw",
        session="regular",
        currency="USD",
        exchange="UNIT",
        partial=False,
        retrieved_at=DECISION_AT,
    )


def _persist_benchmark_bars(store: InMemoryArtifactStore, bars: HistoricalBarsResult) -> None:
    envelope = ArtifactEnvelope.create(
        payload=bars.model_dump(mode="json", exclude_none=False),
        artifact_type=ArtifactType.HISTORICAL_BARS_RESULT,
        logical_as_of=bars.actual_start,
        producer_version="phase16g-1.0",
        provenance_references=_provenance(),
    )
    store.put(envelope)


def _cost_assessment(
    *,
    assessment_id: str = "c" * 64,
    reference_price: float = 100.0,
    total_cost: float = 1.0,
    simulated_at: datetime = REPLAY_START + timedelta(days=1),
    specification: HistoricalReplaySpecification | None = None,
    store: InMemoryArtifactStore | None = None,
) -> ExecutionCostAssessment:
    specification = specification or _specification()
    assessment = ExecutionCostAssessment.create(
        paper_fill_id="f" * 64,
        paper_order_id="o" * 64,
        replay_specification_id=specification.specification_id,
        replay_run_id=specification.run_id,
        symbol="AAPL",
        side="BUY",
        quantity=1,
        reference_execution_price=reference_price,
        effective_execution_price=round(reference_price + total_cost, 10),
        reference_notional=round(reference_price, 10),
        effective_notional=round(reference_price + total_cost, 10),
        commission=0.0,
        spread_cost=round(total_cost / 2, 10),
        slippage_cost=round(total_cost / 2, 10),
        total_transaction_cost=round(total_cost, 10),
        methodology_id="m" * 64,
        simulated_at=simulated_at,
        producer_version="phase16f-1.0",
    )
    if store is not None:
        store.put(assessment.envelope(provenance_references=_provenance()))
    return assessment


def _dividend_entitlement(
    *,
    entitlement_id: str = "e" * 64,
    gross_cash_amount: float = 1.0,
    payment_at: datetime = REPLAY_START + timedelta(days=2),
    specification: HistoricalReplaySpecification | None = None,
    store: InMemoryArtifactStore | None = None,
) -> DividendEntitlement:
    specification = specification or _specification()
    entitlement = DividendEntitlement.create(
        corporate_action_id="a" * 64,
        snapshot_id="s" * 64,
        symbol="AAPL",
        entitled_quantity=1,
        dividend_per_share=gross_cash_amount,
        gross_cash_amount=gross_cash_amount,
        currency="USD",
        ex_date=payment_at,
        payment_at=payment_at,
        replay_specification_id=specification.specification_id,
        replay_run_id=specification.run_id,
        producer_version="phase16f-1.0",
    )
    if store is not None:
        store.put(entitlement.envelope(provenance_references=_provenance()))
    return entitlement


def _evaluation_service(
    store: InMemoryArtifactStore | None = None,
    specification: HistoricalReplaySpecification | None = None,
    benchmark: BenchmarkSpecification | None = None,
    methodology: PerformanceEvaluationMethodology | None = None,
) -> PerformanceEvaluationService:
    store = store or InMemoryArtifactStore()
    specification = specification or _specification()
    methodology = methodology or _performance_methodology()
    _persist_specification(store, specification)
    return PerformanceEvaluationService(
        store=store,
        specification=specification,
        methodology=methodology,
        benchmark=benchmark,
        producer_version="phase16g-1.0",
    )


class TestPerformanceEvaluationMethodology:
    def test_immutable_methodology(self) -> None:
        methodology = _performance_methodology()
        with pytest.raises(TypeError):
            methodology.model_copy(update={"periods_per_year": 252.0})

    def test_deterministic_methodology_id(self) -> None:
        first = _performance_methodology(producer_version="1.0")
        second = _performance_methodology(producer_version="1.0")
        assert first.methodology_id == second.methodology_id
        assert first.canonical_hash == second.canonical_hash

    def test_explicit_periodic_rates(self) -> None:
        methodology = _performance_methodology(periods_per_year=12.0, risk_free_rate_annual=0.06, minimum_acceptable_return_annual=0.03)
        assert methodology.periodic_risk_free_rate == pytest.approx(0.005)
        assert methodology.periodic_minimum_acceptable_return == pytest.approx(0.0025)


class TestPortfolioReturnSeries:
    def test_deterministic_return_series(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 23, tzinfo=UTC), cash=0.0, market_value=105.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        first = service.build_return_series(snapshots)
        second = service.build_return_series(snapshots)
        assert first.series_id == second.series_id

    def test_unvalued_snapshot_excluded(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=None),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 23, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        assert series.valued_snapshot_count == 2
        assert series.unvalued_snapshot_ids == (snapshots[1].portfolio_snapshot_id,)
        assert series.return_observation_count == 0

    def test_zero_starting_equity_skips_return(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=0.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=110.0, market_value=0.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        assert series.return_observation_count == 0


class TestPerformanceMetrics:
    def test_hand_checkable_series(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        values = [100.0, 110.0, 105.0, 120.0, 90.0, 100.0]
        snapshots = []
        for index, value in enumerate(values):
            snapshots.append(
                _valuation_snapshot(
                    simulated_at=datetime(2025, 4, 21 + index, tzinfo=UTC),
                    cash=0.0,
                    market_value=value,
                    snapshot_id=("s" + str(index)).zfill(64),
                )
            )
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        assert series.return_observation_count == 5
        assert series.observations[0].period_return == pytest.approx(0.1)
        assert series.observations[1].period_return == pytest.approx(-0.045454545454545456)
        assert series.observations[2].period_return == pytest.approx(0.14285714285714285)
        assert series.observations[3].period_return == pytest.approx(-0.25)
        assert series.observations[4].period_return == pytest.approx(0.1111111111111111)
        metrics = service._performance_metrics(series, snapshots)
        assert metrics.total_return.value == pytest.approx(values[-1] / values[0] - 1)
        assert metrics.total_return.status == "available"
        assert metrics.period_return_count == 5
        assert metrics.cumulative_return_series == pytest.approx([1.1, 1.05, 1.2, 0.9, 1.0])
        assert metrics.cagr.value == pytest.approx(0.0)

    def test_insufficient_returns_unavailable(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        metrics = service._performance_metrics(series, snapshots)
        assert metrics.total_return.status == "unavailable"
        assert metrics.cagr.status == "unavailable"


class TestRiskMetrics:
    def test_maximum_drawdown(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        values = [100.0, 110.0, 105.0, 120.0, 90.0, 100.0]
        snapshots = []
        for index, value in enumerate(values):
            snapshots.append(
                _valuation_snapshot(
                    simulated_at=datetime(2025, 4, 21 + index, tzinfo=UTC),
                    cash=0.0,
                    market_value=value,
                    snapshot_id=("s" + str(index)).zfill(64),
                )
            )
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        metrics = service._risk_metrics(series, list(series.observations))
        assert metrics.maximum_drawdown.status == "available"
        assert metrics.maximum_drawdown.max_drawdown_percent == pytest.approx(0.25)
        assert metrics.maximum_drawdown.peak_timestamp == datetime(2025, 4, 24, tzinfo=UTC)
        assert metrics.maximum_drawdown.trough_timestamp == datetime(2025, 4, 25, tzinfo=UTC)

    def test_sharpe_zero_volatility_unavailable(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=100.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        metrics = service._risk_metrics(series, list(series.observations))
        assert metrics.sharpe_ratio.status == "unavailable"
        assert metrics.sharpe_ratio.reason == "zero volatility"

    def test_sortino_zero_downside_unavailable(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store, methodology=_performance_methodology(minimum_acceptable_return_annual=0.0))
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=101.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 23, tzinfo=UTC), cash=0.0, market_value=102.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        series = service.build_return_series(snapshots)
        metrics = service._risk_metrics(series, list(series.observations))
        assert metrics.sortino_ratio.status == "unavailable"
        assert metrics.sortino_ratio.reason == "zero downside deviation"


class TestBenchmarkContract:
    def test_deterministic_benchmark_identity(self) -> None:
        first = BenchmarkSpecification.create(
            symbol="SPY",
            price_methodology="close",
            return_methodology="price_return",
            base_currency="USD",
            timeframe="1d",
            source_version="unit-test",
            producer_version="1.0",
        )
        second = BenchmarkSpecification.create(
            symbol="SPY",
            price_methodology="close",
            return_methodology="price_return",
            base_currency="USD",
            timeframe="1d",
            source_version="unit-test",
            producer_version="1.0",
        )
        assert first.benchmark_id == second.benchmark_id

    def test_benchmark_absent_permits_portfolio_evaluation(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store, benchmark=None)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        evaluation = service.evaluate()
        assert evaluation.benchmark_comparison is None
        assert evaluation.performance_metrics.total_return.status == "available"

    def test_benchmark_missing_interval_explicit(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification()
        benchmark = BenchmarkSpecification.create(
            symbol="SPY",
            price_methodology="close",
            return_methodology="price_return",
            base_currency="USD",
            timeframe="1d",
            source_version="unit-test",
            replay_specification_id=specification.specification_id,
            replay_run_id=specification.run_id,
            producer_version="phase16g-1.0",
        )
        _persist_specification(store, specification)
        service = PerformanceEvaluationService(
            store=store,
            specification=specification,
            methodology=_performance_methodology(),
            benchmark=benchmark,
            producer_version="phase16g-1.0",
        )
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0, specification=specification),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0, specification=specification),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        bars = _benchmark_bars(closes=[100.0])  # only one price -> no benchmark returns
        _persist_benchmark_bars(store, bars)
        evaluation = service.evaluate()
        assert evaluation.benchmark_comparison is not None
        assert evaluation.benchmark_comparison.aligned_sample_count == 0
        assert evaluation.benchmark_comparison.benchmark_total_return.status == "unavailable"


class TestTransactionCostAggregate:
    def test_aggregate_costs_from_assessments(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        assessment = _cost_assessment(total_cost=2.0, store=store)
        adjustment = PortfolioAdjustmentLedgerEntry.create_execution_cost(
            snapshot=snapshots[1],
            assessment=assessment,
            prior_snapshot_id=snapshots[0].portfolio_snapshot_id,
            sequence=0,
            producer_version="phase16f-1.0",
        )
        store.put(adjustment.envelope(provenance_references=_provenance()))
        evaluation = service.evaluate()
        assert evaluation.transaction_cost_aggregate.total_transaction_cost == pytest.approx(2.0)

    def test_dividend_aggregate(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        entitlement = _dividend_entitlement(gross_cash_amount=3.0, store=store)
        adjustment = PortfolioAdjustmentLedgerEntry.create_dividend_payment(
            snapshot=snapshots[1],
            entitlement=entitlement,
            prior_snapshot_id=snapshots[0].portfolio_snapshot_id,
            sequence=0,
            producer_version="phase16f-1.0",
        )
        store.put(adjustment.envelope(provenance_references=_provenance()))
        evaluation = service.evaluate()
        assert evaluation.corporate_action_aggregate.total_dividend_cash == pytest.approx(3.0)
        assert evaluation.corporate_action_aggregate.split_count == 0


class TestImmutabilityAndDeterminism:
    def test_deeply_immutable_evaluation(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        evaluation = service.evaluate()
        with pytest.raises(TypeError):
            evaluation.model_copy(update={"evaluation_id": "1" * 64})
        with pytest.raises(TypeError):
            evaluation.performance_metrics.model_copy(update={"period_return_count": 99})
        with pytest.raises(TypeError):
            evaluation.risk_metrics.maximum_drawdown.model_copy(update={"max_drawdown_percent": 1.0})
        with pytest.raises(TypeError):
            evaluation.transaction_cost_aggregate.model_copy(update={"total_transaction_cost": 1.0})

    def test_deterministic_evaluation_id(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
            _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        first = service.evaluate()
        second_store = InMemoryArtifactStore()
        second_service = _evaluation_service(second_store)
        for snapshot in snapshots:
            _persist_snapshot(second_store, snapshot)
        second = second_service.evaluate()
        assert first.evaluation_id == second.evaluation_id

    def test_idempotent_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            service = _evaluation_service(store)
            snapshots = [
                _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
                _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
            ]
            for snapshot in snapshots:
                _persist_snapshot(store, snapshot)
            first = service.evaluate()
            first_envelope = first.envelope(provenance_references=_provenance())
            store.put(first_envelope)
            second = service.evaluate()
            second_envelope = second.envelope(provenance_references=_provenance())
            store.put(second_envelope)
            assert first.evaluation_id == second.evaluation_id

    def test_file_artifact_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            service = _evaluation_service(store)
            snapshots = [
                _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
                _valuation_snapshot(simulated_at=datetime(2025, 4, 22, tzinfo=UTC), cash=0.0, market_value=110.0),
            ]
            for snapshot in snapshots:
                _persist_snapshot(store, snapshot)
            evaluation = service.evaluate()
            persisted_envelope = evaluation.envelope(provenance_references=_provenance())
            store.put(persisted_envelope)
            restarted = FileArtifactStore(temp_dir)
            reloaded = restarted.get(persisted_envelope.artifact_id)
            assert reloaded.payload["evaluation_id"] == evaluation.evaluation_id

    def test_corruption_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            _evaluation_service(store)
            snapshot = _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0)
            _persist_snapshot(store, snapshot)
            snapshot_id = store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT})[0]
            prefix = snapshot_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            target_dir.mkdir(parents=True, exist_ok=True)
            Path(target_dir / f"{snapshot_id}.json").write_text("not-json", encoding="utf-8")
            reloaded_store = FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                _evaluation_service(reloaded_store)

    def test_wrong_replay_linkage_rejected(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification()
        other = HistoricalReplaySpecification.create(
            start_time=REPLAY_START,
            end_time=REPLAY_END,
            instruments=["AAPL"],
            timeframes=["1d"],
            decision_cadence="window_close",
            data_boundary_description="event_time_only",
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
            producer="phase16g-tests",
            producer_version="1.0",
            methodology_version="methodology-16g-1.0",
        )
        _persist_specification(store, other)
        with pytest.raises(MismatchedReplayLinkageError):
            _evaluation_service(store, specification=specification)


class TestNoFeedbackGuarantee:
    def test_future_performance_artifacts_rejected(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(simulated_at=datetime(2025, 4, 21, tzinfo=UTC), cash=0.0, market_value=100.0),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        future_envelope = ArtifactEnvelope.create(
            payload={"evaluation_id": "e" * 64, "status": "future"},
            artifact_type=ArtifactType.HISTORICAL_PERFORMANCE_EVALUATION,
            logical_as_of=datetime(2099, 1, 1, tzinfo=UTC),
            producer_version="phase16g-1.0",
            provenance_references=_provenance(),
        )
        store.put(future_envelope)
        with pytest.raises(LookaheadContaminationError):
            service.evaluate()

    def test_no_broker_or_network_dependency(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "performance_evaluation" / "evaluator.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = {
            node.module if isinstance(node, ast.ImportFrom) and node.module else getattr(node.names[0], "name", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        forbidden = {"broker", "yfinance", "requests", "httpx", "aiohttp", "mt5", "tws"}
        assert forbidden.isdisjoint({name.lower() for name in imports if isinstance(name, str)})
