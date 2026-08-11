"""Phase 10 deterministic research portfolio manager tests."""

from __future__ import annotations

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cli.portfolio import main as portfolio_cli
from app.cli.portfolio import parser as portfolio_parser
from app.domain.models.analyst import (
    AnalysisDirection,
    AnalystOpinion,
    AnalystRole,
    ConfidenceScore,
    DataFreshness,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
)
from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar, Symbol
from app.domain.models.portfolio import AnalystContribution, PortfolioConstraintSet, PortfolioPosition, ResearchPortfolio
from app.main import app
from app.services.portfolio.config import PortfolioManagerConfig
from app.services.portfolio.constraints import ConstraintEngine
from app.services.portfolio.conviction import AssetConvictionService
from app.services.portfolio.correlation import PortfolioCorrelationService
from app.services.portfolio.diversification import DiversificationService
from app.services.portfolio.opinions import PortfolioOpinionAggregationService
from app.services.portfolio.service import PortfolioManagerService, PortfolioProposalRequest
from app.services.portfolio.turnover import compute_turnover, scale_to_turnover
from app.services.portfolio.validation import PortfolioInputValidationService, PortfolioValidationError

AS_OF = datetime(2025, 1, 10, tzinfo=UTC)


def opinion(identifier: str, symbol: str, direction: AnalysisDirection, confidence: float = 0.8, *, stale: bool = False) -> AnalystOpinion:
    observed = AS_OF - timedelta(days=2)
    evidence = EvidenceItem(
        evidence_id=f"e-{identifier}",
        evidence_type=EvidenceType.MARKET_DATA,
        observed_at=observed,
        available_at=observed,
        evaluated_at=AS_OF,
        valid_until=AS_OF + timedelta(days=1),
        strength=EvidenceStrength.MODERATE,
        summary="point-in-time evidence",
        confidence=0.75,
    )
    return AnalystOpinion(
        opinion_id=identifier,
        analyst_id=f"analyst-{identifier}",
        analyst_role=AnalystRole.TECHNICAL,
        ticker=symbol,
        direction=direction,
        confidence=ConfidenceScore(value=confidence, capped=False, has_historical_calibration=True),
        evidence=[evidence],
        generated_at=AS_OF,
        data_freshness=DataFreshness(
            observed_at=observed,
            available_at=observed,
            evaluated_at=AS_OF,
            stale_threshold=timedelta(days=1),
            is_stale=stale,
            age_seconds=172800.0,
        ),
    )


def portfolio(*positions: PortfolioPosition) -> ResearchPortfolio:
    return ResearchPortfolio(portfolio_id="research-1", as_of=AS_OF, positions=positions, cash_weight=1 - sum(p.weight for p in positions))


def contribution(identifier: str, orientation: float, confidence: float = 1.0) -> AnalystContribution:
    return AnalystContribution(
        opinion_id=identifier,
        analyst_id=identifier,
        analyst_role=AnalystRole.TECHNICAL,
        symbol="AAPL",
        orientation=orientation,
        confidence=confidence,
        freshness_factor=1.0,
        data_quality_factor=1.0,
        signed_contribution=orientation * confidence,
    )


def test_domain_is_strict_frozen_finite_and_research_only() -> None:
    model = portfolio(PortfolioPosition(symbol="AAPL", weight=0.2))
    assert (model.research_only, model.suitable_for_live_trading, model.decision_ready) == (True, False, False)
    with pytest.raises(ValidationError):
        model.cash_weight = 0.5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        PortfolioPosition(symbol="AAPL", weight=math.nan)
    with pytest.raises(ValidationError):
        ResearchPortfolio(portfolio_id="p", as_of=AS_OF, positions=(), cash_weight=0.9)
    with pytest.raises(ValidationError):
        ResearchPortfolio(portfolio_id="p", as_of=datetime.now(UTC) + timedelta(days=1), positions=(), cash_weight=1.0)


def test_opinion_validation_future_duplicate_insufficient_and_stale_mapping() -> None:
    valid = opinion("one", "AAPL", AnalysisDirection.BULLISH, stale=True)
    validator = PortfolioInputValidationService()
    with pytest.raises(PortfolioValidationError, match="unique"):
        validator.validate_opinions([valid, valid], AS_OF)
    with pytest.raises(PortfolioValidationError, match="Future"):
        validator.validate_opinions([valid], AS_OF - timedelta(seconds=1))
    insufficient = valid.model_copy(update={"opinion_id": "two", "direction": AnalysisDirection.INSUFFICIENT_EVIDENCE, "evidence": []})
    assert validator.validate_opinions([insufficient], AS_OF) == []
    mapped = PortfolioOpinionAggregationService().contributions([valid])[0]
    assert mapped.orientation == 1 and mapped.freshness_factor == PortfolioManagerConfig().stale_opinion_factor


@pytest.mark.parametrize(
    ("items", "agreement", "conviction_sign"),
    [
        ([contribution("a", 1), contribution("b", 1)], 1.0, 1),
        ([contribution("a", 1), contribution("b", -1)], 0.0, 0),
        ([contribution("a", -1)], 1.0, -1),
    ],
)
def test_agreement_and_bullish_bearish_mixed_conviction(items: list[AnalystContribution], agreement: float, conviction_sign: int) -> None:
    result = AssetConvictionService().calculate(items)[0]
    assert result.agreement == agreement
    assert (result.conviction > 0) - (result.conviction < 0) == conviction_sign


def test_confidence_dispersion_coverage_quality_and_determinism() -> None:
    items = [contribution("a", 1, 0.9), contribution("b", 1, 0.3)]
    service = AssetConvictionService(PortfolioManagerConfig(minimum_analyst_coverage=3))
    result = service.calculate(items)[0]
    assert result.confidence_dispersion > 0 and not result.sufficient_coverage and result.conviction == 0
    assert service.calculate(items) == service.calculate(items)


def test_constraint_caps_cash_groups_counts_and_shorts() -> None:
    metadata = {
        "A": PortfolioPosition(symbol="A", weight=0, sector="tech", industry="software"),
        "B": PortfolioPosition(symbol="B", weight=0, sector="tech", industry="hardware"),
        "C": PortfolioPosition(symbol="C", weight=0, sector="finance", asset_class="bond"),
    }
    constraints = PortfolioConstraintSet(
        max_position_weight=0.3,
        max_sector_weight=0.4,
        max_industry_weight=0.25,
        max_asset_class_weight=0.5,
        min_cash_weight=0.2,
        max_gross_exposure=0.8,
        max_positions=2,
    )
    weights, adjustments = ConstraintEngine().apply({"A": 0.6, "B": 0.4, "C": -0.2}, metadata, constraints)
    assert weights["C"] == 0 and sum(abs(value) for value in weights.values()) <= 0.8
    assert any("position_cap" in item for item in adjustments) and any("shorts_disabled" in item for item in adjustments)


def test_proposal_deterministic_turnover_weak_cash_trace_and_provenance() -> None:
    request = PortfolioProposalRequest(
        portfolio=portfolio(),
        opinions=[opinion("one", "AAPL", AnalysisDirection.BULLISH)],
        constraints=PortfolioConstraintSet(max_position_weight=0.5, min_cash_weight=0.2),
        as_of=AS_OF,
    )
    service = PortfolioManagerService()
    first, second = service.propose(request), service.propose(request)
    assert first == second and first.proposal_id == second.proposal_id and first.opinion_ids == ("one",)
    assert first.cash_weight >= 0.2 and first.trace.nodes and first.trace.edges
    assert any(node.node_type == "ConstraintAdjustments" for node in first.trace.nodes)


def test_turnover_no_change_cap_and_deterministic_scaling() -> None:
    assert compute_turnover({"A": 0.5}, {"A": 0.5}) == 0
    first = scale_to_turnover({"A": 0.5}, {"B": 0.5}, 0.1)
    assert first == scale_to_turnover({"A": 0.5}, {"B": 0.5}, 0.1)
    assert first[1] == pytest.approx(0.1) and first[2]


def bars(symbol: str, closes: list[float]) -> HistoricalBarsResult:
    values = [
        OHLCVBar(timestamp=AS_OF - timedelta(days=len(closes) - index), open=value, high=value, low=value, close=value, volume=1)
        for index, value in enumerate(closes)
    ]
    return HistoricalBarsResult(
        symbol=Symbol(symbol),
        timeframe="1d",
        bars=values,
        provider="offline",
        requested_start=values[0].timestamp,
        requested_end=AS_OF,
        actual_start=values[0].timestamp,
        actual_end=values[-1].timestamp,
        adjustment="raw",
        session="regular",
        retrieved_at=AS_OF,
    )


def test_correlation_identical_missing_insufficient_and_no_future() -> None:
    identical = PortfolioCorrelationService(3).correlations([bars("A", [1, 2, 3, 4]), bars("B", [2, 4, 6, 8])], AS_OF)
    assert identical[("A", "B")] == pytest.approx(1)
    assert PortfolioCorrelationService(5).correlations([bars("A", [1, 2]), bars("B", [2, 3])], AS_OF)[("A", "B")] is None
    assert PortfolioCorrelationService().correlations([], AS_OF) == {}


def test_diversification_hhi_effective_sector_and_correlation_concentration() -> None:
    service = DiversificationService()
    assert service.hhi([0.5, 0.5]) == 0.5 and service.effective_positions([0.5, 0.5]) == 2
    assert service.correlation_concentration({"A": 0.5, "B": 0.5}, {("A", "B"): 0.9}) == 0.25


def test_api_surfaces_safe_errors_and_has_no_execution_routes() -> None:
    client = TestClient(app)
    assert client.get("/portfolio/health").status_code == 200
    assert client.get("/portfolio/metadata").json()["output"] == "PortfolioProposal"
    assert client.post("/portfolio/propose", json={}).status_code == 422
    payload = {
        "portfolio": portfolio().model_dump(mode="json"),
        "opinions": [opinion("api", "AAPL", AnalysisDirection.BULLISH).model_dump(mode="json")],
        "as_of": AS_OF.isoformat(),
    }
    response = client.post("/portfolio/propose", json=payload)
    assert response.status_code == 200 and response.json()["research_only"] is True
    payload["as_of"] = (AS_OF - timedelta(seconds=1)).isoformat()
    payload["portfolio"]["as_of"] = (AS_OF - timedelta(days=1)).isoformat()
    future = client.post("/portfolio/propose", json=payload)
    assert future.status_code == 422 and future.json()["code"] == "HTTP_ERROR"
    assert future.json()["safe_message"] == "Future analyst opinions are forbidden"
    paths = app.openapi()["paths"]
    assert not any(term in path for path in paths for term in ("/portfolio/execute", "/portfolio/order", "/portfolio/rebalance"))


def test_portfolio_package_has_no_forbidden_imports_or_calls() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "portfolio"
    forbidden = {"Broker", "PaperBroker", "ExecutionService", "OrderRequest", "RiskEngine"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert names.isdisjoint(forbidden)


def test_cli_help_offline_json_and_deterministic_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_exit:
        portfolio_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    capsys.readouterr()
    portfolio_path, opinions_path = tmp_path / "portfolio.json", tmp_path / "opinions.json"
    portfolio_path.write_text(json.dumps(portfolio().model_dump(mode="json")), encoding="utf-8")
    opinions_path.write_text(json.dumps([opinion("cli", "AAPL", AnalysisDirection.BULLISH).model_dump(mode="json")]), encoding="utf-8")
    arguments = ["--portfolio-json", str(portfolio_path), "--opinions-json", str(opinions_path), "--as-of", AS_OF.isoformat(), "--json"]
    assert portfolio_cli(arguments) == 0
    first = capsys.readouterr().out
    assert portfolio_cli(arguments) == 0
    second = capsys.readouterr().out
    assert first == second and json.loads(first)["research_only"] is True
