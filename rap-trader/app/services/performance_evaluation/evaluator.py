"""Deterministic Phase 16G performance, risk, and benchmark evaluation service."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import ValidationError

from app.domain.models.artifact import ArtifactType
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import _require_aware_utc
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.performance_evaluation.errors import (
    InsufficientSampleError,
    InvalidMethodologyError,
    LookaheadContaminationError,
    MismatchedReplayLinkageError,
    MissingBenchmarkDataError,
    PerformanceEvaluationError,
)
from app.services.performance_evaluation.models import (
    BenchmarkComparison,
    BenchmarkReturnObservation,
    BenchmarkReturnSeries,
    BenchmarkSpecification,
    CorporateActionAggregate,
    DrawdownPeriod,
    HistoricalPerformanceEvaluation,
    MetricValue,
    PerformanceEvaluationMethodology,
    PerformanceMetrics,
    PortfolioReturnObservation,
    PortfolioReturnSeries,
    RiskMetrics,
    TransactionCostAggregate,
)
from app.services.portfolio_accounting.models import PortfolioSnapshot
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    CorporateActionType,
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentLedgerEntry,
    PortfolioAdjustmentType,
)


class PerformanceEvaluationService:
    """Evaluate immutable historical portfolio performance from persisted snapshots."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        specification: HistoricalReplaySpecification,
        methodology: PerformanceEvaluationMethodology,
        benchmark: BenchmarkSpecification | None = None,
        producer_version: str = "phase16g-1.0",
    ) -> None:
        self.store = store
        self.specification = specification
        self.methodology = methodology
        self.benchmark = benchmark
        self.producer_version = producer_version
        self._validate_specification_linkage()

    def _validate_specification_linkage(self) -> None:
        expected_id = self._expected_specification_id_from_store()
        if self.specification.specification_id != expected_id:
            raise MismatchedReplayLinkageError("specification_id does not match persisted replay linkage")

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
                PortfolioAdjustmentType.EXECUTION_COST.value,
                PortfolioAdjustmentType.CORPORATE_ACTION_SPLIT.value,
                PortfolioAdjustmentType.DIVIDEND_PAYMENT.value,
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
        }
        for artifact_id in self.store.list_ids():
            try:
                envelope = self.store.get(artifact_id)
            except ArtifactCorruptedError as exc:
                raise LookaheadContaminationError(artifact_id, "corrupted") from exc
            if envelope.artifact_type in disallowed_types and envelope.logical_as_of > max_timestamp:
                raise LookaheadContaminationError(artifact_id, envelope.artifact_type.value)

    def _aggregate_transaction_costs(self, assessments: Sequence[ExecutionCostAssessment]) -> TransactionCostAggregate:
        total_commission = round(sum(assessment.commission for assessment in assessments), 10)
        total_spread_cost = round(sum(assessment.spread_cost for assessment in assessments), 10)
        total_slippage_cost = round(sum(assessment.slippage_cost for assessment in assessments), 10)
        total_cost = round(sum(assessment.total_transaction_cost for assessment in assessments), 10)
        starting_capital = self.specification.initial_capital
        ratio = total_cost / starting_capital if starting_capital else None
        return TransactionCostAggregate(
            assessment_count=len(assessments),
            total_commission=total_commission,
            total_spread_cost=total_spread_cost,
            total_slippage_cost=total_slippage_cost,
            total_transaction_cost=total_cost,
            cost_to_starting_capital_ratio=MetricValue(
                value=ratio,
                status="available" if ratio is not None else "unavailable",
                reason=None if ratio is not None else "missing starting capital",
                producer_version=self.producer_version,
            ),
            producer_version=self.producer_version,
        )

    def _aggregate_corporate_actions(
        self, corporate_actions: Sequence[CorporateActionEvent], entitlements: Sequence[DividendEntitlement]
    ) -> CorporateActionAggregate:
        dividend_count = len(entitlements)
        total_dividend_cash = round(sum(entitlement.gross_cash_amount for entitlement in entitlements), 10)
        split_count = sum(1 for action in corporate_actions if action.action_type == CorporateActionType.STOCK_SPLIT.value)
        return CorporateActionAggregate(
            dividend_count=dividend_count,
            total_dividend_cash=total_dividend_cash,
            split_count=split_count,
            producer_version=self.producer_version,
        )

    def build_return_series(self, snapshots: Sequence[PortfolioSnapshot]) -> PortfolioReturnSeries:
        observations: list[PortfolioReturnObservation] = []
        unvalued: list[str] = []
        valued_count = 0
        previous: PortfolioSnapshot | None = None
        for snapshot in snapshots:
            if snapshot.equity is None:
                unvalued.append(snapshot.portfolio_snapshot_id)
                previous = None
                continue
            valued_count += 1
            if previous is None or previous.equity is None:
                previous = snapshot
                continue
            if previous.simulated_at == snapshot.simulated_at:
                previous = snapshot
                continue
            if previous.equity is None or previous.equity <= 0:
                previous = snapshot
                continue
            previous_equity = previous.equity
            period_return = snapshot.equity / previous_equity - 1
            observations.append(
                PortfolioReturnObservation.create(
                    start_snapshot_id=previous.portfolio_snapshot_id,
                    end_snapshot_id=snapshot.portfolio_snapshot_id,
                    start_timestamp=previous.simulated_at,
                    end_timestamp=snapshot.simulated_at,
                    start_equity=previous_equity,
                    end_equity=snapshot.equity,
                    period_return=round(period_return, 10),
                    producer_version=self.producer_version,
                )
            )
            previous = snapshot
        coverage = valued_count / len(snapshots) if snapshots else 0.0
        return PortfolioReturnSeries.create(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=self.specification.run_id,
            methodology_id=self.methodology.methodology_id,
            snapshots_considered=len(snapshots),
            valued_snapshot_count=valued_count,
            unvalued_snapshot_ids=unvalued,
            observations=observations,
            valuation_coverage=round(coverage, 10),
            producer_version=self.producer_version,
        )

    @staticmethod
    def _population_std(values: Sequence[float], mean: float) -> float:
        return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))

    @staticmethod
    def _sample_std(values: Sequence[float], mean: float) -> float:
        count = len(values)
        if count < 2:
            return 0.0
        return math.sqrt(sum((value - mean) ** 2 for value in values) / (count - 1))

    def _performance_metrics(self, series: PortfolioReturnSeries, snapshots: Sequence[PortfolioSnapshot]) -> PerformanceMetrics:
        observations = list(series.observations)
        if len(observations) < 1:
            return PerformanceMetrics(
                starting_equity=MetricValue(
                    status="unavailable", reason="insufficient valued snapshots", producer_version=self.producer_version
                ),
                ending_equity=MetricValue(
                    status="unavailable", reason="insufficient valued snapshots", producer_version=self.producer_version
                ),
                total_return=MetricValue(
                    status="unavailable", reason="insufficient return observations", producer_version=self.producer_version
                ),
                cagr=MetricValue(status="unavailable", reason="insufficient return observations", producer_version=self.producer_version),
                cumulative_return_series=(),
                positive_period_ratio=MetricValue(
                    status="unavailable", reason="insufficient return observations", producer_version=self.producer_version
                ),
                period_return_count=len(observations),
                producer_version=self.producer_version,
            )

        starting_equity = observations[0].start_equity
        ending_equity = observations[-1].end_equity
        total_return = ending_equity / starting_equity - 1 if starting_equity > 0 else None
        returns = [observation.period_return for observation in observations]
        cumulative: list[float] = []
        current = 1.0
        for value in returns:
            current *= 1 + value
            cumulative.append(round(current, 10))
        positive_count = sum(1 for value in returns if value > 0)
        period_count = len(returns)
        cagr: float | None
        if observations[0].start_timestamp == observations[-1].end_timestamp or period_count < 2:
            cagr = None
        else:
            year_fraction = (observations[-1].end_timestamp - observations[0].start_timestamp).total_seconds() / (365.25 * 24 * 60 * 60)
            if year_fraction <= 0 or total_return is None or starting_equity <= 0:
                cagr = None
            else:
                cagr = (ending_equity / starting_equity) ** (1 / year_fraction) - 1
                cagr = round(cagr, 10) if math.isfinite(cagr) else None
        return PerformanceMetrics(
            starting_equity=MetricValue(value=round(starting_equity, 10), status="available", producer_version=self.producer_version),
            ending_equity=MetricValue(value=round(ending_equity, 10), status="available", producer_version=self.producer_version),
            total_return=MetricValue(
                value=round(total_return, 10) if total_return is not None else None,
                status="available" if total_return is not None else "unavailable",
                producer_version=self.producer_version,
            ),
            cagr=MetricValue(value=cagr, status="available" if cagr is not None else "unavailable", producer_version=self.producer_version),
            cumulative_return_series=tuple(cumulative),
            positive_period_ratio=MetricValue(
                value=round(positive_count / period_count, 10), status="available", producer_version=self.producer_version
            ),
            period_return_count=period_count,
            producer_version=self.producer_version,
        )

    def _drawdown_series(self, equity_curve: Sequence[tuple[datetime, float]]) -> DrawdownPeriod:
        if len(equity_curve) < 2:
            return DrawdownPeriod(
                max_drawdown=0.0,
                max_drawdown_percent=0.0,
                peak_timestamp=equity_curve[0][0] if equity_curve else datetime(1970, 1, 1, tzinfo=UTC),
                trough_timestamp=equity_curve[0][0] if equity_curve else datetime(1970, 1, 1, tzinfo=UTC),
                status="available",
                reason="insufficient history" if len(equity_curve) < 2 else None,
                producer_version=self.producer_version,
            )
        peak = equity_curve[0][1]
        peak_time = equity_curve[0][0]
        max_dd = 0.0
        max_pct = 0.0
        trough_time = peak_time
        recovery_time: datetime | None = peak_time
        recovered = True
        trailing_peak = peak
        trailing_peak_time = peak_time
        max_peak_time = peak_time
        for timestamp, equity in equity_curve[1:]:
            if equity >= trailing_peak:
                trailing_peak = equity
                trailing_peak_time = timestamp
                if recovered is False:
                    recovery_time = timestamp
                recovered = True
            else:
                drawdown = trailing_peak - equity
                drawdown_pct = drawdown / trailing_peak if trailing_peak != 0 else 0.0
                if drawdown_pct > max_pct:
                    max_dd = drawdown
                    max_pct = drawdown_pct
                    trough_time = timestamp
                    max_peak_time = trailing_peak_time
                    recovery_time = None
                    recovered = False
        duration = None
        if recovery_time is not None:
            duration = round((recovery_time - peak_time).total_seconds() / 60.0, 10)
        if max_pct == 0.0:
            recovery_time = peak_time
            duration = 0.0
        return DrawdownPeriod(
            max_drawdown=round(max_dd, 10),
            max_drawdown_percent=round(max_pct, 10),
            peak_timestamp=max_peak_time,
            trough_timestamp=trough_time,
            recovery_timestamp=recovery_time,
            duration=duration,
            status="available",
            reason=None,
            producer_version=self.producer_version,
        )

    def _risk_metrics(self, series: PortfolioReturnSeries, observations: Sequence[PortfolioReturnObservation]) -> RiskMetrics:
        returns = [observation.period_return for observation in observations]
        mean_return = sum(returns) / len(returns) if returns else 0.0
        volatility = self._sample_std(returns, mean_return) if returns else 0.0
        annualized_volatility = volatility * math.sqrt(self.methodology.periods_per_year)
        downside_values = [min(0.0, value - self.methodology.periodic_minimum_acceptable_return) for value in returns]
        downside_deviation = math.sqrt(sum(value**2 for value in downside_values) / len(downside_values)) if downside_values else 0.0
        equity_curve = [
            (observation.end_timestamp, observation.end_equity) for observation in sorted(observations, key=lambda item: item.end_timestamp)
        ]
        drawdown = self._drawdown_series(equity_curve)
        max_drawdown_pct = drawdown.max_drawdown_percent if drawdown.status == "available" else 0.0
        sharpe: float | None = None
        sortino: float | None = None
        calmar: float | None = None
        if volatility > 0 and len(returns) > 1:
            sharpe = ((mean_return - self.methodology.periodic_risk_free_rate) / volatility) * math.sqrt(self.methodology.periods_per_year)
            sharpe = round(sharpe, 10) if math.isfinite(sharpe) else None
        if downside_deviation > 0 and len(returns) > 1:
            sortino = ((mean_return - self.methodology.periodic_minimum_acceptable_return) / downside_deviation) * math.sqrt(
                self.methodology.periods_per_year
            )
            sortino = round(sortino, 10) if math.isfinite(sortino) else None
        if max_drawdown_pct > 0 and len(returns) > 1 and annualized_volatility > 0:
            cagr_value = None
            if len(observations) >= 2 and observations[0].start_timestamp != observations[-1].end_timestamp:
                year_fraction = (observations[-1].end_timestamp - observations[0].start_timestamp).total_seconds() / (365.25 * 24 * 60 * 60)
                if year_fraction > 0:
                    cagr_value = (observations[-1].end_equity / observations[0].start_equity) ** (1 / year_fraction) - 1
                    cagr_value = round(cagr_value, 10) if math.isfinite(cagr_value) else None
            if cagr_value is not None:
                calmar = cagr_value / max_drawdown_pct if max_drawdown_pct != 0 else None
                calmar = round(calmar, 10) if calmar is not None and math.isfinite(calmar) else None
        best = max(returns) if returns else None
        worst = min(returns) if returns else None
        return RiskMetrics(
            annualized_volatility=MetricValue(
                value=round(annualized_volatility, 10), status="available", producer_version=self.producer_version
            )
            if annualized_volatility
            else MetricValue(status="unavailable", reason="zero volatility", producer_version=self.producer_version),
            downside_deviation=MetricValue(value=round(downside_deviation, 10), status="available", producer_version=self.producer_version)
            if downside_deviation
            else MetricValue(status="unavailable", reason="zero downside deviation", producer_version=self.producer_version),
            maximum_drawdown=drawdown,
            sharpe_ratio=MetricValue(
                value=sharpe,
                status="available" if sharpe is not None else "unavailable",
                reason=None if sharpe is not None else "zero volatility",
                producer_version=self.producer_version,
            ),
            sortino_ratio=MetricValue(
                value=sortino,
                status="available" if sortino is not None else "unavailable",
                reason=None if sortino is not None else "zero downside deviation",
                producer_version=self.producer_version,
            ),
            calmar_ratio=MetricValue(
                value=calmar,
                status="available" if calmar is not None else "unavailable",
                reason=None if calmar is not None else "invalid calmar inputs",
                producer_version=self.producer_version,
            ),
            best_period_return=MetricValue(
                value=round(best, 10) if best is not None else None,
                status="available" if best is not None else "unavailable",
                producer_version=self.producer_version,
            ),
            worst_period_return=MetricValue(
                value=round(worst, 10) if worst is not None else None,
                status="available" if worst is not None else "unavailable",
                producer_version=self.producer_version,
            ),
            producer_version=self.producer_version,
        )

    def _load_benchmark_series(self, end_timestamp: datetime) -> BenchmarkReturnSeries | None:
        if self.benchmark is None:
            return None
        prices: list[tuple[str, datetime, float]] = []
        for artifact_id in self.store.list_ids():
            try:
                envelope = self.store.get(artifact_id)
            except ArtifactCorruptedError as exc:
                raise MissingBenchmarkDataError("benchmark artifact corrupted") from exc
            if envelope.artifact_type != ArtifactType.HISTORICAL_BARS_RESULT:
                continue
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            if payload.get("symbol") != str(self.benchmark.symbol):
                continue
            bars = payload.get("bars") or []
            if not isinstance(bars, list):
                continue
            for bar in bars:
                if not isinstance(bar, dict) or "timestamp" not in bar or "close" not in bar:
                    continue
                try:
                    timestamp = datetime.fromisoformat(str(bar["timestamp"]))
                    timestamp = _require_aware_utc(timestamp)
                    price = float(bar["close"])
                except (TypeError, ValueError):
                    continue
                if timestamp > end_timestamp:
                    continue
                prices.append((timestamp.isoformat(), timestamp, price))
            break
        if not prices:
            return BenchmarkReturnSeries.create(
                benchmark_specification_id=self.benchmark.benchmark_id,
                observations=(),
                producer_version=self.producer_version,
            )
        prices.sort(key=lambda item: item[0])
        sorted_prices = [price for _, _, price in prices]
        sorted_timestamps = [timestamp for _, timestamp, _ in prices]
        observations: list[BenchmarkReturnObservation] = []
        for index in range(1, len(sorted_prices)):
            previous_price = sorted_prices[index - 1]
            if previous_price <= 0:
                continue
            benchmark_return = sorted_prices[index] / previous_price - 1
            observations.append(
                BenchmarkReturnObservation.create(
                    timestamp=sorted_timestamps[index],
                    price=sorted_prices[index],
                    benchmark_return=round(benchmark_return, 10),
                    producer_version=self.producer_version,
                )
            )
        return BenchmarkReturnSeries.create(
            benchmark_specification_id=self.benchmark.benchmark_id,
            observations=observations,
            producer_version=self.producer_version,
        )

    def _align_benchmark(
        self, portfolio_observations: Sequence[PortfolioReturnObservation], benchmark_series: BenchmarkReturnSeries | None
    ) -> tuple[Sequence[float], Sequence[float], BenchmarkComparison]:
        if benchmark_series is None:
            return (
                [],
                [],
                BenchmarkComparison(
                    benchmark_specification_id="",
                    benchmark_total_return=MetricValue(
                        status="unavailable", reason="no benchmark series", producer_version=self.producer_version
                    ),
                    portfolio_excess_total_return=MetricValue(
                        status="unavailable", reason="no benchmark series", producer_version=self.producer_version
                    ),
                    tracking_error=MetricValue(status="unavailable", reason="no benchmark series", producer_version=self.producer_version),
                    information_ratio=MetricValue(
                        status="unavailable", reason="no benchmark series", producer_version=self.producer_version
                    ),
                    beta=MetricValue(status="unavailable", reason="no benchmark series", producer_version=self.producer_version),
                    alpha=MetricValue(status="unavailable", reason="no benchmark series", producer_version=self.producer_version),
                    correlation=MetricValue(status="unavailable", reason="no benchmark series", producer_version=self.producer_version),
                    aligned_sample_count=0,
                    portfolio_sample_count=len(portfolio_observations),
                    benchmark_sample_count=0,
                    excluded_intervals=tuple(observation.observation_id for observation in portfolio_observations),
                    benchmark_price_return_semantics="",
                    producer_version=self.producer_version,
                ),
            )
        assert self.benchmark is not None
        if not benchmark_series.observations:
            return (
                [],
                [],
                BenchmarkComparison(
                    benchmark_specification_id=self.benchmark.benchmark_id,
                    benchmark_total_return=MetricValue(
                        status="unavailable", reason="no benchmark observations", producer_version=self.producer_version
                    ),
                    portfolio_excess_total_return=MetricValue(
                        status="unavailable", reason="no benchmark observations", producer_version=self.producer_version
                    ),
                    tracking_error=MetricValue(
                        status="unavailable", reason="no benchmark observations", producer_version=self.producer_version
                    ),
                    information_ratio=MetricValue(
                        status="unavailable", reason="no benchmark observations", producer_version=self.producer_version
                    ),
                    beta=MetricValue(status="unavailable", reason="no benchmark observations", producer_version=self.producer_version),
                    alpha=MetricValue(status="unavailable", reason="no benchmark observations", producer_version=self.producer_version),
                    correlation=MetricValue(
                        status="unavailable", reason="no benchmark observations", producer_version=self.producer_version
                    ),
                    aligned_sample_count=0,
                    portfolio_sample_count=len(portfolio_observations),
                    benchmark_sample_count=0,
                    excluded_intervals=tuple(observation.observation_id for observation in portfolio_observations),
                    benchmark_price_return_semantics=self.benchmark.return_methodology,
                    producer_version=self.producer_version,
                ),
            )
        benchmark_lookup = {
            observation.timestamp: observation.benchmark_return
            for observation in benchmark_series.observations
            if observation.status == "available" and observation.benchmark_return is not None
        }
        aligned_returns: list[float] = []
        aligned_benchmark_returns: list[float] = []
        excluded_intervals: list[str] = []
        for observation in portfolio_observations:
            benchmark_return = benchmark_lookup.get(observation.end_timestamp)
            if benchmark_return is None:
                excluded_intervals.append(observation.observation_id)
                continue
            aligned_returns.append(observation.period_return)
            aligned_benchmark_returns.append(benchmark_return)
        if len(aligned_returns) < 2:
            return (
                aligned_returns,
                aligned_benchmark_returns,
                BenchmarkComparison(
                    benchmark_specification_id=self.benchmark.benchmark_id,
                    benchmark_total_return=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    portfolio_excess_total_return=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    tracking_error=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    information_ratio=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    beta=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    alpha=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    correlation=MetricValue(
                        status="unavailable", reason="insufficient aligned observations", producer_version=self.producer_version
                    ),
                    aligned_sample_count=len(aligned_returns),
                    portfolio_sample_count=len(portfolio_observations),
                    benchmark_sample_count=len(benchmark_series.observations),
                    excluded_intervals=tuple(excluded_intervals),
                    benchmark_price_return_semantics=self.benchmark.return_methodology,
                    producer_version=self.producer_version,
                ),
            )
        portfolio_mean = sum(aligned_returns) / len(aligned_returns)
        benchmark_mean = sum(aligned_benchmark_returns) / len(aligned_benchmark_returns)
        benchmark_variance = self._sample_std(aligned_benchmark_returns, benchmark_mean) ** 2
        covariance = sum(
            (portfolio_return - portfolio_mean) * (benchmark_return - benchmark_mean)
            for portfolio_return, benchmark_return in zip(aligned_returns, aligned_benchmark_returns)
        ) / (len(aligned_returns) - 1)
        beta = covariance / benchmark_variance if benchmark_variance > 0 else None
        alpha = None
        if beta is not None:
            alpha = (
                portfolio_mean
                - self.methodology.periodic_risk_free_rate
                - beta * (benchmark_mean - self.methodology.periodic_risk_free_rate)
            )
            alpha = round(alpha * self.methodology.periods_per_year, 10) if math.isfinite(alpha) else None
        excess_returns = [
            portfolio_return - benchmark_return for portfolio_return, benchmark_return in zip(aligned_returns, aligned_benchmark_returns)
        ]
        excess_mean = sum(excess_returns) / len(excess_returns)
        tracking_error = self._sample_std(excess_returns, excess_mean) * math.sqrt(self.methodology.periods_per_year)
        information_ratio = None
        if tracking_error > 0:
            information_ratio = excess_mean * math.sqrt(self.methodology.periods_per_year) / tracking_error
            information_ratio = round(information_ratio, 10) if math.isfinite(information_ratio) else None
        correlation = None
        if benchmark_variance > 0:
            portfolio_variance = self._sample_std(aligned_returns, portfolio_mean) ** 2
            denominator = math.sqrt(portfolio_variance * benchmark_variance)
            if denominator > 0:
                correlation = covariance / denominator
                correlation = round(max(-1.0, min(1.0, correlation)), 10)
        return (
            aligned_returns,
            aligned_benchmark_returns,
            BenchmarkComparison(
                benchmark_specification_id=self.benchmark.benchmark_id,
                benchmark_total_return=MetricValue(
                    value=round(math.prod(1 + value for value in aligned_benchmark_returns) - 1, 10),
                    status="available",
                    producer_version=self.producer_version,
                ),
                portfolio_excess_total_return=MetricValue(
                    value=round(math.prod(1 + value for value in excess_returns) - 1, 10),
                    status="available",
                    producer_version=self.producer_version,
                ),
                tracking_error=MetricValue(value=round(tracking_error, 10), status="available", producer_version=self.producer_version),
                information_ratio=MetricValue(
                    value=information_ratio,
                    status="available" if information_ratio is not None else "unavailable",
                    reason=None if information_ratio is not None else "zero tracking error",
                    producer_version=self.producer_version,
                ),
                beta=MetricValue(
                    value=round(beta, 10) if beta is not None else None,
                    status="available" if beta is not None else "unavailable",
                    reason=None if beta is not None else "zero benchmark variance",
                    producer_version=self.producer_version,
                ),
                alpha=MetricValue(
                    value=alpha,
                    status="available" if alpha is not None else "unavailable",
                    reason=None if alpha is not None else "invalid alpha inputs",
                    producer_version=self.producer_version,
                ),
                correlation=MetricValue(
                    value=correlation,
                    status="available" if correlation is not None else "unavailable",
                    reason=None if correlation is not None else "zero variance",
                    producer_version=self.producer_version,
                ),
                aligned_sample_count=len(aligned_returns),
                portfolio_sample_count=len(portfolio_observations),
                benchmark_sample_count=len(benchmark_series.observations),
                excluded_intervals=tuple(excluded_intervals),
                benchmark_price_return_semantics=self.benchmark.return_methodology,
                producer_version=self.producer_version,
            ),
        )

    def evaluate(self) -> HistoricalPerformanceEvaluation:
        self._validate_specification_linkage()
        if self.methodology.periods_per_year <= 0:
            raise InvalidMethodologyError("periods_per_year must be positive")
        snapshots = self._load_ordered_snapshots()
        if not snapshots:
            raise InsufficientSampleError("no portfolio snapshots available")
        max_timestamp = max(snapshot.simulated_at for snapshot in snapshots)
        self._reject_future_evaluation_artifacts(max_timestamp)
        return_series = self.build_return_series(snapshots)
        observations = list(return_series.observations)
        performance = self._performance_metrics(return_series, snapshots)
        risk = self._risk_metrics(return_series, observations)
        benchmark_series = self._load_benchmark_series(max_timestamp)
        benchmark_comparison = None
        if self.benchmark is not None and benchmark_series is not None:
            _, _, benchmark_comparison = self._align_benchmark(observations, benchmark_series)
        _adjustments, corporate_actions, entitlements, assessments = self._load_adjustments()
        transaction_cost_aggregate = self._aggregate_transaction_costs(assessments)
        corporate_action_aggregate = self._aggregate_corporate_actions(corporate_actions, entitlements)
        return HistoricalPerformanceEvaluation.create(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=self.specification.run_id,
            methodology_id=self.methodology.methodology_id,
            first_snapshot_id=snapshots[0].portfolio_snapshot_id,
            last_snapshot_id=snapshots[-1].portfolio_snapshot_id,
            snapshot_count=len(snapshots),
            valued_snapshot_count=return_series.valued_snapshot_count,
            return_observation_count=return_series.return_observation_count,
            performance_metrics=performance,
            risk_metrics=risk,
            transaction_cost_aggregate=transaction_cost_aggregate,
            corporate_action_aggregate=corporate_action_aggregate,
            benchmark_comparison=benchmark_comparison,
            input_artifact_ids=(),
            logical_as_of=max_timestamp,
            producer_version=self.producer_version,
        )
