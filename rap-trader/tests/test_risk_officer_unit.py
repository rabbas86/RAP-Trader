"""Phase 11 domain, service, and policy unit tests.

These tests complement tests/test_risk_officer.py by exercising each risk
category and decision path at the unit level, mirroring the deterministic,
research-only, no-lookahead, no-network, no-execution conventions of the
repository. They do not invoke brokers, execution, or any external service.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.domain.models.portfolio import PortfolioProposal, PortfolioProposalPosition
from app.domain.models.risk import (
    RiskCategory,
    RiskConstraintSet,
    RiskDecisionType,
    RiskMetric,
    RiskModificationType,
    RiskSeverity,
    StressScenario,
)
from app.services.risk.concentration import ConcentrationRiskService
from app.services.risk.config import RiskOfficerConfig
from app.services.risk.correlation import CorrelationRiskService
from app.services.risk.data_quality import RiskDataQualityService
from app.services.risk.decision import RiskDecisionService
from app.services.risk.drawdown import DrawdownRiskService
from app.services.risk.exposure import ExposureRiskService
from app.services.risk.limits import RiskLimitEvaluator
from app.services.risk.liquidity import LiquidityRiskService
from app.services.risk.service import RiskOfficerService
from app.services.risk.stress import StressTestingService
from app.services.risk.turnover import TurnoverReviewService
from app.services.risk.var import VaRCVaRService
from app.services.risk.volatility import VolatilityRiskService

AS_OF = datetime(2025, 1, 10, tzinfo=UTC)


def _position(
    symbol: str,
    weight: float,
    sector: str = "technology",
    industry: str = "software",
    asset_class: str = "equity",
) -> PortfolioProposalPosition:
    return PortfolioProposalPosition(
        symbol=symbol,
        current_weight=weight,
        proposed_weight=weight,
        conviction=0.0,
        sector=sector,
        industry=industry,
        asset_class=asset_class,
    )


def _proposal(
    *weights: tuple[str, float, str, str],
    turnover: float = 0.2,
    cash: float | None = None,
) -> PortfolioProposal:
    positions = tuple(_position(s, w, sec, ind) for s, w, sec, ind in weights)
    net = sum(w for _, w, _, _ in weights)
    gross = sum(abs(w) for _, w, _, _ in weights)
    if cash is None:
        cash = max(0.0, 1.0 - net) if net > 0 else 1.0
    return PortfolioProposal(
        proposal_id="proposal-11",
        portfolio_id="portfolio-11",
        as_of=AS_OF,
        positions=positions,
        cash_weight=cash,
        gross_exposure=gross,
        net_exposure=net,
        turnover=turnover,
        input_fingerprint="fingerprint",
        config_fingerprint="config",
        constraint_fingerprint="constraint",
        algorithm_version="phase-10",
        trace=_trace(),
    )


def _trace():
    from app.domain.models.analyst import AnalysisTrace, TraceNode

    return AnalysisTrace(
        trace_id="proposal-trace",
        nodes=[TraceNode(node_id="source", node_type="fixture", created_at=AS_OF)],
        edges=[],
        created_at=AS_OF,
    )


def _bars(symbol: str, closes: list[float], end: datetime = AS_OF) -> HistoricalBarsResult:
    values = [
        OHLCVBar(
            timestamp=end - timedelta(days=len(closes) - index - 1),
            open=value,
            high=value,
            low=value,
            close=value,
            volume=1000,
        )
        for index, value in enumerate(closes)
    ]
    return HistoricalBarsResult(
        symbol=Symbol(symbol),
        timeframe="1d",
        bars=values,
        provider="offline",
        requested_start=values[0].timestamp,
        requested_end=end,
        actual_start=values[0].timestamp,
        actual_end=values[-1].timestamp,
        adjustment="raw",
        session="regular",
        retrieved_at=end,
    )


def _service(constraints: RiskConstraintSet | None = None) -> RiskOfficerService:
    return RiskOfficerService(config=RiskOfficerConfig(constraints=constraints or RiskConstraintSet()))


# ---------------------------------------------------------------------------
# Domain: enums, frozen models, finite values, safety flags
# ---------------------------------------------------------------------------


def test_risk_severity_and_category_and_decision_enums_are_str() -> None:
    assert {item.value for item in RiskSeverity} == {"info", "low", "moderate", "high", "critical"}
    assert RiskSeverity.CRITICAL.value == "critical"
    assert {item.value for item in RiskDecisionType} == {"approve", "reject", "require_modification", "insufficient_data"}
    cats = {item.value for item in RiskCategory}
    for name in ("concentration", "diversification", "volatility", "drawdown", "var", "cvar", "stress"):
        assert name in cats
    assert RiskCategory.VAR == "var"


def test_risk_models_are_frozen_finite_and_research_only() -> None:
    model = RiskConstraintSet()
    assert (model.research_only, model.suitable_for_live_trading, model.decision_ready) == (True, False, False)
    with pytest.raises(ValidationError):
        model.max_single_position_weight = 0.5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RiskMetric(
            metric_id="m1",
            category=RiskCategory.VOLATILITY,
            name="vol",
            value=math.nan,
            units="ratio",
            as_of=AS_OF,
            source_fingerprint="fp",
        )
    with pytest.raises(ValidationError):
        RiskMetric(
            metric_id="m2",
            category=RiskCategory.VOLATILITY,
            name="vol",
            value=math.inf,
            units="ratio",
            as_of=AS_OF,
            source_fingerprint="fp",
        )


def test_stress_scenario_rejects_non_finite_shocks() -> None:
    with pytest.raises(ValidationError):
        StressScenario(scenario_id="x", name="x", description="x", shocks={"market": math.nan}, source="s")
    valid = StressScenario(scenario_id="x", name="x", description="x", shocks={"market": -0.1}, source="s")
    assert valid.deterministic is True


def test_constraint_set_rejects_cvar_below_var() -> None:
    with pytest.raises(ValidationError):
        RiskConstraintSet(max_var_95=0.05, max_cvar_95=0.03)


def test_constraint_set_rejects_future_as_of_in_metric() -> None:
    with pytest.raises(ValidationError):
        RiskMetric(
            metric_id="m1",
            category=RiskCategory.VOLATILITY,
            name="vol",
            value=0.1,
            units="ratio",
            as_of=datetime(2099, 1, 1, tzinfo=UTC),
            source_fingerprint="fp",
        )


# ---------------------------------------------------------------------------
# Concentration
# ---------------------------------------------------------------------------


def test_concentration_single_position_breach_top3_top5() -> None:
    proposal = _proposal(("AAA", 0.9, "tech", "software"), ("BBB", 0.05, "tech", "hardware"))
    result = ConcentrationRiskService().calculate(proposal)
    assert result["max_single_position_weight"] == pytest.approx(0.9)
    assert result["top_3_weight"] == pytest.approx(0.95)
    assert result["max_sector_weight"] == pytest.approx(0.95)
    assert result["unknown_classification_weight"] == pytest.approx(0.0)


def test_concentration_hhi_and_effective_positions() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = ConcentrationRiskService().calculate(proposal)
    assert result["hhi"] == pytest.approx(0.5)
    assert result["effective_positions"] == pytest.approx(2.0)


def test_concentration_unknown_classification_concentration() -> None:
    proposal = _proposal(("AAA", 1.0, None, None))  # type: ignore[arg-type]
    result = ConcentrationRiskService().calculate(proposal)
    assert result["unknown_classification_weight"] == pytest.approx(1.0)
    assert result["max_sector_weight"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------


def test_exposure_long_short_cash_and_leverage() -> None:
    proposal = _proposal(("AAA", 0.7, "tech", "software"), ("BBB", -0.1, "tech", "hardware"))
    result = ExposureRiskService().calculate(proposal)
    assert result["gross_exposure"] == pytest.approx(0.8)
    assert result["net_exposure"] == pytest.approx(0.6)
    assert result["long_exposure"] == pytest.approx(0.7)
    assert result["short_exposure"] == pytest.approx(0.1)
    assert result["cash_weight"] == pytest.approx(0.4)  # 1 - (0.7 + -0.1) = 0.4
    assert result["implied_leverage"] == pytest.approx(0.8)  # shorts present -> gross


def test_exposure_no_shorts_implied_leverage_floors_to_one() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.4, "tech", "hardware"))
    result = ExposureRiskService().calculate(proposal)
    assert result["implied_leverage"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def test_volatility_deterministic_and_insufficient_history() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i * 0.5 for i in range(31)])]
    base = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = VolatilityRiskService().calculate(base, history, 20)
    assert result["sample_size"] == 30
    assert result["portfolio_volatility"] is not None
    assert result["missing"] == []
    assert VolatilityRiskService().calculate(base, history, 20) == result


def test_volatility_returns_none_when_missing_history() -> None:
    result = VolatilityRiskService().calculate(_proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware")), [], 20)
    assert result["portfolio_volatility"] is None
    assert sorted(result["missing"]) == ["AAA", "BBB"]


def test_volatility_insufficient_samples() -> None:
    history = [_bars("AAA", [100, 101, 102])]
    result = VolatilityRiskService().calculate(_proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware")), history, 20)
    assert result["sample_size"] == 0
    assert result["portfolio_volatility"] is None


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


def test_correlation_identical_series_high_and_missing() -> None:
    history = [_bars("AAA", [100, 102, 104, 106]), _bars("BBB", [200, 204, 208, 212])]
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = CorrelationRiskService().calculate(proposal, history, 3)
    assert result["pair_count"] == 1
    assert result["max_pairwise_correlation"] == pytest.approx(1.0)
    assert result["high_correlation_clusters"] == (("AAA", "BBB"),)


def test_correlation_low_and_missing_data() -> None:
    history = [_bars("AAA", [100, 110, 90, 105, 95]), _bars("BBB", [100, 90, 110, 95, 105])]
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = CorrelationRiskService().calculate(proposal, history, 4)
    assert result["max_pairwise_correlation"] is not None
    assert -1.0 <= result["max_pairwise_correlation"] <= 1.0


def test_correlation_insufficient_samples_yields_none() -> None:
    history = [_bars("AAA", [100, 110]), _bars("BBB", [100, 90])]
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = CorrelationRiskService().calculate(proposal, history, 5)
    assert result["max_pairwise_correlation"] is None
    assert result["weighted_average_correlation"] is None


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------


def test_drawdown_maximum_and_recent() -> None:
    history = [_bars("AAA", [100, 120, 90, 100, 121])]
    result = DrawdownRiskService().calculate(history)
    assert result["max_drawdown"] == pytest.approx(0.25)
    assert result["recent_drawdown"]["AAA"] == pytest.approx(0.0)  # ends at peak


def test_drawdown_no_loss_is_zero() -> None:
    history = [_bars("AAA", [100, 110, 120, 130])]
    result = DrawdownRiskService().calculate(history)
    assert result["max_drawdown"] == pytest.approx(0.0)


def test_drawdown_recovery_duration() -> None:
    # peak at 0 (100), trough at 1 (90), recovers to 105 at bar 3
    history = [_bars("AAA", [100, 90, 95, 105])]
    result = DrawdownRiskService().calculate(history)
    assert result["recovery_duration"]["AAA"] is not None


def test_drawdown_empty_one_bar_and_no_history_are_unavailable() -> None:
    one = _bars("AAA", [100])
    empty = HistoricalBarsResult.model_construct(**{**one.__dict__, "bars": []})
    empty_result = DrawdownRiskService().calculate([empty])
    one_result = DrawdownRiskService().calculate([one])
    no_history_result = DrawdownRiskService().calculate([])
    assert not empty_result["valid"] and empty_result["max_drawdown"] is None and empty_result["limitations"]
    assert not one_result["valid"] and one_result["max_drawdown"] is None and one_result["limitations"]
    assert not no_history_result["valid"] and no_history_result["max_drawdown"] is None


# ---------------------------------------------------------------------------
# VaR / CVaR
# ---------------------------------------------------------------------------


def test_var_cvar_deterministic_historical_95_and_99() -> None:
    losses = [-0.10, -0.05, -0.01, -0.02, 0.03] * 10
    result_95 = VaRCVaRService.calculate(losses, 0.95, 20)
    assert result_95["valid"]
    assert result_95["var"] == pytest.approx(0.10)
    assert result_95["cvar"] > 0
    result_99 = VaRCVaRService.calculate(losses, 0.99, 20)
    assert result_99["valid"]
    assert result_99["var"] >= result_95["var"]


def test_var_cvar_insufficient_samples_invalid() -> None:
    result = VaRCVaRService.calculate([0.1], 0.95, 20)
    assert not result["valid"]
    assert result["var"] is None


def test_var_sign_convention_positive_is_loss_magnitude() -> None:
    # All-positive returns => no losses => VaR is 0 (max(0.0, -cutoff))
    result = VaRCVaRService.calculate([0.01, 0.02, 0.03, 0.04, 0.05] * 10, 0.95, 20)
    assert result["valid"]
    assert result["var"] == pytest.approx(0.0)
    assert result["cvar"] == pytest.approx(0.0)


def test_var_cvar_deterministic_equality() -> None:
    losses = [-0.10, -0.05, 0.01, 0.02] * 25
    first = VaRCVaRService.calculate(losses, 0.95, 20)
    second = VaRCVaRService.calculate(losses, 0.95, 20)
    assert first == second


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------


def test_liquidity_liquid_scores() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    observations = {"AAA": {"average_dollar_volume": 5_000_000.0}, "BBB": {"average_dollar_volume": 2_000_000.0}}
    result = LiquidityRiskService().calculate(proposal, observations, 1_000_000)
    assert result["illiquid_weight"] == pytest.approx(0.0)
    assert not result["warnings"]
    assert result["portfolio_value_known"] is False


def test_liquidity_illiquid_and_missing_data() -> None:
    proposal = _proposal(("AAA", 0.4, "tech", "software"), ("BBB", 0.4, "tech", "hardware"))
    observations = {"AAA": {"average_dollar_volume": 100.0}}
    result = LiquidityRiskService().calculate(proposal, observations, 1_000_000)
    assert result["illiquid_weight"] == pytest.approx(0.4)
    assert result["warnings"]


def test_liquidity_no_fabricated_depth_when_no_volume() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    result = LiquidityRiskService().calculate(proposal, {}, 1_000_000)
    assert result["warnings"]
    assert result["liquidity_score"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Turnover
# ---------------------------------------------------------------------------


def test_turnover_within_limit() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"), turnover=0.3)
    turnover, warnings = TurnoverReviewService.calculate(proposal, 0.5)
    assert turnover == pytest.approx(0.3)
    assert not warnings


def test_turnover_breach_warning() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"), turnover=0.8)
    turnover, warnings = TurnoverReviewService.calculate(proposal, 0.5)
    assert turnover == pytest.approx(0.8)
    assert warnings


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------


def test_data_quality_stale_missing_unknown_classifications() -> None:
    stale_history = [_bars("AAA", list(range(1, 25)), end=AS_OF - timedelta(days=30))]
    proposal = _proposal(("AAA", 0.5, None, None), ("BBB", 0.5, "tech", "software"))  # type: ignore[arg-type]
    result = RiskDataQualityService().calculate(proposal, stale_history, {}, 20, timedelta(days=7))
    assert result["score"] < 1.0
    assert not result["sufficient"]
    assert any("stale" in issue for issue in result["issues"])
    assert any("missing market" in issue for issue in result["issues"])
    assert any("BBB" in issue for issue in result["issues"])
    assert any("unknown" in issue for issue in result["issues"])


def test_data_quality_sufficient_when_all_present() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)])]
    proposal = _proposal(("AAA", 1.0, "tech", "software"))
    result = RiskDataQualityService().calculate(proposal, history, {"AAA": {"average_dollar_volume": 1_000_000}}, 20, timedelta(days=7))
    assert result["sufficient"]


# ---------------------------------------------------------------------------
# Stress testing
# ---------------------------------------------------------------------------


def test_stress_scenarios_count_and_non_forecast_assumptions() -> None:
    scenarios = StressTestingService().scenarios()
    names = {item.scenario_id for item in scenarios}
    assert {
        "market_down_10",
        "market_down_20",
        "top_position_down_25",
        "sector_down_20",
        "approximate_volatility_spike",
        "correlation_to_one",
        "credit_spreads_widen",
        "rates_up_100bps",
        "liquidity_haircut_50",
        "combined_risk_off",
    }.issubset(names)
    assert len(scenarios) == 10
    assert all(item.deterministic for item in scenarios)


def test_stress_impacts_non_positive_research_only() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    results = StressTestingService().run(proposal, 0.1)
    assert len(results) == 10
    assert all(item.estimated_portfolio_impact <= 0 for item in results)
    impacts = {item.scenario_id: item.estimated_portfolio_impact for item in results}
    assert impacts["combined_risk_off"] <= impacts["market_down_10"]


def test_stress_deterministic() -> None:
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"))
    first = StressTestingService().run(proposal, 0.1)
    second = StressTestingService().run(proposal, 0.1)
    assert first == second


# ---------------------------------------------------------------------------
# Limits / breaches
# ---------------------------------------------------------------------------


def _within_bounds_constraints() -> RiskConstraintSet:
    return RiskConstraintSet(
        max_single_position_weight=0.5,
        max_gross_exposure=2.0,
        max_sector_weight=1.0,
        max_industry_weight=1.0,
        max_asset_class_weight=1.0,
        max_short_exposure=1.0,
        max_net_exposure=2.0,
        max_turnover=2.0,
        max_drawdown=1.0,
        max_var_95=1.0,
        max_cvar_95=1.0,
        max_hhi=1.0,
        max_pairwise_correlation=1.0,
        maximum_average_correlation=1.0,
        max_var_99=1.0,
        max_cvar_99=1.0,
        minimum_liquidity_score=0.0,
        maximum_unknown_metadata_weight=1.0,
        min_cash_weight=0.0,
        min_data_quality_score=0.0,
        catastrophic_stress_loss=1.0,
        min_effective_positions=0.01,
        max_illiquid_weight=1.0,
        max_portfolio_volatility=1.0,
    )


def test_limits_hard_breach_single_position_and_gross() -> None:
    constraints = RiskConstraintSet(max_single_position_weight=0.1, max_gross_exposure=0.9)
    proposal = _proposal(("AAA", 0.9, "tech", "software"), ("BBB", 0.05, "tech", "hardware"))
    evaluation = _service(constraints).assess(proposal, [], {})
    breach_categories = {item.category for item in evaluation.breaches}
    assert RiskCategory.CONCENTRATION in breach_categories
    assert RiskCategory.GROSS_EXPOSURE in breach_categories
    assert any(item.hard_limit for item in evaluation.breaches)
    assert evaluation.highest_severity == RiskSeverity.CRITICAL


def test_limits_evaluator_soft_breach_sector() -> None:
    proposal = _proposal(("AAA", 0.3, "tech", "software"), ("BBB", 0.3, "tech", "hardware"))
    evaluation = _service(RiskConstraintSet(max_sector_weight=0.5)).assess(proposal, [], {})
    assert any(item.category == RiskCategory.SECTOR for item in evaluation.breaches)
    assert any(item.severity == RiskSeverity.HIGH for item in evaluation.breaches)


def test_limits_no_breaches_when_within_bounds() -> None:
    evaluation = _service(_within_bounds_constraints()).assess(
        _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.3, "tech", "hardware")), [], {}
    )
    assert evaluation.breaches == ()


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------


def test_decision_approve_when_no_breaches() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.3, "finance", "banking"))
    _, decision = _service(_within_bounds_constraints()).review(proposal, history, liquidity)
    assert decision.decision == RiskDecisionType.APPROVE


def test_decision_reject_on_critical_hard_breach() -> None:
    constraints = RiskConstraintSet(max_single_position_weight=0.1)
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.9, "tech", "software"), ("BBB", 0.05, "tech", "hardware"))
    service = _service(constraints)
    assessment = service.assess(proposal, history, liquidity)
    decision = service.decide(assessment, constraints)
    assert decision.decision == RiskDecisionType.REJECT
    assert decision.blocking_breaches


def test_decision_insufficient_data_without_history() -> None:
    _, decision = _service().review(_proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware")), [], {})
    assert decision.decision == RiskDecisionType.INSUFFICIENT_DATA


def test_decision_require_modification_on_moderate_breach() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.5, "tech", "software"), ("BBB", 0.5, "tech", "hardware"), turnover=0.5)
    # Relax every bound except turnover so the only breach is the moderate turnover breach.
    constraints = RiskConstraintSet(
        max_single_position_weight=1.0,
        max_gross_exposure=2.0,
        max_sector_weight=1.0,
        max_industry_weight=1.0,
        max_asset_class_weight=1.0,
        max_short_exposure=2.0,
        max_net_exposure=2.0,
        max_turnover=0.1,
        max_drawdown=1.0,
        max_var_95=1.0,
        max_cvar_95=1.0,
        max_hhi=1.0,
        max_pairwise_correlation=1.0,
        min_cash_weight=0.0,
        min_data_quality_score=0.0,
        catastrophic_stress_loss=1.0,
        min_effective_positions=0.01,
        max_illiquid_weight=1.0,
        max_portfolio_volatility=1.0,
    )
    _, decision = _service(constraints).review(proposal, history, liquidity)
    assert decision.decision == RiskDecisionType.REQUIRE_MODIFICATION
    assert decision.required_modifications


# ---------------------------------------------------------------------------
# Modification recommendations
# ---------------------------------------------------------------------------


def test_modification_recommendations_built_from_breaches() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i * 0.5 for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.45, "tech", "software"), ("BBB", 0.45, "tech", "hardware"))
    _, decision = _service(RiskConstraintSet(max_sector_weight=0.4)).review(proposal, history, liquidity)
    types = {item.modification_type for item in decision.required_modifications}
    assert "reduce_sector_exposure" in types


def test_major_breach_categories_have_typed_modifications() -> None:
    expected = {
        RiskCategory.CONCENTRATION: RiskModificationType.REDUCE_SYMBOL_WEIGHT,
        RiskCategory.SECTOR: RiskModificationType.REDUCE_SECTOR_EXPOSURE,
        RiskCategory.INDUSTRY: RiskModificationType.REDUCE_INDUSTRY_EXPOSURE,
        RiskCategory.ASSET_CLASS: RiskModificationType.REDUCE_ASSET_CLASS_EXPOSURE,
        RiskCategory.CASH: RiskModificationType.INCREASE_CASH,
        RiskCategory.GROSS_EXPOSURE: RiskModificationType.REDUCE_GROSS_EXPOSURE,
        RiskCategory.NET_EXPOSURE: RiskModificationType.REDUCE_NET_EXPOSURE,
        RiskCategory.SHORT_EXPOSURE: RiskModificationType.REDUCE_SHORT_EXPOSURE,
        RiskCategory.TURNOVER: RiskModificationType.REDUCE_TURNOVER,
        RiskCategory.CORRELATION: RiskModificationType.REDUCE_CORRELATED_CLUSTER,
        RiskCategory.LIQUIDITY: RiskModificationType.REMOVE_OR_REDUCE_ILLIQUID_ASSET,
        RiskCategory.DATA_QUALITY: RiskModificationType.IMPROVE_DATA_QUALITY,
        RiskCategory.STALE_DATA: RiskModificationType.REFRESH_STALE_DATA,
        RiskCategory.VAR: RiskModificationType.REDUCE_VAR,
        RiskCategory.CVAR: RiskModificationType.REDUCE_CVAR,
        RiskCategory.VOLATILITY: RiskModificationType.REDUCE_VOLATILITY,
        RiskCategory.DRAWDOWN: RiskModificationType.REDUCE_DRAWDOWN_EXPOSURE,
    }
    assert all(RiskDecisionService.MODIFICATION_TYPES[category] is value for category, value in expected.items())


def test_new_limit_metrics_and_equality_boundaries() -> None:
    rules = RiskConstraintSet(
        maximum_average_correlation=0.5,
        maximum_pair_correlation=0.8,
        maximum_var_95=0.05,
        maximum_cvar_95=0.08,
        maximum_var_99=0.10,
        maximum_cvar_99=0.15,
        minimum_liquidity_score=0.4,
        maximum_illiquid_weight=0.2,
        maximum_unknown_metadata_weight=0.1,
    )
    definitions = (
        (RiskCategory.CORRELATION, "weighted_average_correlation", 0.51),
        (RiskCategory.CORRELATION, "max_pairwise_correlation", 0.81),
        (RiskCategory.VAR, "var_95", 0.06),
        (RiskCategory.CVAR, "cvar_95", 0.09),
        (RiskCategory.VAR, "var_99", 0.11),
        (RiskCategory.CVAR, "cvar_99", 0.16),
        (RiskCategory.LIQUIDITY, "liquidity_score", 0.39),
        (RiskCategory.LIQUIDITY, "illiquid_weight", 0.21),
        (RiskCategory.DATA_QUALITY, "unknown_metadata_weight", 0.11),
    )
    metrics = tuple(
        RiskMetric(metric_id=name, category=category, name=name, value=value, units="ratio", as_of=AS_OF, source_fingerprint="x")
        for category, name, value in definitions
    )
    assert {item.metric_name for item in RiskLimitEvaluator().evaluate(metrics, rules, "x")} == {name for _, name, _ in definitions}
    boundaries = tuple(
        metric.model_copy(update={"value": threshold})
        for metric, threshold in zip(metrics, (0.5, 0.8, 0.05, 0.08, 0.10, 0.15, 0.4, 0.2, 0.1), strict=True)
    )
    assert RiskLimitEvaluator().evaluate(boundaries, rules, "x") == ()


# ---------------------------------------------------------------------------
# Trace / provenance
# ---------------------------------------------------------------------------


def test_assessment_preserves_proposal_id_and_fingerprints() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.4, "tech", "software"), ("BBB", 0.4, "tech", "hardware"))
    assessment = _service().assess(proposal, history, liquidity)
    assert assessment.proposal_id == "proposal-11" == proposal.proposal_id
    assert assessment.portfolio_id == "portfolio-11"
    assert "input_fingerprint" in assessment.provenance
    assert "git_commit" in assessment.provenance


def test_assessment_deterministic_ids() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    proposal = _proposal(("AAA", 0.4, "tech", "software"), ("BBB", 0.4, "tech", "hardware"))
    first = _service().assess(proposal, history, liquidity)
    second = _service().assess(proposal, history, liquidity)
    assert first == second
    assert first.assessment_id == second.assessment_id
    expected = str(uuid5(NAMESPACE_URL, f"risk-assessment:{proposal.proposal_id}:{first.provenance['input_fingerprint']}"))
    assert first.assessment_id == expected


def test_expanded_provenance_fingerprints_windows_and_scenarios() -> None:
    proposal = _proposal(("AAA", 0.4, "tech", "software"), ("BBB", 0.4, "finance", "banking"))
    market = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 2_000_000}}
    first = _service().assess(proposal, market, liquidity)
    same = _service().assess(proposal, market, liquidity)
    assert first.provenance == same.provenance
    assert first.provenance["proposal_id"] == proposal.proposal_id
    assert first.provenance["proposal_algorithm_version"] == proposal.algorithm_version
    windows = first.provenance["historical_sample_windows"]
    assert windows["AAA"] == {
        "start": market[0].bars[0].timestamp.isoformat(),
        "end": market[0].bars[-1].timestamp.isoformat(),
        "sample_count": 31,
    }
    changed_policy = _service(RiskConstraintSet(max_var_95=0.06)).assess(proposal, market, liquidity)
    assert changed_policy.provenance["risk_policy_fingerprint"] != first.provenance["risk_policy_fingerprint"]
    changed_bar = market[0].bars[-1].model_copy(update={"close": market[0].bars[-1].close + 1.0})
    changed_history = [market[0].model_copy(update={"bars": [*market[0].bars[:-1], changed_bar]}), market[1]]
    changed_market = _service().assess(proposal, changed_history, liquidity)
    assert changed_market.provenance["market_data_fingerprints"]["AAA"] != first.provenance["market_data_fingerprints"]["AAA"]
    assert first.provenance["stress_scenario_version"] == StressTestingService.VERSION
    assert {item["scenario_id"] for item in first.provenance["stress_scenarios"]} >= {"correlation_to_one"}


def test_trace_dag_validity() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    assessment = _service().assess(_proposal(("AAA", 0.4, "tech", "software"), ("BBB", 0.4, "tech", "hardware")), history, liquidity)
    assert assessment.trace.nodes
    assert assessment.trace.edges
    types = {node.node_type for node in assessment.trace.nodes}
    assert {"PortfolioProposal", "RiskMetrics", "RiskBreaches", "StressResults", "RiskAssessment", "RiskDecision"}.issubset(types)


def test_provenance_git_commit_present_or_none() -> None:
    from app.services.risk.provenance import RiskProvenanceService

    result = RiskProvenanceService().git_commit()
    assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_validation_rejects_future_market_data() -> None:
    from app.services.risk.validation import RiskError, RiskErrorCode, RiskInputValidationService

    future = _bars("AAA", [100, 101, 102, 103], end=AS_OF + timedelta(days=1))
    with pytest.raises(RiskError) as exc:
        RiskInputValidationService().validate(_proposal(("AAA", 1.0, "tech", "software")), [future])
    assert exc.value.code == RiskErrorCode.FUTURE_DATA


def test_validation_rejects_duplicate_symbols() -> None:
    from app.services.risk.validation import RiskError, RiskErrorCode, RiskInputValidationService

    dup = _bars("AAA", [100, 101, 102, 103])
    with pytest.raises(RiskError) as exc:
        RiskInputValidationService().validate(_proposal(("AAA", 1.0, "tech", "software")), [dup, dup])
    assert exc.value.code == RiskErrorCode.INVALID_INPUT


def test_validation_rejects_non_finite_liquidity() -> None:
    from app.services.risk.validation import RiskError, RiskErrorCode, RiskInputValidationService

    with pytest.raises(RiskError) as exc:
        RiskInputValidationService.validate_liquidity({"AAA": {"average_dollar_volume": math.nan}})
    assert exc.value.code == RiskErrorCode.INVALID_INPUT
    with pytest.raises(RiskError) as exc:
        RiskInputValidationService.validate_liquidity({"AAA": {"average_dollar_volume": -1.0}})
    assert exc.value.code == RiskErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Overall risk score
# ---------------------------------------------------------------------------


def test_risk_score_increases_with_breaches_and_severity_ordering() -> None:
    history = [_bars("AAA", [100 + i for i in range(31)]), _bars("BBB", [80 + i for i in range(31)])]
    liquidity = {"AAA": {"average_dollar_volume": 5_000_000}, "BBB": {"average_dollar_volume": 5_000_000}}
    service = _service()
    safe = service.assess(_proposal(("AAA", 0.3, "tech", "software"), ("BBB", 0.3, "finance", "banking")), history, liquidity)
    risky = service.assess(_proposal(("AAA", 0.9, "tech", "software"), ("BBB", 0.05, "tech", "hardware")), history, liquidity)
    assert 0 <= safe.overall_risk_score <= 100
    assert 0 <= risky.overall_risk_score <= 100
    assert safe.overall_risk_score <= risky.overall_risk_score
    order = list(RiskSeverity)
    assert order.index(RiskSeverity.INFO) < order.index(RiskSeverity.CRITICAL)
