"""Offline deterministic portfolio Risk Officer orchestration."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from typing import ClassVar
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskCategory, RiskConstraintSet, RiskDecision, RiskMetric, RiskSeverity
from app.services.risk.concentration import ConcentrationRiskService
from app.services.risk.config import RiskOfficerConfig
from app.services.risk.correlation import CorrelationRiskService
from app.services.risk.data_quality import RiskDataQualityService
from app.services.risk.decision import RiskDecisionService
from app.services.risk.drawdown import DrawdownRiskService
from app.services.risk.exposure import ExposureRiskService
from app.services.risk.limits import RiskLimitEvaluator
from app.services.risk.liquidity import LiquidityRiskService
from app.services.risk.provenance import RiskProvenanceService
from app.services.risk.stress import StressTestingService
from app.services.risk.trace import build_risk_trace
from app.services.risk.turnover import TurnoverReviewService
from app.services.risk.validation import RiskInputValidationService
from app.services.risk.var import VaRCVaRService
from app.services.risk.volatility import VolatilityRiskService


class RiskOfficerService:
    CALCULATOR_VERSIONS: ClassVar[dict[str, str]] = {
        "concentration": "1",
        "correlation": "1",
        "drawdown": "2",
        "exposure": "1",
        "liquidity": "1",
        "turnover": "1",
        "var_cvar": "1",
        "volatility": "1",
    }

    def __init__(self, config: RiskOfficerConfig | None = None) -> None:
        self.config = config or RiskOfficerConfig()
        self.provenance = RiskProvenanceService()
        self.validation = RiskInputValidationService()

    def health(self) -> dict[str, object]:
        return {"status": "ok", "offline": True, "research_only": True, "checked_at": datetime.now(UTC).isoformat()}

    def metadata(self) -> dict[str, object]:
        return {
            "component": "risk-officer",
            "algorithm_version": self.config.algorithm_version,
            "deterministic": True,
            "offline": True,
            "research_only": True,
            "suitable_for_live_trading": False,
            "decision_ready": False,
            "output": "RiskAssessment and RiskDecision",
        }

    def assess(
        self,
        proposal: PortfolioProposal,
        historical_bars: list[HistoricalBarsResult] | None = None,
        liquidity_inputs: dict[str, dict[str, float]] | None = None,
        constraints: RiskConstraintSet | None = None,
    ) -> RiskAssessment:
        history, liquidity = historical_bars or [], liquidity_inputs or {}
        rules = constraints or self.config.constraints
        self.validation.validate(proposal, history)
        self.validation.validate_liquidity(liquidity)
        source = self.provenance.fingerprint({"proposal": proposal, "history": history, "liquidity": liquidity, "constraints": rules})
        concentration = ConcentrationRiskService().calculate(proposal)
        exposure = ExposureRiskService().calculate(proposal)
        volatility = VolatilityRiskService().calculate(proposal, history, rules.min_sample_size)
        correlation = CorrelationRiskService().calculate(
            proposal, history, rules.min_sample_size, self.config.correlation_cluster_threshold
        )
        drawdown = DrawdownRiskService().calculate(history)
        liquidity_result = LiquidityRiskService().calculate(proposal, liquidity, self.config.illiquid_dollar_volume)
        turnover, turnover_warnings = TurnoverReviewService().calculate(proposal, rules.max_turnover)
        quality = RiskDataQualityService().calculate(proposal, history, liquidity, rules.min_sample_size, rules.stale_data_tolerance)
        portfolio_returns = self._portfolio_returns(proposal, history)
        var95 = VaRCVaRService.calculate(portfolio_returns, 0.95, rules.min_sample_size)
        var99 = VaRCVaRService.calculate(portfolio_returns, 0.99, rules.min_sample_size)
        values: list[tuple[RiskCategory, str, float | None, str]] = [
            (RiskCategory.CONCENTRATION, "max_single_position_weight", concentration["max_single_position_weight"], "weight"),
            (RiskCategory.CONCENTRATION, "hhi", concentration["hhi"], "ratio"),
            (RiskCategory.DIVERSIFICATION, "effective_positions", concentration["effective_positions"], "count"),
            (RiskCategory.SECTOR, "max_sector_weight", concentration["max_sector_weight"], "weight"),
            (RiskCategory.INDUSTRY, "max_industry_weight", concentration["max_industry_weight"], "weight"),
            (RiskCategory.ASSET_CLASS, "max_asset_class_weight", concentration["max_asset_class_weight"], "weight"),
            (RiskCategory.VOLATILITY, "portfolio_volatility", volatility["portfolio_volatility"], "annualized"),
            (RiskCategory.CORRELATION, "max_pairwise_correlation", correlation["max_pairwise_correlation"], "correlation"),
            (RiskCategory.CORRELATION, "weighted_average_correlation", correlation["weighted_average_correlation"], "correlation"),
            (RiskCategory.DRAWDOWN, "max_drawdown", drawdown["max_drawdown"], "loss"),
            (RiskCategory.VAR, "var_95", var95["var"], "loss"),
            (RiskCategory.CVAR, "cvar_95", var95["cvar"], "loss"),
            (RiskCategory.VAR, "var_99", var99["var"], "loss"),
            (RiskCategory.CVAR, "cvar_99", var99["cvar"], "loss"),
            (RiskCategory.LIQUIDITY, "illiquid_weight", liquidity_result["illiquid_weight"], "weight"),
            (
                RiskCategory.LIQUIDITY,
                "liquidity_score",
                liquidity_result["liquidity_score"] if liquidity_result["scores"] else None,
                "score",
            ),
            (RiskCategory.DATA_QUALITY, "unknown_metadata_weight", concentration["unknown_classification_weight"], "weight"),
            (RiskCategory.GROSS_EXPOSURE, "gross_exposure", exposure["gross_exposure"], "weight"),
            (RiskCategory.NET_EXPOSURE, "net_exposure", exposure["net_exposure"], "weight"),
            (RiskCategory.SHORT_EXPOSURE, "short_exposure", exposure["short_exposure"], "weight"),
            (RiskCategory.CASH, "cash_weight", exposure["cash_weight"], "weight"),
            (RiskCategory.LEVERAGE, "implied_leverage", exposure["implied_leverage"], "multiple"),
            (RiskCategory.TURNOVER, "turnover", turnover, "weight"),
            (RiskCategory.DATA_QUALITY, "data_quality_score", quality["score"], "ratio"),
        ]
        metrics = tuple(self._metric(category, name, value, units, proposal.as_of, source) for category, name, value, units in values)
        breaches = RiskLimitEvaluator().evaluate(metrics, rules, source)
        stress = StressTestingService().run(proposal, liquidity_result["illiquid_weight"])
        limitations = tuple(quality["issues"]) if not quality["sufficient"] else ()
        warnings = tuple(sorted({*turnover_warnings, *liquidity_result["warnings"], *quality["issues"]}))
        score = min(
            100.0,
            sum(
                {RiskSeverity.INFO: 1, RiskSeverity.LOW: 5, RiskSeverity.MODERATE: 15, RiskSeverity.HIGH: 25, RiskSeverity.CRITICAL: 50}[
                    item.severity
                ]
                for item in breaches
            ),
        )
        highest = max((item.severity for item in breaches), key=lambda item: list(RiskSeverity).index(item), default=RiskSeverity.INFO)
        assessment_id = str(uuid5(NAMESPACE_URL, f"risk-assessment:{proposal.proposal_id}:{source}"))
        trace = build_risk_trace(
            proposal.proposal_id,
            assessment_id,
            tuple(item.name for item in metrics),
            tuple(item.breach_id for item in breaches),
            proposal.as_of,
        )
        return RiskAssessment(
            assessment_id=assessment_id,
            proposal_id=proposal.proposal_id,
            portfolio_id=proposal.portfolio_id,
            as_of=proposal.as_of,
            metrics=metrics,
            breaches=breaches,
            stress_results=stress,
            overall_risk_score=score,
            highest_severity=highest,
            data_quality_score=quality["score"],
            warnings=warnings,
            limitations=limitations,
            provenance=self._assessment_provenance(proposal, history, liquidity, rules, source),
            trace=trace,
        )

    def decide(self, assessment: RiskAssessment, constraints: RiskConstraintSet | None = None) -> RiskDecision:
        rules = constraints or self.config.constraints
        return RiskDecisionService().decide(assessment, rules.catastrophic_stress_loss, rules.min_data_quality_score)

    def review(
        self,
        proposal: PortfolioProposal,
        historical_bars: list[HistoricalBarsResult] | None = None,
        liquidity_inputs: dict[str, dict[str, float]] | None = None,
        constraints: RiskConstraintSet | None = None,
    ) -> tuple[RiskAssessment, RiskDecision]:
        assessment = self.assess(proposal, historical_bars, liquidity_inputs, constraints)
        return assessment, self.decide(assessment, constraints)

    @staticmethod
    def _metric(category: RiskCategory, name: str, value: float | None, units: str, as_of: datetime, source: str) -> RiskMetric:
        valid = value is not None
        number = value if value is not None else 0.0
        return RiskMetric(
            metric_id=str(uuid5(NAMESPACE_URL, f"risk-metric:{source}:{name}")),
            category=category,
            name=name,
            value=number,
            units=units,
            as_of=as_of,
            source_fingerprint=source,
            valid=valid,
            warnings=() if valid else ("Required input unavailable",),
        )

    @staticmethod
    def _portfolio_returns(proposal: PortfolioProposal, history: list[HistoricalBarsResult]) -> list[float]:
        series = {str(item.symbol): [current.close / previous.close - 1 for previous, current in pairwise(item.bars)] for item in history}
        length = min((len(series.get(item.symbol, [])) for item in proposal.positions), default=0)
        if not length:
            return []
        return [sum(item.proposed_weight * series[item.symbol][-length + index] for item in proposal.positions) for index in range(length)]

    def _assessment_provenance(
        self,
        proposal: PortfolioProposal,
        history: list[HistoricalBarsResult],
        liquidity: dict[str, dict[str, float]],
        rules: RiskConstraintSet,
        source: str,
    ) -> dict[str, object]:
        market_data_fingerprints = {
            str(item.symbol): self.provenance.fingerprint(item) for item in sorted(history, key=lambda value: str(value.symbol))
        }
        sample_windows = {
            str(item.symbol): {
                "start": item.bars[0].timestamp.isoformat() if item.bars else None,
                "end": item.bars[-1].timestamp.isoformat() if item.bars else None,
                "sample_count": len(item.bars),
            }
            for item in sorted(history, key=lambda value: str(value.symbol))
        }
        scenarios = StressTestingService().scenarios()
        return {
            "input_fingerprint": source,
            "algorithm_version": self.config.algorithm_version,
            "proposal_id": proposal.proposal_id,
            "proposal_algorithm_version": proposal.algorithm_version,
            "proposal_fingerprint": self.provenance.fingerprint(proposal),
            "risk_policy_fingerprint": self.provenance.fingerprint(rules),
            "market_data_fingerprints": market_data_fingerprints,
            "liquidity_input_fingerprints": {
                symbol: self.provenance.fingerprint(observation) for symbol, observation in sorted(liquidity.items())
            },
            "historical_sample_windows": sample_windows,
            "risk_service_version": self.config.algorithm_version,
            "calculator_versions": self.CALCULATOR_VERSIONS,
            "stress_scenario_version": StressTestingService.VERSION,
            "stress_scenarios": tuple({"scenario_id": item.scenario_id, "version": item.version} for item in scenarios),
            "feature_snapshot_ids": (),
            "git_commit": self.provenance.git_commit(),
        }
