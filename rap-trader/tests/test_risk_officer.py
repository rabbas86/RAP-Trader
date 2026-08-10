"""Phase 11 deterministic research-only Risk Officer tests."""

from __future__ import annotations

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cli.risk import main as risk_cli
from app.cli.risk import parser as risk_parser
from app.domain.models.analyst import AnalysisTrace, TraceNode
from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.domain.models.portfolio import PortfolioProposal, PortfolioProposalPosition
from app.domain.models.risk import RiskDecisionType, RiskMetric, RiskSeverity
from app.main import app
from app.services.risk.concentration import ConcentrationRiskService
from app.services.risk.correlation import CorrelationRiskService
from app.services.risk.data_quality import RiskDataQualityService
from app.services.risk.drawdown import DrawdownRiskService
from app.services.risk.exposure import ExposureRiskService
from app.services.risk.liquidity import LiquidityRiskService
from app.services.risk.service import RiskOfficerService
from app.services.risk.stress import StressTestingService
from app.services.risk.turnover import TurnoverReviewService
from app.services.risk.var import VaRCVaRService
from app.services.risk.volatility import VolatilityRiskService

AS_OF = datetime(2025, 1, 10, tzinfo=UTC)


def proposal(*, first: float = 0.4, second: float = 0.4, turnover: float = 0.2) -> PortfolioProposal:
    positions = (
        PortfolioProposalPosition(
            symbol="AAA", current_weight=0.3, proposed_weight=first, conviction=0.7, sector="technology", industry="software"
        ),
        PortfolioProposalPosition(
            symbol="BBB", current_weight=0.3, proposed_weight=second, conviction=0.6, sector="technology", industry="hardware"
        ),
    )
    net = first + second
    trace = AnalysisTrace(
        trace_id="proposal-trace", nodes=[TraceNode(node_id="source", node_type="fixture", created_at=AS_OF)], edges=[], created_at=AS_OF
    )
    return PortfolioProposal(
        proposal_id="proposal-11",
        portfolio_id="portfolio-11",
        as_of=AS_OF,
        positions=positions,
        cash_weight=1 - net,
        gross_exposure=abs(first) + abs(second),
        net_exposure=net,
        turnover=turnover,
        input_fingerprint="input",
        config_fingerprint="config",
        constraint_fingerprint="constraint",
        algorithm_version="phase-10",
        trace=trace,
    )


def bars(symbol: str, closes: list[float], *, stale: bool = False) -> HistoricalBarsResult:
    end = AS_OF - timedelta(days=10 if stale else 0)
    values = [
        OHLCVBar(timestamp=end - timedelta(days=len(closes) - index - 1), open=value, high=value, low=value, close=value, volume=1000)
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


def history() -> list[HistoricalBarsResult]:
    return [
        bars("AAA", [100 + index + (index % 3) for index in range(31)]),
        bars("BBB", [80 + index * 0.5 - (index % 2) for index in range(31)]),
    ]


def liquidity() -> dict[str, dict[str, float]]:
    return {"AAA": {"average_dollar_volume": 5_000_000.0}, "BBB": {"average_dollar_volume": 2_000_000.0}}


def test_domain_models_are_frozen_finite_and_research_only() -> None:
    result = RiskOfficerService().assess(proposal(), history(), liquidity())
    assert (result.research_only, result.suitable_for_live_trading, result.decision_ready) == (True, False, False)
    with pytest.raises(ValidationError):
        result.overall_risk_score = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RiskMetric(metric_id="x", category="volatility", name="x", value=math.nan, units="ratio", as_of=AS_OF, source_fingerprint="x")


def test_concentration_and_exposure_metrics() -> None:
    concentration = ConcentrationRiskService().calculate(proposal())
    assert concentration["hhi"] == pytest.approx(0.32)
    assert concentration["effective_positions"] == pytest.approx(3.125)
    assert concentration["top_3_weight"] == pytest.approx(0.8)
    assert concentration["max_sector_weight"] == pytest.approx(0.8)
    exposure = ExposureRiskService().calculate(proposal(first=0.7, second=-0.1))
    assert exposure == pytest.approx(
        {
            "gross_exposure": 0.8,
            "net_exposure": 0.6,
            "long_exposure": 0.7,
            "short_exposure": 0.1,
            "cash_weight": 0.4,
            "implied_leverage": 0.8,
        }
    )


def test_volatility_correlation_drawdown_and_var() -> None:
    items = history()
    volatility = VolatilityRiskService().calculate(proposal(), items, 20)
    assert volatility["sample_size"] == 30 and volatility["portfolio_volatility"] is not None
    correlation = CorrelationRiskService().calculate(proposal(), items, 20)
    assert correlation["pair_count"] == 1 and correlation["max_pairwise_correlation"] is not None
    drawdown = DrawdownRiskService().calculate([bars("AAA", [100, 120, 90, 100, 121])])
    assert drawdown["max_drawdown"] == pytest.approx(0.25)
    risk = VaRCVaRService.calculate([-0.10, -0.05, 0.01, 0.02] * 10, 0.95, 20)
    assert risk["valid"] and risk["var"] == pytest.approx(0.10) and risk["cvar"] == pytest.approx(0.10)
    assert not VaRCVaRService.calculate([0.1], 0.95, 20)["valid"]


def test_liquidity_turnover_data_quality_and_stress() -> None:
    illiquid = LiquidityRiskService().calculate(proposal(), {"AAA": {"average_dollar_volume": 100.0}}, 1_000_000)
    assert illiquid["illiquid_weight"] == pytest.approx(0.4) and illiquid["warnings"]
    assert TurnoverReviewService.calculate(proposal(turnover=0.8), 0.5)[1]
    quality = RiskDataQualityService().calculate(proposal(), [bars("AAA", list(range(1, 25)), stale=True)], {}, 20, timedelta(days=7))
    assert quality["score"] < 1 and not quality["sufficient"]
    stresses = StressTestingService().run(proposal(), 0.1)
    assert len(stresses) == 10 and all(item.estimated_portfolio_impact <= 0 for item in stresses)


def test_limits_decisions_modifications_trace_provenance_and_determinism() -> None:
    service = RiskOfficerService()
    first = service.assess(proposal(), history(), liquidity())
    second = service.assess(proposal(), history(), liquidity())
    assert first == second and first.assessment_id == second.assessment_id
    assert first.breaches and first.highest_severity in {RiskSeverity.HIGH, RiskSeverity.CRITICAL}
    decision = service.decide(first)
    assert decision.decision in {RiskDecisionType.REJECT, RiskDecisionType.REQUIRE_MODIFICATION}
    assert decision.required_modifications and first.trace.nodes and first.trace.edges
    insufficient = service.review(proposal(), [], {})[1]
    assert insufficient.decision is RiskDecisionType.INSUFFICIENT_DATA


def test_api_surfaces_and_no_execution_endpoints() -> None:
    client = TestClient(app)
    assert client.get("/risk/health").status_code == 200
    assert client.get("/risk/metadata").json()["output"] == "RiskAssessment and RiskDecision"
    assert client.post("/risk/assess", json={}).status_code == 422
    payload = {
        "proposal": proposal().model_dump(mode="json"),
        "historical_bars": [item.model_dump(mode="json") for item in history()],
        "liquidity_inputs": liquidity(),
    }
    response = client.post("/risk/review", json=payload)
    assert response.status_code == 200 and response.json()["decision"]["research_only"] is True
    assert not any(term in path for path in app.openapi()["paths"] for term in ("/risk/execute", "/risk/order", "/risk/trade"))


def test_cli_help_json_summary_output_and_determinism(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_exit:
        risk_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    capsys.readouterr()
    proposal_path, history_path, liquidity_path = tmp_path / "proposal.json", tmp_path / "history.json", tmp_path / "liquidity.json"
    proposal_path.write_text(json.dumps(proposal().model_dump(mode="json")), encoding="utf-8")
    history_path.write_text(json.dumps([item.model_dump(mode="json") for item in history()]), encoding="utf-8")
    liquidity_path.write_text(json.dumps(liquidity()), encoding="utf-8")
    arguments = [
        "--proposal-json",
        str(proposal_path),
        "--history-json",
        str(history_path),
        "--liquidity-json",
        str(liquidity_path),
        "--as-of",
        AS_OF.isoformat(),
        "--json",
    ]
    assert risk_cli(arguments) == 0
    first = capsys.readouterr().out
    assert risk_cli(arguments) == 0
    assert first == capsys.readouterr().out and json.loads(first)["assessment"]["research_only"] is True


def test_phase_11_source_has_no_forbidden_imports_or_names() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "risk"
    forbidden = {"Broker", "PaperBroker", "ExecutionService", "OrderRequest", "InvestmentCommittee", "Chairman"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        imported = {
            alias.name.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert names.isdisjoint(forbidden) and imported.isdisjoint(forbidden)
