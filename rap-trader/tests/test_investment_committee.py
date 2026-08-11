"""Phase 12 deterministic, cross-functional research governance tests."""

from __future__ import annotations

import ast
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cli.committee import main as committee_cli
from app.cli.committee import parser as committee_parser
from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisTrace,
    AnalystOpinion,
    AnalystRole,
    ConfidenceScore,
    DataFreshness,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    TraceNode,
    validate_trace,
)
from app.domain.models.committee import CommitteeMemberRole, CommitteeRecommendationType
from app.domain.models.portfolio import PortfolioProposal, PortfolioProposalPosition
from app.domain.models.risk import RiskDecision, RiskDecisionType
from app.main import app
from app.services.committee import CommitteeConfig, CommitteeError, CommitteeErrorCode, InvestmentCommitteeService
from app.services.committee.alignment import CommitteeAlignmentService
from app.services.committee.portfolio_review import CommitteePortfolioReviewService
from app.services.committee.research_case import ResearchCaseAssemblyService
from app.services.risk import RiskOfficerService

AS_OF = datetime(2025, 1, 10, tzinfo=UTC)
ROLES = (AnalystRole.TECHNICAL, AnalystRole.FUNDAMENTAL, AnalystRole.MACRO, AnalystRole.NEWS)


def opinion(
    role: AnalystRole,
    direction: AnalysisDirection = AnalysisDirection.BULLISH,
    confidence: float = 0.8,
    *,
    stale: bool = False,
    generated_at: datetime = AS_OF,
) -> AnalystOpinion:
    observed = AS_OF - timedelta(hours=2)
    return AnalystOpinion(
        opinion_id=f"opinion-{role.value.lower()}",
        analyst_id=f"analyst-{role.value.lower()}",
        analyst_role=role,
        ticker="AAA",
        direction=direction,
        confidence=ConfidenceScore(value=confidence, capped=False, has_historical_calibration=True),
        evidence=[
            EvidenceItem(
                evidence_id=f"e-{role.value}",
                evidence_type=EvidenceType.MARKET_DATA,
                observed_at=observed,
                available_at=observed,
                evaluated_at=AS_OF,
                valid_until=AS_OF + timedelta(days=1),
                strength=EvidenceStrength.STRONG,
                summary="offline evidence",
                confidence=confidence,
            )
        ],
        generated_at=generated_at,
        data_freshness=DataFreshness(
            observed_at=observed,
            available_at=observed,
            evaluated_at=AS_OF,
            stale_threshold=timedelta(days=1),
            is_stale=stale,
            age_seconds=7200.0,
        ),
    )


def proposal(*, weight: float = 0.2, conviction: float = 0.8) -> PortfolioProposal:
    trace = AnalysisTrace(
        trace_id="proposal-trace", nodes=[TraceNode(node_id="source", node_type="fixture", created_at=AS_OF)], edges=[], created_at=AS_OF
    )
    return PortfolioProposal(
        proposal_id="proposal-12",
        portfolio_id="portfolio-12",
        as_of=AS_OF,
        positions=(PortfolioProposalPosition(symbol="AAA", current_weight=0.1, proposed_weight=weight, conviction=conviction),),
        cash_weight=1 - weight,
        gross_exposure=weight,
        net_exposure=weight,
        turnover=0.1,
        input_fingerprint="input",
        config_fingerprint="config",
        constraint_fingerprint="constraints",
        algorithm_version="phase-10-v1",
        trace=trace,
    )


def inputs(
    decision_type: RiskDecisionType = RiskDecisionType.APPROVE,
    directions: tuple[AnalysisDirection, ...] | None = None,
    confidences: tuple[float, ...] | None = None,
) -> tuple[list[AnalystOpinion], PortfolioProposal, object, RiskDecision]:
    directions = directions or (AnalysisDirection.BULLISH,) * 4
    confidences = confidences or (0.8,) * 4
    opinions = [opinion(role, direction, confidence) for role, direction, confidence in zip(ROLES, directions, confidences, strict=True)]
    item = proposal()
    assessment = RiskOfficerService().assess(item)
    assessment = assessment.model_copy(update={"data_quality_score": 0.9})
    decision = RiskDecision(
        decision_id="risk-decision-12",
        assessment_id=assessment.assessment_id,
        proposal_id=item.proposal_id,
        decision=decision_type,
        rationale=(decision_type.value,),
        required_modifications=(),
        blocking_breaches=("hard-risk-blocker",) if decision_type is RiskDecisionType.REJECT else (),
        warnings=(),
    )
    return opinions, item, assessment, decision


def test_domain_enums_frozen_finite_and_safety_invariants() -> None:
    assert CommitteeMemberRole.TECHNICAL_ANALYST.value == "technical_analyst"
    assert CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL.value == "approve_research_proposal"
    assessment, recommendation = InvestmentCommitteeService().review(*inputs())
    assert (assessment.research_only, assessment.suitable_for_live_trading, assessment.decision_ready) == (True, False, False)
    assert recommendation.requires_chairman_review is True
    with pytest.raises(ValidationError):
        assessment.committee_confidence = 0.2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        assessment.model_copy(update={"committee_confidence": math.nan}).model_validate(
            assessment.model_copy(update={"committee_confidence": math.nan}).model_dump()
        )


def test_alignment_full_partial_strong_dissent_mixed_and_missing() -> None:
    opinions, _, _, _ = inputs(
        directions=(AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BEARISH),
        confidences=(0.8, 0.8, 0.8, 0.95),
    )
    case = ResearchCaseAssemblyService().assemble(opinions, CommitteeConfig().required_specialist_roles)
    alignment = CommitteeAlignmentService().calculate(case, 4, 0.75)
    assert alignment.directional_agreement == 0.75
    assert alignment.strong_minority_roles == (CommitteeMemberRole.NEWS_ANALYST,)
    assert alignment.confidence_dispersion == pytest.approx(0.15)
    missing = ResearchCaseAssemblyService().assemble(opinions[:3], CommitteeConfig().required_specialist_roles)
    assert missing.missing_roles == (CommitteeMemberRole.NEWS_ANALYST,)


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskDecisionType.REJECT, CommitteeRecommendationType.REJECT_RESEARCH_PROPOSAL),
        (RiskDecisionType.REQUIRE_MODIFICATION, CommitteeRecommendationType.REVISE_RESEARCH_PROPOSAL),
        (RiskDecisionType.INSUFFICIENT_DATA, CommitteeRecommendationType.INSUFFICIENT_EVIDENCE),
    ],
)
def test_risk_precedence_hard_cases(risk: RiskDecisionType, expected: CommitteeRecommendationType) -> None:
    _, recommendation = InvestmentCommitteeService().review(*inputs(risk))
    assert recommendation.recommendation is expected
    assert recommendation.recommendation is not CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL


def test_case_2_high_confidence_minority_is_preserved_and_reduces_confidence() -> None:
    baseline = InvestmentCommitteeService().review(*inputs())[1]
    args = inputs(
        directions=(AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BEARISH),
        confidences=(0.8, 0.8, 0.8, 0.95),
    )
    recommendation = InvestmentCommitteeService().review(*args)[1]
    assert recommendation.dissenting_views[0].view == "BEARISH"
    assert recommendation.dissenting_views[0].blocking is True
    assert recommendation.confidence < baseline.confidence


def test_cases_5_and_6_mixed_defers_and_clean_alignment_approves() -> None:
    mixed = inputs(directions=(AnalysisDirection.BULLISH, AnalysisDirection.BEARISH, AnalysisDirection.NEUTRAL, AnalysisDirection.BULLISH))
    assert InvestmentCommitteeService().review(*mixed)[1].recommendation is CommitteeRecommendationType.DEFER
    assessment, recommendation = InvestmentCommitteeService().review(*inputs())
    assert recommendation.recommendation is CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL
    assert not assessment.conflicts


def test_conflicts_portfolio_review_and_questions() -> None:
    args = inputs(directions=(AnalysisDirection.BULLISH, AnalysisDirection.BEARISH, AnalysisDirection.BULLISH, AnalysisDirection.BULLISH))
    assessment, _ = InvestmentCommitteeService().review(*args)
    assert {item.conflict_type for item in assessment.conflicts} >= {"technical_vs_fundamental", "company_vs_macro", "news_vs_fundamental"}
    case = ResearchCaseAssemblyService().assemble(args[0], CommitteeConfig().required_specialist_roles)
    review = CommitteePortfolioReviewService().review(proposal(weight=0.4, conviction=0.1), case, CommitteeConfig())
    assert not review.acceptable and len(review.required_modifications) == 2


def test_validation_duplicate_missing_stale_future_timestamp_and_mismatches() -> None:
    service = InvestmentCommitteeService()
    opinions, item, assessment, decision = inputs()
    with pytest.raises(CommitteeError, match="unique"):
        service.assess([*opinions, opinions[0]], item, assessment, decision)
    with pytest.raises(CommitteeError) as stale:
        service.assess([opinion(AnalystRole.TECHNICAL, stale=True), *opinions[1:]], item, assessment, decision)
    assert stale.value.code is CommitteeErrorCode.STALE_INPUT
    future = opinions[0].model_copy(update={"generated_at": AS_OF + timedelta(seconds=1)})
    with pytest.raises(CommitteeError) as invalid:
        service.assess([future, *opinions[1:]], item, assessment, decision)
    assert invalid.value.code is CommitteeErrorCode.FUTURE_DATA
    with pytest.raises(CommitteeError):
        service.assess(opinions, item, assessment.model_copy(update={"proposal_id": "other"}), decision)
    with pytest.raises(CommitteeError):
        service.assess(opinions, item, assessment, decision.model_copy(update={"assessment_id": "other"}))
    with pytest.raises(CommitteeError):
        service.assess(opinions, item, assessment, decision, AS_OF + timedelta(seconds=1))


def test_provenance_trace_ids_and_determinism() -> None:
    first = InvestmentCommitteeService().review(*inputs())
    second = InvestmentCommitteeService().review(*inputs())
    assert first[0].assessment_id == second[0].assessment_id
    assert first[1].recommendation_id == second[1].recommendation_id
    assert set(first[0].provenance["opinion_ids"]) == {f"opinion-{role.value.lower()}" for role in ROLES}
    assert len(first[0].provenance["committee_policy_fingerprint"]) == 64
    validate_trace(first[0].trace)
    assert not any("execution" in node.node_type.lower() or "order" in node.node_type.lower() for node in first[0].trace.nodes)


def test_api_health_metadata_review_errors_and_no_execution_routes() -> None:
    client = TestClient(app)
    assert client.get("/committee/health").json()["offline"] is True
    assert client.get("/committee/metadata").json()["decision_ready"] is False
    opinions, item, assessment, decision = inputs()
    payload = {
        "opinions": [value.model_dump(mode="json") for value in opinions],
        "proposal": item.model_dump(mode="json"),
        "risk_assessment": assessment.model_dump(mode="json"),
        "risk_decision": decision.model_dump(mode="json"),
    }
    response = client.post("/committee/review", json=payload)
    assert response.status_code == 200
    assert response.json()["recommendation"]["recommendation"] == "approve_research_proposal"
    payload["risk_decision"]["assessment_id"] = "wrong"
    assert client.post("/committee/assess", json=payload).status_code == 422
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/committee/execute" not in paths and "/committee/order" not in paths


def test_cli_help_offline_review_and_deterministic_json(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as help_exit:
        committee_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    opinions, item, assessment, decision = inputs()
    files = {
        "opinions": [value.model_dump(mode="json") for value in opinions],
        "proposal": item.model_dump(mode="json"),
        "assessment": assessment.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }
    paths: dict[str, Path] = {}
    for name, value in files.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    output = tmp_path / "review.json"
    argv = [
        "--opinions-json",
        str(paths["opinions"]),
        "--proposal-json",
        str(paths["proposal"]),
        "--risk-assessment-json",
        str(paths["assessment"]),
        "--risk-decision-json",
        str(paths["decision"]),
        "--json",
        "--output",
        str(output),
    ]
    assert committee_cli(argv) == committee_cli(argv) == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["recommendation"]["requires_chairman_review"] is True


def test_committee_package_has_no_forbidden_imports_or_network_llm_calls() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "committee"
    forbidden = ("broker", "execution", "order", "chairman", "requests", "httpx", "openai", "anthropic", "transformers")
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any(token in name.lower() for name in imports for token in forbidden), (path, imports)
