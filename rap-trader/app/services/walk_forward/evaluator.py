"""Deterministic Phase 16H walk-forward evaluation service."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from pydantic import ValidationError

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import HistoricalBarsResult, _require_aware_utc
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.performance_evaluation.errors import (
    InsufficientSampleError,
    PerformanceEvaluationError,
)
from app.services.performance_evaluation.evaluator import PerformanceEvaluationService
from app.services.performance_evaluation.models import (
    BenchmarkSpecification,
    CorporateActionAggregate,
    DrawdownPeriod,
    HistoricalPerformanceEvaluation,
    MetricValue,
    PerformanceEvaluationMethodology,
    PerformanceMetrics,
    RiskMetrics,
    TransactionCostAggregate,
)
from app.services.portfolio_accounting.models import PortfolioSnapshot
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentLedgerEntry,
)
from app.services.walk_forward.errors import (
    FutureEvaluationContaminationError,
    InsufficientHistoryError,
    InvalidPerformanceEvaluationLinkageError,
    InvalidWalkForwardMethodologyError,
    InvalidWindowOrderingError,
    OverlappingTestWindowsError,
    WrongReplayLinkageError,
)
from app.services.walk_forward.models import (
    FoldStabilityMetrics,
    FoldStatus,
    HistoricalBacktestReport,
    IncompleteFinalFoldPolicy,
    WalkForwardEvaluation,
    WalkForwardEvaluationMethodology,
    WalkForwardFold,
    WalkForwardMode,
)


def _normalize_ts(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    assert isinstance(value, datetime)
    return _require_aware_utc(value)


def _parse_duration(value: str) -> timedelta:
    value = value.strip()
    if value.isdigit():
        return timedelta(days=int(value))
    suffixes = {
        "d": "days",
        "w": "weeks",
        "M": "days",
        "y": "days",
    }
    if value.endswith(tuple(suffixes.keys())):
        suffix = value[-1]
        number = float(value[:-1])
        if suffix == "d":
            return timedelta(days=number)
        if suffix == "w":
            return timedelta(weeks=number)
        if suffix == "M":
            return timedelta(days=number * 30)
        if suffix == "y":
            return timedelta(days=number * 365)
    raise InvalidWalkForwardMethodologyError(f"unsupported duration format: {value}")


class WalkForwardEvaluationService:
    """Chronological walk-forward evaluation over immutable Phase 16 artifacts."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        specification: HistoricalReplaySpecification,
        methodology: WalkForwardEvaluationMethodology,
        benchmark: HistoricalBarsResult | None = None,
        producer_version: str = "phase16h-1.0",
        compute_train_metrics: bool = False,
    ) -> None:
        self.store = store
        self.specification = specification
        self.methodology = methodology
        self.benchmark = benchmark
        self.producer_version = producer_version
        self.compute_train_metrics = compute_train_metrics
        self._validate_methodology()
        self._validate_specification_linkage()

    def _validate_methodology(self) -> None:
        if self.methodology.mode not in {WalkForwardMode.ANCHORED.value, WalkForwardMode.ROLLING.value}:
            raise InvalidWalkForwardMethodologyError("mode must be ANCHORED or ROLLING")
        try:
            _parse_duration(self.methodology.train_window)
            _parse_duration(self.methodology.test_window)
            _parse_duration(self.methodology.step)
            _parse_duration(self.methodology.embargo)
        except InvalidWalkForwardMethodologyError as exc:
            raise InvalidWalkForwardMethodologyError(f"invalid methodology duration: {exc.message}") from exc

    def _validate_specification_linkage(self) -> None:
        expected_id = self._expected_specification_id_from_store()
        if self.specification.specification_id != expected_id:
            raise WrongReplayLinkageError("specification_id does not match persisted replay linkage")

    def _expected_specification_id_from_store(self) -> str:
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            if envelope.artifact_type == ArtifactType.HISTORICAL_REPLAY_SPECIFICATION:
                payload = envelope.payload if isinstance(envelope.payload, dict) else {}
                return str(payload.get("specification_id", ""))
        return self.specification.specification_id

    def _load_ordered_snapshots(self) -> list[PortfolioSnapshot]:
        snapshots: list[tuple[str, PortfolioSnapshot]] = []
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            if envelope.artifact_type != ArtifactType.PORTFOLIO_SNAPSHOT:
                continue
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            if payload.get("replay_specification_id") != self.specification.specification_id:
                continue
            try:
                snapshot = PortfolioSnapshot.model_validate(payload)
            except ValidationError as exc:
                raise PerformanceEvaluationError(
                    code="INVALID_SNAPSHOT", message=f"invalid portfolio snapshot payload: {artifact_id}"
                ) from exc
            snapshots.append((str(payload.get("simulated_at", "")), snapshot))
        snapshots.sort(key=lambda item: item[0])
        return [snapshot for _, snapshot in snapshots]

    def _load_adjustments(
        self,
    ) -> tuple[list[PortfolioAdjustmentLedgerEntry], list[CorporateActionEvent], list[DividendEntitlement], list[ExecutionCostAssessment]]:
        adjustments: list[PortfolioAdjustmentLedgerEntry] = []
        corporate_actions: list[CorporateActionEvent] = []
        entitlements: list[DividendEntitlement] = []
        assessments: list[ExecutionCostAssessment] = []
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            if payload.get("replay_specification_id") != self.specification.specification_id:
                continue
            if envelope.artifact_type == ArtifactType.PORTFOLIO_LEDGER_ENTRY and payload.get("event_type") in {
                "execution_cost",
                "corporate_action_split",
                "dividend_payment",
            }:
                try:
                    adjustments.append(PortfolioAdjustmentLedgerEntry.model_validate(payload))
                except ValidationError as exc:
                    raise PerformanceEvaluationError(
                        code="INVALID_ADJUSTMENT", message=f"invalid adjustment payload: {artifact_id}"
                    ) from exc
            elif envelope.artifact_type == ArtifactType.CORPORATE_ACTION:
                try:
                    corporate_actions.append(CorporateActionEvent.model_validate(payload))
                except ValidationError as exc:
                    raise PerformanceEvaluationError(
                        code="INVALID_ADJUSTMENT", message=f"invalid corporate action payload: {artifact_id}"
                    ) from exc
            elif envelope.artifact_type == ArtifactType.DIVIDEND_ENTITLEMENT:
                try:
                    entitlements.append(DividendEntitlement.model_validate(payload))
                except ValidationError as exc:
                    raise PerformanceEvaluationError(
                        code="INVALID_ADJUSTMENT", message=f"invalid entitlement payload: {artifact_id}"
                    ) from exc
            elif envelope.artifact_type == ArtifactType.EXECUTION_COST_ASSESSMENT:
                try:
                    assessments.append(ExecutionCostAssessment.model_validate(payload))
                except ValidationError as exc:
                    raise PerformanceEvaluationError(
                        code="INVALID_ADJUSTMENT", message=f"invalid cost assessment payload: {artifact_id}"
                    ) from exc
        return adjustments, corporate_actions, entitlements, assessments

    def _reject_future_evaluation_artifacts(self, max_timestamp: datetime) -> None:
        disallowed_types = {
            ArtifactType.OUTCOME_EVALUATION,
            ArtifactType.ATTRIBUTION_RECORD,
            ArtifactType.CHAMPION_CHALLENGER_EVALUATION,
            ArtifactType.OUTCOME_OBSERVATION,
            ArtifactType.HISTORICAL_PERFORMANCE_EVALUATION,
            ArtifactType.WALK_FORWARD_EVALUATION,
            ArtifactType.HISTORICAL_BACKTEST_REPORT,
        }
        for artifact_id in self.store.list_ids():
            try:
                envelope = self.store.get(artifact_id)
            except ArtifactCorruptedError as exc:
                raise FutureEvaluationContaminationError(f"{artifact_id}: corrupted") from exc
            if envelope.artifact_type in disallowed_types and envelope.logical_as_of > max_timestamp:
                raise FutureEvaluationContaminationError(f"{artifact_id}: {envelope.artifact_type.value}")

    def _subset_store(
        self,
        *,
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
        benchmark: HistoricalBarsResult | None = None,
    ) -> InMemoryArtifactStore:
        store = InMemoryArtifactStore()
        provenance = (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.DETERMINISTIC_SOURCE,
                identifier=self.specification.specification_id,
                description="walk-forward subset lineage",
                producer=self.producer_version,
                producer_version="1.0",
            ),
        )
        spec_envelope = ArtifactEnvelope.create(
            payload=self.specification.model_dump(mode="json", exclude_none=False),
            artifact_type=ArtifactType.HISTORICAL_REPLAY_SPECIFICATION,
            logical_as_of=self.specification.logical_as_of,
            producer_version=self.specification.producer_version,
            provenance_references=provenance,
        )
        store.put(spec_envelope)
        for snapshot in snapshots:
            store.put(snapshot.envelope(provenance_references=provenance))
        for adjustment in adjustments:
            store.put(adjustment.envelope(provenance_references=provenance))
        for action in corporate_actions:
            store.put(action.envelope(provenance_references=provenance))
        for entitlement in entitlements:
            store.put(entitlement.envelope(provenance_references=provenance))
        for assessment in assessments:
            store.put(assessment.envelope(provenance_references=provenance))
        if benchmark is not None:
            bench_envelope = ArtifactEnvelope.create(
                payload=benchmark.model_dump(mode="json", exclude_none=False),
                artifact_type=ArtifactType.HISTORICAL_BARS_RESULT,
                logical_as_of=benchmark.actual_start,
                producer_version="phase16h-1.0",
                provenance_references=provenance,
            )
            store.put(bench_envelope)
        return store

    def _evaluate_subset(
        self,
        *,
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
        benchmark: HistoricalBarsResult | None = None,
    ) -> HistoricalPerformanceEvaluation:
        subset = self._subset_store(
            snapshots=snapshots,
            adjustments=adjustments,
            corporate_actions=corporate_actions,
            entitlements=entitlements,
            assessments=assessments,
            benchmark=benchmark,
        )
        benchmark_specification = None
        if benchmark is not None:
            benchmark_specification = BenchmarkSpecification(
                benchmark_id="0" * 64,
                symbol=benchmark.symbol,
                price_methodology="close",
                return_methodology="price_return",
                base_currency=str(benchmark.currency),
                timeframe=benchmark.timeframe,
                source_version="walk-forward-subset",
                replay_specification_id=self.specification.specification_id,
                replay_run_id=self.specification.run_id,
                producer_version="phase16h-1.0",
            )
        evaluator = PerformanceEvaluationService(
            store=subset,
            specification=self.specification,
            methodology=PerformanceEvaluationMethodology(
                methodology_id="0" * 64,
                methodology_name="walk_forward_subset",
                periods_per_year=252.0,
                producer_version="phase16h-1.0",
            ),
            benchmark=benchmark_specification,
            producer_version="phase16h-1.0",
        )
        return evaluator.evaluate()

    def _observation_count_for_window(self, snapshots: Sequence[PortfolioSnapshot], start: datetime, end: datetime) -> int:
        return len([s for s in snapshots if start <= s.simulated_at < end])

    def _build_folds(
        self,
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
    ) -> list[WalkForwardFold]:
        if not snapshots:
            raise InsufficientHistoryError("no portfolio snapshots available")
        replay_start = self.specification.start_time
        replay_end = self.specification.end_time
        train_duration = _parse_duration(self.methodology.train_window)
        test_duration = _parse_duration(self.methodology.test_window)
        step_duration = _parse_duration(self.methodology.step)
        embargo_duration = _parse_duration(self.methodology.embargo)
        folds: list[WalkForwardFold] = []
        cursor = replay_start + train_duration
        fold_index = 0
        while True:
            train_end = cursor
            test_start = train_end + embargo_duration
            test_end = test_start + test_duration
            if test_end > replay_end:
                if self.methodology.incomplete_final_fold_policy == IncompleteFinalFoldPolicy.DROP_INCOMPLETE.value:
                    break
                test_end = replay_end
            if test_start >= replay_end:
                break
            if test_start >= test_end:
                break
            test_observation_count = self._observation_count_for_window(snapshots, test_start, test_end)
            training_observation_count = self._observation_count_for_window(snapshots, replay_start, train_end)
            status = FoldStatus.VALID.value
            warnings: list[str] = []
            if test_observation_count < self.methodology.minimum_test_observations:
                if self.methodology.incomplete_final_fold_policy == IncompleteFinalFoldPolicy.DROP_INCOMPLETE.value:
                    if test_end >= replay_end:
                        break
                    cursor += step_duration
                    continue
                status = FoldStatus.INSUFFICIENT_DATA.value
                warnings.append("insufficient test observations")
            fold = WalkForwardFold.create(
                fold_index=fold_index,
                replay_specification_id=self.specification.specification_id,
                methodology_id=self.methodology.methodology_id,
                train_start=replay_start if self.methodology.mode == WalkForwardMode.ANCHORED.value else train_end - train_duration,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                embargo_start=train_end if embargo_duration.total_seconds() > 0 else None,
                embargo_end=test_start if embargo_duration.total_seconds() > 0 else None,
                training_observation_count=training_observation_count,
                test_observation_count=test_observation_count,
                status=status,
                warnings=tuple(warnings),
                retrained=False,
                suitable_for_live_trading=False,
                producer_version=self.producer_version,
            )
            folds.append(fold)
            fold_index += 1
            if test_end >= replay_end:
                break
            cursor += step_duration
        return folds

    def _validate_folds(self, folds: Sequence[WalkForwardFold]) -> None:
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]
        test_windows = [(fold.test_start, fold.test_end) for fold in valid_folds]
        for i, (a_start, a_end) in enumerate(test_windows):
            for j, (b_start, b_end) in enumerate(test_windows):
                if i >= j:
                    continue
                if not (a_end <= b_start or b_end <= a_start):
                    raise OverlappingTestWindowsError(
                        f"overlapping test windows: fold {i} [{a_start.isoformat()}, {a_end.isoformat()}) and fold {j} [{b_start.isoformat()}, {b_end.isoformat()})"
                    )
        # chronological ordering
        for i in range(1, len(valid_folds)):
            if valid_folds[i].test_start < valid_folds[i - 1].test_start:
                raise InvalidWindowOrderingError("test windows are not chronological")
            if valid_folds[i].fold_index <= valid_folds[i - 1].fold_index:
                raise InvalidWindowOrderingError("fold indices are not strictly increasing")

    def _evaluate_fold(
        self,
        fold: WalkForwardFold,
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
    ) -> HistoricalPerformanceEvaluation | None:
        test_snapshots = [s for s in snapshots if fold.test_start <= s.simulated_at < fold.test_end]
        test_adjustments = [a for a in adjustments if fold.test_start <= a.simulated_at < fold.test_end]
        test_corporate_actions = [
            a for a in corporate_actions if a.announced_at is not None and fold.test_start <= a.announced_at < fold.test_end
        ]
        test_entitlements = [e for e in entitlements if fold.test_start <= e.ex_date < fold.test_end]
        test_assessments = [a for a in assessments if fold.test_start <= a.simulated_at < fold.test_end]
        if not test_snapshots:
            return None
        try:
            return self._evaluate_subset(
                snapshots=test_snapshots,
                adjustments=test_adjustments,
                corporate_actions=test_corporate_actions,
                entitlements=test_entitlements,
                assessments=test_assessments,
                benchmark=self.benchmark,
            )
        except InsufficientSampleError:
            return None
        except PerformanceEvaluationError as exc:
            raise InvalidPerformanceEvaluationLinkageError(f"fold {fold.fold_index} evaluation failed: {exc.message}") from exc

    def _evaluate_train_window(
        self,
        fold: WalkForwardFold,
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
    ) -> HistoricalPerformanceEvaluation | None:
        train_snapshots = [s for s in snapshots if fold.train_start <= s.simulated_at < fold.train_end]
        train_adjustments = [a for a in adjustments if fold.train_start <= a.simulated_at < fold.train_end]
        train_corporate_actions = [
            a for a in corporate_actions if a.announced_at is not None and fold.train_start <= a.announced_at < fold.train_end
        ]
        train_entitlements = [e for e in entitlements if fold.train_start <= e.ex_date < fold.train_end]
        train_assessments = [a for a in assessments if fold.train_start <= a.simulated_at < fold.train_end]
        if len(train_snapshots) < 2:
            return None
        try:
            return self._evaluate_subset(
                snapshots=train_snapshots,
                adjustments=train_adjustments,
                corporate_actions=train_corporate_actions,
                entitlements=train_entitlements,
                assessments=train_assessments,
                benchmark=self.benchmark,
            )
        except InsufficientSampleError:
            return None
        except PerformanceEvaluationError as exc:
            raise InvalidPerformanceEvaluationLinkageError(f"fold {fold.fold_index} train evaluation failed: {exc.message}") from exc

    def _aggregate_oos(
        self,
        valid_folds: Sequence[WalkForwardFold],
        snapshots: Sequence[PortfolioSnapshot],
        adjustments: Sequence[PortfolioAdjustmentLedgerEntry],
        corporate_actions: Sequence[CorporateActionEvent],
        entitlements: Sequence[DividendEntitlement],
        assessments: Sequence[ExecutionCostAssessment],
        benchmark: HistoricalBarsResult | None = None,
    ) -> tuple[HistoricalPerformanceEvaluation, FoldStabilityMetrics]:
        if not valid_folds:
            empty_metrics = PerformanceMetrics(
                starting_equity=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                ending_equity=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                total_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                cagr=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                cumulative_return_series=(),
                positive_period_ratio=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                period_return_count=0,
                producer_version=self.producer_version,
            )
            empty_risk = RiskMetrics(
                annualized_volatility=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                downside_deviation=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                maximum_drawdown=self._empty_drawdown(),
                sharpe_ratio=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                sortino_ratio=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                calmar_ratio=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                best_period_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                worst_period_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                producer_version=self.producer_version,
            )
            empty_costs = TransactionCostAggregate(
                assessment_count=0,
                total_commission=0.0,
                total_spread_cost=0.0,
                total_slippage_cost=0.0,
                total_transaction_cost=0.0,
                cost_to_starting_capital_ratio=MetricValue(
                    status="unavailable", reason="no valid folds", producer_version=self.producer_version
                ),
                producer_version=self.producer_version,
            )
            empty_corporate = CorporateActionAggregate(
                dividend_count=0,
                total_dividend_cash=0.0,
                split_count=0,
                producer_version=self.producer_version,
            )
            evaluation = HistoricalPerformanceEvaluation.create(
                replay_specification_id=self.specification.specification_id,
                replay_run_id=self.specification.run_id,
                methodology_id=self.methodology.performance_evaluation_methodology_id,
                first_snapshot_id=None,
                last_snapshot_id=None,
                snapshot_count=0,
                valued_snapshot_count=0,
                return_observation_count=0,
                performance_metrics=empty_metrics,
                risk_metrics=empty_risk,
                transaction_cost_aggregate=empty_costs,
                corporate_action_aggregate=empty_corporate,
                benchmark_comparison=None,
                input_artifact_ids=(),
                logical_as_of=self.specification.end_time,
                producer_version=self.producer_version,
            )
            stability = FoldStabilityMetrics(
                fold_count=len(valid_folds),
                valid_fold_count=0,
                insufficient_fold_count=sum(1 for f in valid_folds if f.status == FoldStatus.INSUFFICIENT_DATA.value),
                positive_fold_count=0,
                negative_fold_count=0,
                positive_fold_ratio=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                best_fold_total_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                worst_fold_total_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                median_fold_total_return=MetricValue(status="unavailable", reason="no valid folds", producer_version=self.producer_version),
                producer_version=self.producer_version,
            )
            return evaluation, stability

        # Build aggregate subset from union of valid test windows.
        oos_start = min(fold.test_start for fold in valid_folds)
        oos_end = max(fold.test_end for fold in valid_folds)
        aggregate_snapshots = [s for s in snapshots if oos_start <= s.simulated_at < oos_end]
        aggregate_adjustments = [a for a in adjustments if oos_start <= a.simulated_at < oos_end]
        aggregate_corporate_actions = [a for a in corporate_actions if a.announced_at is not None and oos_start <= a.announced_at < oos_end]
        aggregate_entitlements = [e for e in entitlements if oos_start <= e.ex_date < oos_end]
        aggregate_assessments = [a for a in assessments if oos_start <= a.simulated_at < oos_end]

        evaluation = self._evaluate_subset(
            snapshots=aggregate_snapshots,
            adjustments=aggregate_adjustments,
            corporate_actions=aggregate_corporate_actions,
            entitlements=aggregate_entitlements,
            assessments=aggregate_assessments,
            benchmark=benchmark,
        )

        fold_returns: list[float] = []
        fold_sharpes: list[float] = []
        fold_drawdowns: list[float] = []
        for fold in valid_folds:
            fold_eval = self._evaluate_fold(
                fold,
                snapshots,
                adjustments,
                corporate_actions,
                entitlements,
                assessments,
            )
            if fold_eval is None:
                continue
            total_return = fold_eval.performance_metrics.total_return
            if total_return.status == "available" and total_return.value is not None:
                fold_returns.append(total_return.value)
            sharpe = fold_eval.risk_metrics.sharpe_ratio
            if sharpe.status == "available" and sharpe.value is not None:
                fold_sharpes.append(sharpe.value)
            max_dd = fold_eval.risk_metrics.maximum_drawdown
            if max_dd.status == "available":
                fold_drawdowns.append(max_dd.max_drawdown_percent)

        positive_count = sum(1 for value in fold_returns if value > 0)
        negative_count = sum(1 for value in fold_returns if value < 0)
        fold_count = len(valid_folds)
        positive_ratio = positive_count / fold_count if fold_count else None

        def _sorted_metric(values: list[float]) -> tuple[MetricValue, MetricValue, MetricValue]:
            if not values:
                return (
                    MetricValue(status="unavailable", reason="no valid fold metrics", producer_version=self.producer_version),
                    MetricValue(status="unavailable", reason="no valid fold metrics", producer_version=self.producer_version),
                    MetricValue(status="unavailable", reason="no valid fold metrics", producer_version=self.producer_version),
                )
            sorted_values = sorted(values)
            median = sorted_values[len(sorted_values) // 2]
            best = sorted_values[-1]
            worst = sorted_values[0]
            return (
                MetricValue(value=round(best, 10), status="available", producer_version=self.producer_version),
                MetricValue(value=round(worst, 10), status="available", producer_version=self.producer_version),
                MetricValue(value=round(median, 10), status="available", producer_version=self.producer_version),
            )

        best_ret, worst_ret, median_ret = _sorted_metric(fold_returns)
        _, _, median_sharpe = _sorted_metric(fold_sharpes)
        worst_dd = MetricValue(status="unavailable", reason="no valid fold metrics", producer_version=self.producer_version)
        if fold_drawdowns:
            worst_dd = MetricValue(value=round(max(fold_drawdowns), 10), status="available", producer_version=self.producer_version)

        stability = FoldStabilityMetrics(
            fold_count=fold_count,
            valid_fold_count=fold_count,
            insufficient_fold_count=0,
            positive_fold_count=positive_count,
            negative_fold_count=negative_count,
            positive_fold_ratio=MetricValue(
                value=round(positive_ratio, 10) if positive_ratio is not None else None,
                status="available" if positive_ratio is not None else "unavailable",
                reason=None if positive_ratio is not None else "no valid folds",
                producer_version=self.producer_version,
            ),
            best_fold_total_return=best_ret,
            worst_fold_total_return=worst_ret,
            median_fold_total_return=median_ret,
            median_fold_sharpe=median_sharpe,
            worst_fold_drawdown=worst_dd,
            producer_version=self.producer_version,
        )
        return evaluation, stability

    def _empty_drawdown(self) -> DrawdownPeriod:
        return DrawdownPeriod(
            max_drawdown=0.0,
            max_drawdown_percent=0.0,
            peak_timestamp=self.specification.start_time,
            trough_timestamp=self.specification.start_time,
            recovery_timestamp=self.specification.start_time,
            duration=0.0,
            status="unavailable",
            reason="no valid folds",
            producer_version=self.producer_version,
        )

    def _build_report(
        self,
        evaluation: WalkForwardEvaluation,
        full_evaluation: HistoricalPerformanceEvaluation | None = None,
    ) -> HistoricalBacktestReport:
        performance: dict[str, Any] = {}
        if full_evaluation is not None:
            performance = {
                "total_return": full_evaluation.performance_metrics.total_return.model_dump(mode="json"),
                "cagr": full_evaluation.performance_metrics.cagr.model_dump(mode="json"),
                "volatility": full_evaluation.risk_metrics.annualized_volatility.model_dump(mode="json"),
                "sharpe": full_evaluation.risk_metrics.sharpe_ratio.model_dump(mode="json"),
                "sortino": full_evaluation.risk_metrics.sortino_ratio.model_dump(mode="json"),
                "calmar": full_evaluation.risk_metrics.calmar_ratio.model_dump(mode="json"),
                "maximum_drawdown": full_evaluation.risk_metrics.maximum_drawdown.model_dump(mode="json"),
            }
        benchmark: dict[str, Any] = {}
        if full_evaluation is not None and full_evaluation.benchmark_comparison is not None:
            benchmark = {
                "benchmark_total_return": full_evaluation.benchmark_comparison.benchmark_total_return.model_dump(mode="json"),
                "excess_return": full_evaluation.benchmark_comparison.portfolio_excess_total_return.model_dump(mode="json"),
                "tracking_error": full_evaluation.benchmark_comparison.tracking_error.model_dump(mode="json"),
                "information_ratio": full_evaluation.benchmark_comparison.information_ratio.model_dump(mode="json"),
                "beta": full_evaluation.benchmark_comparison.beta.model_dump(mode="json"),
                "alpha": full_evaluation.benchmark_comparison.alpha.model_dump(mode="json"),
                "correlation": full_evaluation.benchmark_comparison.correlation.model_dump(mode="json"),
            }
        costs = {
            "total_transaction_cost": full_evaluation.transaction_cost_aggregate.total_transaction_cost if full_evaluation else None,
            "total_commission": full_evaluation.transaction_cost_aggregate.total_commission if full_evaluation else None,
            "total_spread_cost": full_evaluation.transaction_cost_aggregate.total_spread_cost if full_evaluation else None,
            "total_slippage_cost": full_evaluation.transaction_cost_aggregate.total_slippage_cost if full_evaluation else None,
        }
        corporate_actions = {
            "dividend_count": full_evaluation.corporate_action_aggregate.dividend_count if full_evaluation else None,
            "total_dividend_cash": full_evaluation.corporate_action_aggregate.total_dividend_cash if full_evaluation else None,
            "split_count": full_evaluation.corporate_action_aggregate.split_count if full_evaluation else None,
        }
        walk_forward = {
            "fold_count": evaluation.fold_count,
            "valid_fold_count": evaluation.valid_fold_count,
            "insufficient_fold_count": evaluation.insufficient_fold_count,
            "oos_performance": evaluation.oos_performance_metrics.model_dump(mode="json"),
            "oos_risk": evaluation.oos_risk_metrics.model_dump(mode="json"),
            "fold_stability": evaluation.fold_stability_metrics.model_dump(mode="json"),
        }
        warnings = list(evaluation.warnings)
        if evaluation.insufficient_fold_count:
            warnings.append(f"{evaluation.insufficient_fold_count} fold(s) had insufficient data")
        if not evaluation.fold_count:
            warnings.append("no walk-forward folds were generated")
        return HistoricalBacktestReport.create(
            backtest_report_id="",
            replay_specification_id=self.specification.specification_id,
            backtest_run_manifest_id=None,
            walk_forward_evaluation_id=evaluation.walk_forward_evaluation_id,
            performance_evaluation_id=full_evaluation.evaluation_id if full_evaluation else None,
            methodology_ids=(self.methodology.methodology_id,),
            producer_version=self.producer_version,
            status="COMPLETE_WITH_WARNINGS" if warnings else "COMPLETE",
            scope={
                "replay_start": self.specification.start_time.isoformat(),
                "replay_end": self.specification.end_time.isoformat(),
                "instruments": [str(symbol) for symbol in self.specification.instruments],
                "initial_capital": self.specification.initial_capital,
                "base_currency": self.specification.base_currency,
                "walk_forward_mode": self.methodology.mode,
            },
            data_quality={
                "snapshot_count": full_evaluation.snapshot_count if full_evaluation else None,
                "valued_snapshot_count": full_evaluation.valued_snapshot_count if full_evaluation else None,
                "return_observation_count": full_evaluation.return_observation_count if full_evaluation else None,
            },
            performance=performance,
            benchmark=benchmark,
            costs=costs,
            corporate_actions=corporate_actions,
            walk_forward=walk_forward,
            reproducibility={
                "replay_specification_id": self.specification.specification_id,
                "methodology_ids": [self.methodology.methodology_id],
                "logical_as_of": self.specification.logical_as_of.isoformat(),
                "deterministic_report_id": None,
            },
            safety={
                "research_only": True,
                "paper_trading_only": True,
                "suitable_for_live_trading": False,
                "no_broker_authority": True,
            },
            warnings=tuple(warnings),
            limitations=(
                "historical evidence only; not live-trading approval",
                "walk-forward evaluation does not imply retrained model artifacts",
                "missing or degraded point-in-time data may reduce confidence",
            ),
            logical_as_of=self.specification.logical_as_of,
        )

    def evaluate(self, *, benchmark: HistoricalBarsResult | None = None) -> tuple[WalkForwardEvaluation, HistoricalBacktestReport]:
        self._reject_future_evaluation_artifacts(self.specification.end_time)
        snapshots = self._load_ordered_snapshots()
        if not snapshots:
            raise InsufficientHistoryError("no portfolio snapshots available")
        adjustments, corporate_actions, entitlements, assessments = self._load_adjustments()
        folds = self._build_folds(snapshots, adjustments, corporate_actions, entitlements, assessments)
        self._validate_folds(folds)
        valid_folds = [fold for fold in folds if fold.status == FoldStatus.VALID.value]

        effective_benchmark = benchmark if benchmark is not None else self.benchmark
        full_evaluation = self._evaluate_subset(
            snapshots=snapshots,
            adjustments=adjustments,
            corporate_actions=corporate_actions,
            entitlements=entitlements,
            assessments=assessments,
            benchmark=effective_benchmark,
        )

        oos_evaluation, stability = self._aggregate_oos(
            valid_folds,
            snapshots,
            adjustments,
            corporate_actions,
            entitlements,
            assessments,
            benchmark=effective_benchmark,
        )
        oos_start = min((fold.test_start for fold in valid_folds), default=self.specification.start_time)
        oos_end = max((fold.test_end for fold in valid_folds), default=self.specification.end_time)
        evaluation = WalkForwardEvaluation.create(
            replay_specification_id=self.specification.specification_id,
            backtest_run_manifest_id=None,
            methodology_id=self.methodology.methodology_id,
            performance_methodology_id=self.methodology.performance_evaluation_methodology_id,
            fold_ids=tuple(fold.fold_id for fold in folds),
            fold_count=len(folds),
            valid_fold_count=len(valid_folds),
            insufficient_fold_count=sum(1 for fold in folds if fold.status == FoldStatus.INSUFFICIENT_DATA.value),
            oos_start=oos_start,
            oos_end=oos_end,
            oos_performance_metrics=oos_evaluation.performance_metrics,
            oos_risk_metrics=oos_evaluation.risk_metrics,
            fold_stability_metrics=stability,
            benchmark_comparison=oos_evaluation.benchmark_comparison,
            transaction_cost_aggregate=oos_evaluation.transaction_cost_aggregate,
            corporate_action_aggregate=oos_evaluation.corporate_action_aggregate,
            warnings=tuple(filter(None, (oos_evaluation.performance_metrics.total_return.reason,))),
            input_artifact_ids=tuple(self.store.list_ids()),
            logical_as_of=self.specification.end_time,
            producer_version=self.producer_version,
        )
        report = self._build_report(evaluation, full_evaluation=full_evaluation)
        return evaluation, report
