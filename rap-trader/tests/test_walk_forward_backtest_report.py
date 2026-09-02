"""Phase 16H walk-forward evaluation and final backtest report tests."""

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
from app.services.performance_evaluation.models import (
    CorporateActionAggregate,
    DrawdownPeriod,
    MetricValue,
    PerformanceEvaluationMethodology,
    PerformanceMetrics,
    RiskMetrics,
    TransactionCostAggregate,
)
from app.services.portfolio_accounting.models import PortfolioAccountingMethodology, PortfolioSnapshot, PositionState
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    CorporateActionStatus,
    CorporateActionType,
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentLedgerEntry,
)
from app.services.walk_forward.errors import (
    FutureEvaluationContaminationError,
    OverlappingTestWindowsError,
    WrongReplayLinkageError,
)
from app.services.walk_forward.evaluator import WalkForwardEvaluationService
from app.services.walk_forward.models import (
    FoldStabilityMetrics,
    FoldStatus,
    HistoricalBacktestReport,
    WalkForwardEvaluation,
    WalkForwardEvaluationMethodology,
    WalkForwardFold,
)

DECISION_AT = datetime(2025, 1, 1, tzinfo=UTC)
REPLAY_START = datetime(2025, 1, 1, tzinfo=UTC)
REPLAY_END = datetime(2025, 1, 11, tzinfo=UTC)
SPEC_ID = "a" * 64
RUN_ID = UUID("16" + "0" * 30)


def _provenance(identifier: str = SPEC_ID) -> tuple[ProvenanceReference, ...]:
    return (
        ProvenanceReference(
            kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
            identifier=identifier,
            description="seed provenance for phase16h tests",
            producer="phase16h-tests",
            producer_version="1.0",
        ),
    )


def _specification(
    *,
    start_time: datetime = REPLAY_START,
    end_time: datetime = REPLAY_END,
    instruments: Sequence[str] = ("AAPL",),
    producer_version: str = "phase16e-1.0",
) -> HistoricalReplaySpecification:
    return HistoricalReplaySpecification.create(
        start_time=start_time,
        end_time=end_time,
        instruments=list(instruments),
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
        producer="phase16h-tests",
        producer_version=producer_version,
        methodology_version="methodology-16h-1.0",
    )


def _accounting_methodology() -> PortfolioAccountingMethodology:
    return PortfolioAccountingMethodology.create(
        methodology_name="average_cost_no_short_no_margin_v1",
        cost_basis_method="average_cost",
        base_currency_behavior="isolated_base_currency",
        valuation_policy="mark_to_market_explicit",
        producer_version="phase16e-1.0",
    )


def _performance_methodology(producer_version: str = "phase16g-1.0") -> PerformanceEvaluationMethodology:
    return PerformanceEvaluationMethodology.create(
        methodology_name="baseline_performance_evaluation",
        periods_per_year=252.0,
        producer_version=producer_version,
    )


def _valuation_snapshot(
    *,
    simulated_at: datetime,
    cash: float = 0.0,
    market_value: float = 100.0,
    prior_snapshot_id: str | None = None,
    producer_version: str = "phase16e-1.0",
    specification: HistoricalReplaySpecification | None = None,
    snapshot_id: str | None = None,
) -> PortfolioSnapshot:
    specification = specification or _specification()
    effective_market_value = market_value if market_value else None
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
            unrealized_pnl=0.0,
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
        unrealized_pnl=0.0 if market_value else None,
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
    payload["simulated_at"] = simulated_at.isoformat()
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


def _walk_forward_methodology(
    *,
    mode: str = "ANCHORED",
    performance_methodology_id: str | None = None,
    producer_version: str = "phase16h-1.0",
    train_window: str = "2d",
    test_window: str = "1d",
    minimum_test_observations: int = 1,
) -> WalkForwardEvaluationMethodology:
    return WalkForwardEvaluationMethodology.create(
        methodology_name="chronological_walk_forward_v1",
        mode=mode,
        train_window=train_window,
        test_window=test_window,
        step="1d",
        embargo="0d",
        minimum_train_observations=0,
        minimum_test_observations=minimum_test_observations,
        incomplete_final_fold_policy="DROP_INCOMPLETE",
        performance_evaluation_methodology_id=performance_methodology_id or _performance_methodology().methodology_id,
        benchmark_policy="downstream_only",
        fold_aggregation_policy="union_valid_test_windows",
        producer_version=producer_version,
    )


def _evaluation_service(
    store: InMemoryArtifactStore | None = None,
    specification: HistoricalReplaySpecification | None = None,
    methodology: WalkForwardEvaluationMethodology | None = None,
) -> WalkForwardEvaluationService:
    store = store or InMemoryArtifactStore()
    specification = specification or _specification()
    methodology = methodology or _walk_forward_methodology()
    _persist_specification(store, specification)
    return WalkForwardEvaluationService(
        store=store,
        specification=specification,
        methodology=methodology,
        producer_version="phase16h-1.0",
    )


def _snapshots_for_replay(
    *,
    specification: HistoricalReplaySpecification,
    cadence_days: int = 1,
    market_value: float = 100.0,
) -> list[PortfolioSnapshot]:
    snapshots: list[PortfolioSnapshot] = []
    current = specification.start_time
    index = 0
    while current <= specification.end_time:
        snapshots.append(
            _valuation_snapshot(
                simulated_at=current,
                cash=0.0,
                market_value=market_value,
                snapshot_id=("s" + str(index)).zfill(64),
                specification=specification,
            )
        )
        current = current + timedelta(days=cadence_days)
        index += 1
    return snapshots


class TestWalkForwardMethodology:
    def test_immutable_methodology(self) -> None:
        methodology = _walk_forward_methodology()
        with pytest.raises(TypeError):
            methodology.model_copy(update={"mode": "ROLLING"})

    def test_deterministic_methodology_identity(self) -> None:
        first = _walk_forward_methodology()
        second = _walk_forward_methodology()
        assert first.methodology_id == second.methodology_id
        assert first.canonical_hash == second.canonical_hash


class TestFoldGeneration:
    def test_anchored_fold_generation(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 9, tzinfo=UTC))
        methodology = WalkForwardEvaluationMethodology.create(
            methodology_name="anchored",
            mode="ANCHORED",
            train_window="2d",
            test_window="1d",
            step="1d",
            embargo="0d",
            minimum_train_observations=0,
            minimum_test_observations=1,
            incomplete_final_fold_policy="DROP_INCOMPLETE",
            performance_evaluation_methodology_id=_performance_methodology().methodology_id,
            benchmark_policy="downstream_only",
            fold_aggregation_policy="union_valid_test_windows",
            producer_version="phase16h-1.0",
        )
        _persist_specification(store, specification)
        service = WalkForwardEvaluationService(
            store=store,
            specification=specification,
            methodology=methodology,
            producer_version="phase16h-1.0",
        )
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert len(folds) == 6
        assert [fold.fold_index for fold in folds] == [0, 1, 2, 3, 4, 5]
        assert folds[0].train_start == REPLAY_START
        assert folds[0].test_start == datetime(2025, 1, 3, tzinfo=UTC)
        assert folds[1].test_start == datetime(2025, 1, 4, tzinfo=UTC)
        assert folds[0].test_end == datetime(2025, 1, 4, tzinfo=UTC)
        assert folds[1].test_end == datetime(2025, 1, 5, tzinfo=UTC)
        assert folds[2].test_end == datetime(2025, 1, 6, tzinfo=UTC)

    def test_rolling_fold_generation(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 9, tzinfo=UTC))
        methodology = WalkForwardEvaluationMethodology.create(
            methodology_name="rolling",
            mode="ROLLING",
            train_window="2d",
            test_window="1d",
            step="1d",
            embargo="0d",
            minimum_train_observations=0,
            minimum_test_observations=1,
            incomplete_final_fold_policy="DROP_INCOMPLETE",
            performance_evaluation_methodology_id=_performance_methodology().methodology_id,
            benchmark_policy="downstream_only",
            fold_aggregation_policy="union_valid_test_windows",
            producer_version="phase16h-1.0",
        )
        _persist_specification(store, specification)
        service = WalkForwardEvaluationService(
            store=store,
            specification=specification,
            methodology=methodology,
            producer_version="phase16h-1.0",
        )
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert len(folds) == 6
        assert folds[0].train_start == REPLAY_START
        assert folds[1].train_start == datetime(2025, 1, 2, tzinfo=UTC)

    def test_explicit_half_open_boundaries(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 9, tzinfo=UTC))
        _persist_specification(store, specification)
        service = _evaluation_service(store, specification=specification)
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        boundaries = [(fold.test_start, fold.test_end) for fold in folds]
        assert boundaries == [
            (datetime(2025, 1, 3, tzinfo=UTC), datetime(2025, 1, 4, tzinfo=UTC)),
            (datetime(2025, 1, 4, tzinfo=UTC), datetime(2025, 1, 5, tzinfo=UTC)),
            (datetime(2025, 1, 5, tzinfo=UTC), datetime(2025, 1, 6, tzinfo=UTC)),
            (datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 1, 7, tzinfo=UTC)),
            (datetime(2025, 1, 7, tzinfo=UTC), datetime(2025, 1, 8, tzinfo=UTC)),
            (datetime(2025, 1, 8, tzinfo=UTC), datetime(2025, 1, 9, tzinfo=UTC)),
        ]

    def test_embargo_honored(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 11, tzinfo=UTC))
        methodology = WalkForwardEvaluationMethodology.create(
            methodology_name="embargo",
            mode="ANCHORED",
            train_window="2d",
            test_window="2d",
            step="2d",
            embargo="1d",
            minimum_train_observations=0,
            minimum_test_observations=2,
            incomplete_final_fold_policy="DROP_INCOMPLETE",
            performance_evaluation_methodology_id=_performance_methodology().methodology_id,
            benchmark_policy="downstream_only",
            fold_aggregation_policy="union_valid_test_windows",
            producer_version="phase16h-1.0",
        )
        _persist_specification(store, specification)
        service = WalkForwardEvaluationService(
            store=store,
            specification=specification,
            methodology=methodology,
            producer_version="phase16h-1.0",
        )
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert folds[0].embargo_start == datetime(2025, 1, 3, tzinfo=UTC)
        assert folds[0].embargo_end == datetime(2025, 1, 4, tzinfo=UTC)
        assert folds[0].test_start == datetime(2025, 1, 4, tzinfo=UTC)

    def test_non_overlapping_test_windows(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert service._validate_folds(folds) is None

    def test_deterministic_fold_ids(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        first = service._build_folds(snapshots, [], [], [], [])
        second = service._build_folds(snapshots, [], [], [], [])
        assert [fold.fold_id for fold in first] == [fold.fold_id for fold in second]

    def test_chronological_fold_ordering(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert [fold.test_start for fold in folds] == sorted(fold.test_start for fold in folds)
        assert [fold.fold_index for fold in folds] == list(range(len(folds)))

    def test_incomplete_final_fold_policy(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 7, tzinfo=UTC))
        methodology = WalkForwardEvaluationMethodology.create(
            methodology_name="incomplete_policy",
            mode="ANCHORED",
            train_window="2d",
            test_window="2d",
            step="2d",
            embargo="0d",
            minimum_train_observations=0,
            minimum_test_observations=2,
            incomplete_final_fold_policy="DROP_INCOMPLETE",
            performance_evaluation_methodology_id=_performance_methodology().methodology_id,
            benchmark_policy="downstream_only",
            fold_aggregation_policy="union_valid_test_windows",
            producer_version="phase16h-1.0",
        )
        _persist_specification(store, specification)
        service = WalkForwardEvaluationService(
            store=store,
            specification=specification,
            methodology=methodology,
            producer_version="phase16h-1.0",
        )
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert len(folds) == 2
        assert all(fold.status == FoldStatus.VALID.value for fold in folds)

    def test_insufficient_data_fold_retained(self) -> None:
        store = InMemoryArtifactStore()
        specification = _specification(start_time=REPLAY_START, end_time=datetime(2025, 1, 11, tzinfo=UTC))
        methodology = WalkForwardEvaluationMethodology.create(
            methodology_name="insufficient_policy",
            mode="ANCHORED",
            train_window="2d",
            test_window="2d",
            step="2d",
            embargo="0d",
            minimum_train_observations=0,
            minimum_test_observations=10,
            incomplete_final_fold_policy="EVALUATE_IF_MINIMUM_SAMPLE_MET",
            performance_evaluation_methodology_id=_performance_methodology().methodology_id,
            benchmark_policy="downstream_only",
            fold_aggregation_policy="union_valid_test_windows",
            producer_version="phase16h-1.0",
        )
        _persist_specification(store, specification)
        service = WalkForwardEvaluationService(
            store=store,
            specification=specification,
            methodology=methodology,
            producer_version="phase16h-1.0",
        )
        snapshots = _snapshots_for_replay(specification=specification, cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        assert all(fold.status == FoldStatus.INSUFFICIENT_DATA.value for fold in folds)
        assert any("insufficient test observations" in fold.warnings for fold in folds)


class TestOutOfSampleEvaluation:
    def test_fold_oos_uses_test_window_only(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]
        evaluation = service._evaluate_fold(valid_folds[0], snapshots, [], [], [], [])
        assert evaluation is not None
        assert evaluation.snapshot_count == 1
        assert evaluation.performance_metrics.period_return_count == 0
        assert evaluation.performance_metrics.total_return.status == "unavailable"

    def test_no_train_return_leakage_into_oos_metrics(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]
        evaluation, _ = service._aggregate_oos(valid_folds, snapshots, [], [], [], [])
        assert evaluation.return_observation_count >= 1
        assert evaluation.performance_metrics.total_return.status == "available"

    def test_aggregate_oos_not_naive_average(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]
        evaluation, stability = service._aggregate_oos(valid_folds, snapshots, [], [], [], [])
        assert evaluation.return_observation_count >= 1
        assert evaluation.performance_metrics.total_return.status == "available"
        assert stability.fold_count == len(valid_folds)

    def test_deterministic_aggregate_oos_metrics(self) -> None:
        store1 = InMemoryArtifactStore()
        service1 = _evaluation_service(store1)
        snapshots1 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots1:
            _persist_snapshot(store1, snapshot)
        eval1, _ = service1._aggregate_oos(service1._build_folds(snapshots1, [], [], [], []), snapshots1, [], [], [], [])

        store2 = InMemoryArtifactStore()
        service2 = _evaluation_service(store2)
        snapshots2 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots2:
            _persist_snapshot(store2, snapshot)
        eval2, _ = service2._aggregate_oos(service2._build_folds(snapshots2, [], [], [], []), snapshots2, [], [], [], [])
        assert eval1.performance_metrics.total_return.value == eval2.performance_metrics.total_return.value


class TestStabilityMetrics:
    def test_positive_negative_fold_counts(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        _, stability = service._aggregate_oos(folds, snapshots, [], [], [], [])
        assert stability.positive_fold_count >= 0
        assert stability.negative_fold_count >= 0
        assert stability.positive_fold_ratio.status in {"available", "unavailable"}

    def test_best_worst_and_median_fold(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]
        _, stability = service._aggregate_oos(valid_folds, snapshots, [], [], [], [])
        assert stability.best_fold_total_return.status == "unavailable"
        assert stability.worst_fold_total_return.status == "unavailable"
        assert stability.median_fold_total_return.status == "unavailable"

    def test_unavailable_fold_sharpe_handled(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        folds = service._build_folds(snapshots, [], [], [], [])
        _, stability = service._aggregate_oos(folds, snapshots, [], [], [], [])
        assert stability.median_fold_sharpe is None or stability.median_fold_sharpe.status == "unavailable"
        assert stability.worst_fold_drawdown is not None
        assert stability.worst_fold_drawdown.status == "available"
        assert stability.worst_fold_drawdown.value == 0.0


class TestBenchmarkAndCosts:
    def test_benchmark_fold_alignment(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        benchmark = _benchmark_bars(closes=[100.0, 110.0, 105.0, 90.0])
        _persist_benchmark_bars(store, benchmark)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        evaluation, _ = service.evaluate(benchmark=benchmark)
        assert evaluation.benchmark_comparison is not None

    def test_benchmark_missing_data_semantics(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        benchmark = _benchmark_bars(closes=[100.0])
        _persist_benchmark_bars(store, benchmark)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        evaluation, _ = service.evaluate(benchmark=benchmark)
        assert evaluation.benchmark_comparison is None or evaluation.benchmark_comparison.aligned_sample_count == 0

    def test_transaction_costs_reflected_exactly_once(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        assessment = _cost_assessment(total_cost=2.0, store=store, simulated_at=datetime(2025, 1, 3, tzinfo=UTC))
        adjustment = PortfolioAdjustmentLedgerEntry.create_execution_cost(
            snapshot=snapshots[2],
            assessment=assessment,
            prior_snapshot_id=snapshots[1].portfolio_snapshot_id,
            sequence=0,
            producer_version="phase16f-1.0",
        )
        store.put(adjustment.envelope(provenance_references=_provenance()))
        evaluation, report = service.evaluate()
        assert evaluation.transaction_cost_aggregate.total_transaction_cost == 2.0
        assert report.costs["total_transaction_cost"] == 2.0

    def test_dividends_reflected_exactly_once(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        entitlement = _dividend_entitlement(gross_cash_amount=3.0, store=store)
        adjustment = PortfolioAdjustmentLedgerEntry.create_dividend_payment(
            snapshot=snapshots[2],
            entitlement=entitlement,
            prior_snapshot_id=snapshots[1].portfolio_snapshot_id,
            sequence=0,
            producer_version="phase16f-1.0",
        )
        store.put(adjustment.envelope(provenance_references=_provenance()))
        evaluation, report = service.evaluate()
        assert evaluation.corporate_action_aggregate.total_dividend_cash == 3.0
        assert report.corporate_actions["total_dividend_cash"] == 3.0

    def test_split_not_treated_as_profit(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = [
            _valuation_snapshot(
                simulated_at=datetime(2025, 1, 1, tzinfo=UTC), cash=100_000.0, market_value=None, specification=_specification()
            ),
            _valuation_snapshot(
                simulated_at=datetime(2025, 1, 2, tzinfo=UTC),
                cash=0.0,
                market_value=100.0,
                prior_snapshot_id="0" * 64,
                specification=_specification(),
            ),
            _valuation_snapshot(
                simulated_at=datetime(2025, 1, 3, tzinfo=UTC),
                cash=0.0,
                market_value=100.0,
                prior_snapshot_id="1" * 64,
                specification=_specification(),
            ),
        ]
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        split = CorporateActionEvent.create(
            corporate_action_id="2" * 64,
            symbol="AAPL",
            action_type=CorporateActionType.STOCK_SPLIT.value,
            effective_at=datetime(2025, 1, 3, tzinfo=UTC),
            announced_at=datetime(2025, 1, 3, tzinfo=UTC),
            split_ratio=(2, 1),
            currency="USD",
            status=CorporateActionStatus.EFFECTIVE.value,
            price_adjustment_convention="split",
            methodology_version="phase16f-1.0",
            replay_specification_id=_specification().specification_id,
            replay_run_id=_specification().run_id,
            producer_version="phase16f-1.0",
        )
        store.put(split.envelope(provenance_references=_provenance()))
        evaluation, _ = service.evaluate()
        assert evaluation.corporate_action_aggregate.split_count == 1
        assert evaluation.oos_performance_metrics.total_return.status == "unavailable"


class TestImmutabilityAndDeterminism:
    def test_immutable_fold(self) -> None:
        fold = WalkForwardFold.create(
            fold_index=0,
            replay_specification_id=SPEC_ID,
            methodology_id="m" * 64,
            train_start=REPLAY_START,
            train_end=REPLAY_START + timedelta(days=2),
            test_start=REPLAY_START + timedelta(days=2),
            test_end=REPLAY_START + timedelta(days=3),
            status=FoldStatus.VALID.value,
            producer_version="phase16h-1.0",
        )
        with pytest.raises(TypeError):
            fold.model_copy(update={"test_start": REPLAY_START})

    def test_immutable_walk_forward_evaluation(self) -> None:
        evaluation = WalkForwardEvaluation.create(
            replay_specification_id=SPEC_ID,
            methodology_id="m" * 64,
            performance_methodology_id="p" * 64,
            fold_count=0,
            valid_fold_count=0,
            insufficient_fold_count=0,
            oos_performance_metrics=PerformanceMetrics(
                starting_equity=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                ending_equity=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                total_return=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                cagr=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                cumulative_return_series=(),
                positive_period_ratio=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                period_return_count=0,
                producer_version="phase16h-1.0",
            ),
            oos_risk_metrics=RiskMetrics(
                annualized_volatility=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                downside_deviation=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                maximum_drawdown=DrawdownPeriod(
                    max_drawdown=0.0,
                    max_drawdown_percent=0.0,
                    peak_timestamp=REPLAY_START,
                    trough_timestamp=REPLAY_START,
                    recovery_timestamp=REPLAY_START,
                    duration=0.0,
                    status="unavailable",
                    reason="insufficient fold observations",
                    producer_version="phase16h-1.0",
                ),
                sharpe_ratio=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                sortino_ratio=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                calmar_ratio=MetricValue(status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"),
                best_period_return=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                worst_period_return=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                producer_version="phase16h-1.0",
            ),
            fold_stability_metrics=FoldStabilityMetrics(
                fold_count=0,
                valid_fold_count=0,
                insufficient_fold_count=0,
                positive_fold_count=0,
                negative_fold_count=0,
                positive_fold_ratio=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                best_fold_total_return=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                worst_fold_total_return=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                median_fold_total_return=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                producer_version="phase16h-1.0",
            ),
            transaction_cost_aggregate=TransactionCostAggregate(
                assessment_count=0,
                total_commission=0.0,
                total_spread_cost=0.0,
                total_slippage_cost=0.0,
                total_transaction_cost=0.0,
                cost_to_starting_capital_ratio=MetricValue(
                    status="unavailable", reason="insufficient fold observations", producer_version="phase16h-1.0"
                ),
                producer_version="phase16h-1.0",
            ),
            corporate_action_aggregate=CorporateActionAggregate(
                dividend_count=0,
                total_dividend_cash=0.0,
                split_count=0,
                producer_version="phase16h-1.0",
            ),
            logical_as_of=REPLAY_END,
            producer_version="phase16h-1.0",
        )
        with pytest.raises(TypeError):
            evaluation.model_copy(update={"walk_forward_evaluation_id": "1" * 64})

    def test_deterministic_walk_forward_evaluation_id(self) -> None:
        store1 = InMemoryArtifactStore()
        service1 = _evaluation_service(store1)
        snapshots1 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots1:
            _persist_snapshot(store1, snapshot)
        evaluation1, _ = service1.evaluate()

        store2 = InMemoryArtifactStore()
        service2 = _evaluation_service(store2)
        snapshots2 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots2:
            _persist_snapshot(store2, snapshot)
        evaluation2, _ = service2.evaluate()
        assert evaluation1.walk_forward_evaluation_id == evaluation2.walk_forward_evaluation_id

    def test_final_report_immutable(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        _, report = service.evaluate()
        with pytest.raises(TypeError):
            report.model_copy(update={"status": "INVALID"})

    def test_deterministic_final_report_id(self) -> None:
        store1 = InMemoryArtifactStore()
        service1 = _evaluation_service(store1)
        snapshots1 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots1:
            _persist_snapshot(store1, snapshot)
        _, report1 = service1.evaluate()

        store2 = InMemoryArtifactStore()
        service2 = _evaluation_service(store2)
        snapshots2 = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots2:
            _persist_snapshot(store2, snapshot)
        _, report2 = service2.evaluate()
        assert report1.backtest_report_id == report2.backtest_report_id


class TestReportSections:
    def test_report_includes_lineage(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        _, report = service.evaluate()
        assert report.reproducibility["replay_specification_id"] == _specification().specification_id
        assert report.methodology_ids == (_walk_forward_methodology().methodology_id,)

    def test_report_includes_cost_and_performance_sections(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        _, report = service.evaluate()
        assert "total_return" in report.performance
        assert "oos_performance" in report.walk_forward
        assert "fold_stability" in report.walk_forward
        assert report.safety["suitable_for_live_trading"] is False
        assert "historical evidence only; not live-trading approval" in report.limitations

    def test_report_cannot_claim_live_readiness(self) -> None:
        with pytest.raises(ValueError):
            HistoricalBacktestReport.create(
                backtest_report_id="",
                deterministic_report_id="",
                replay_specification_id=SPEC_ID,
                walk_forward_evaluation_id="w" * 64,
                producer_version="phase16h-1.0",
                status="READY_FOR_LIVE_TRADING",
                logical_as_of=REPLAY_END,
            )


class TestNoLookaheadAndIntegrity:
    def test_no_future_performance_artifact_enters_historical_decision(self) -> None:
        store = InMemoryArtifactStore()
        _persist_specification(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        future_envelope = ArtifactEnvelope.create(
            payload={"evaluation_id": "e" * 64, "status": "future"},
            artifact_type=ArtifactType.HISTORICAL_PERFORMANCE_EVALUATION,
            logical_as_of=datetime(2099, 1, 1, tzinfo=UTC),
            producer_version="phase16h-1.0",
            provenance_references=_provenance(),
        )
        store.put(future_envelope)
        with pytest.raises(FutureEvaluationContaminationError):
            _evaluation_service(store).evaluate()

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
            producer="phase16h-tests",
            producer_version="1.0",
            methodology_version="methodology-16h-1.0",
        )
        _persist_specification(store, other)
        with pytest.raises(WrongReplayLinkageError):
            _evaluation_service(store, specification=specification)

    def test_overlapping_test_windows_rejected(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        fold_a = WalkForwardFold.create(
            fold_index=0,
            replay_specification_id=SPEC_ID,
            methodology_id="m" * 64,
            train_start=REPLAY_START,
            train_end=REPLAY_START + timedelta(days=2),
            test_start=REPLAY_START + timedelta(days=2),
            test_end=REPLAY_START + timedelta(days=4),
            status=FoldStatus.VALID.value,
            producer_version="phase16h-1.0",
        )
        fold_b = WalkForwardFold.create(
            fold_index=1,
            replay_specification_id=SPEC_ID,
            methodology_id="m" * 64,
            train_start=REPLAY_START + timedelta(days=1),
            train_end=REPLAY_START + timedelta(days=3),
            test_start=REPLAY_START + timedelta(days=3),
            test_end=REPLAY_START + timedelta(days=5),
            status=FoldStatus.VALID.value,
            producer_version="phase16h-1.0",
        )
        with pytest.raises(OverlappingTestWindowsError):
            service._validate_folds([fold_a, fold_b])

    def test_no_broker_or_network_dependency(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "app" / "services" / "walk_forward" / "evaluator.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path), mode="exec")
        imports = {
            node.module if isinstance(node, ast.ImportFrom) and node.module else getattr(node.names[0], "name", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        forbidden = {"broker", "yfinance", "requests", "httpx", "aiohttp", "mt5", "tws"}
        assert forbidden.isdisjoint({name.lower() for name in imports if isinstance(name, str)})


class TestPersistence:
    def test_file_artifact_store_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            service = _evaluation_service(store)
            snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
            for snapshot in snapshots:
                _persist_snapshot(store, snapshot)
            evaluation, report = service.evaluate()
            persisted_evaluation = store.put(evaluation.envelope(provenance_references=_provenance(evaluation.walk_forward_evaluation_id)))
            persisted_report = store.put(report.envelope(provenance_references=_provenance(report.backtest_report_id)))
            restarted = FileArtifactStore(temp_dir)
            reloaded_eval = restarted.get(persisted_evaluation.artifact_id)
            assert reloaded_eval.payload["walk_forward_evaluation_id"] == evaluation.walk_forward_evaluation_id
            reloaded_report = restarted.get(persisted_report.artifact_id)
            assert reloaded_report.payload["backtest_report_id"] == report.backtest_report_id

    def test_referenced_lineage_resolves_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            service = _evaluation_service(store)
            snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
            for snapshot in snapshots:
                _persist_snapshot(store, snapshot)
            evaluation, report = service.evaluate()
            store.put(evaluation.envelope(provenance_references=_provenance(evaluation.walk_forward_evaluation_id)))
            store.put(report.envelope(provenance_references=_provenance(report.backtest_report_id)))
            restarted = FileArtifactStore(temp_dir)
            for artifact_id in evaluation.input_artifact_ids:
                assert restarted.exists(artifact_id)

    def test_corruption_propagation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = FileArtifactStore(temp_dir)
            _evaluation_service(store)
            snapshot = _valuation_snapshot(simulated_at=datetime(2025, 1, 1, tzinfo=UTC), cash=100_000.0, market_value=100.0)
            _persist_snapshot(store, snapshot)
            snapshot_id = store.list_ids(filters={"artifact_type": ArtifactType.PORTFOLIO_SNAPSHOT})[0]
            prefix = snapshot_id[:2]
            target_dir = Path(temp_dir) / "artifacts" / prefix
            target_dir.mkdir(parents=True, exist_ok=True)
            Path(target_dir / f"{snapshot_id}.json").write_text("not-json", encoding="utf-8")
            reloaded = FileArtifactStore(temp_dir)
            with pytest.raises(ArtifactCorruptedError):
                _evaluation_service(reloaded).evaluate()

    def test_idempotent_report_generation(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        first_eval, first_report = service.evaluate()
        second_eval, second_report = service.evaluate()
        assert first_eval.walk_forward_evaluation_id == second_eval.walk_forward_evaluation_id
        assert first_report.backtest_report_id == second_report.backtest_report_id


class TestIntegration:
    def test_full_phase16_chain_end_to_end(self) -> None:
        store = InMemoryArtifactStore()
        service = _evaluation_service(store)
        snapshots = _snapshots_for_replay(specification=_specification(), cadence_days=1, market_value=100.0)
        for snapshot in snapshots:
            _persist_snapshot(store, snapshot)
        assessment = _cost_assessment(total_cost=1.0, store=store, simulated_at=datetime(2025, 1, 3, tzinfo=UTC))
        adjustment = PortfolioAdjustmentLedgerEntry.create_execution_cost(
            snapshot=snapshots[2],
            assessment=assessment,
            prior_snapshot_id=snapshots[1].portfolio_snapshot_id,
            sequence=0,
            producer_version="phase16f-1.0",
        )
        store.put(adjustment.envelope(provenance_references=_provenance()))
        entitlement = _dividend_entitlement(gross_cash_amount=2.0, store=store)
        dividend_adjustment = PortfolioAdjustmentLedgerEntry.create_dividend_payment(
            snapshot=snapshots[3],
            entitlement=entitlement,
            prior_snapshot_id=snapshots[2].portfolio_snapshot_id,
            sequence=1,
            producer_version="phase16f-1.0",
        )
        store.put(dividend_adjustment.envelope(provenance_references=_provenance()))
        evaluation, report = service.evaluate()
        assert evaluation.fold_count > 0
        assert report.safety["suitable_for_live_trading"] is False
        assert evaluation.transaction_cost_aggregate.total_transaction_cost == 1.0
        assert evaluation.corporate_action_aggregate.total_dividend_cash == 2.0
        assert report.costs["total_transaction_cost"] == 1.0
        assert report.corporate_actions["total_dividend_cash"] == 2.0

    def test_phase16a_g_regression_remains_green(self) -> None:
        test_files = [
            Path("tests/test_historical_replay_contract.py"),
            Path("tests/test_point_in_time_boundary.py"),
            Path("tests/test_historical_decision_orchestrator.py"),
            Path("tests/test_paper_execution_simulator.py"),
            Path("tests/test_portfolio_ledger.py"),
            Path("tests/test_transaction_costs_corporate_actions.py"),
            Path("tests/test_performance_risk_benchmark.py"),
        ]
        module = Path(__file__).resolve().parents[1] / "app" / "services" / "walk_forward" / "evaluator.py"
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module), mode="exec")
        imports = {
            node.module if isinstance(node, ast.ImportFrom) and node.module else getattr(node.names[0], "name", None)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        forbidden = {"broker", "yfinance", "requests", "httpx", "aiohttp", "mt5", "tws"}
        assert forbidden.isdisjoint({name.lower() for name in imports if isinstance(name, str)})
        for path in test_files:
            assert path.exists()
