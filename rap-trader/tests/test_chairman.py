"""Phase 13 deterministic Chairman research-governance tests."""

import ast
import json
import math
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cli.chairman import main as chairman_cli
from app.cli.chairman import parser as chairman_parser
from app.domain.models.analyst import AnalysisDirection
from app.domain.models.chairman import ChairmanDecisionType
from app.domain.models.committee import CommitteeConflict, CommitteeMemberRole, CommitteeRecommendationType
from app.domain.models.risk import RiskDecisionType
from app.main import app
from app.services.chairman import ChairmanService
from app.services.committee import InvestmentCommitteeService
from tests.test_investment_committee import AS_OF, inputs


def review(risk: RiskDecisionType = RiskDecisionType.APPROVE):
    opinions, proposal, risk_assessment, risk_decision = inputs(risk)
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    return ChairmanService().review(committee, recommendation, proposal, risk_assessment, risk_decision)


def test_domain_frozen_finite_research_only_and_deterministic_ids() -> None:
    first = review()
    second = review()
    assert first[0].assessment_id == second[0].assessment_id
    assert first[1].decision_id == second[1].decision_id
    assert first[1].decision is ChairmanDecisionType.APPROVE_RESEARCH
    assert (first[0].research_only, first[0].suitable_for_live_trading, first[0].decision_ready) == (True, False, False)
    assert (first[1].research_only, first[1].suitable_for_live_trading, first[1].decision_ready) == (True, False, False)
    with pytest.raises(ValidationError):
        first[0].governance_score = 0.1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        type(first[0]).model_validate(first[0].model_copy(update={"governance_score": math.nan}).model_dump())


@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskDecisionType.REJECT, ChairmanDecisionType.REJECT_RESEARCH),
        (RiskDecisionType.INSUFFICIENT_DATA, ChairmanDecisionType.INSUFFICIENT_EVIDENCE),
    ],
)
def test_risk_precedence_is_never_overridden(risk: RiskDecisionType, expected: ChairmanDecisionType) -> None:
    assert review(risk)[1].decision is expected


def test_committee_approval_can_be_revised_for_missing_governance_provenance_trace_and_dissent() -> None:
    opinions, proposal, risk_assessment, risk_decision = inputs()
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    assert recommendation.recommendation is CommitteeRecommendationType.APPROVE_RESEARCH_PROPOSAL
    cases = [
        committee.model_copy(update={"coverage_score": 0.5}),
        committee.model_copy(update={"provenance": {}}),
        committee.model_copy(update={"trace": committee.trace.model_copy(update={"nodes": committee.trace.nodes[:1], "edges": []})}),
    ]
    for item in cases:
        assessment, decision = ChairmanService().review(item, recommendation, proposal, risk_assessment, risk_decision)
        assert assessment.unresolved_questions
        assert decision.decision is not ChairmanDecisionType.APPROVE_RESEARCH


def test_unresolved_conflict_ignored_dissent_and_stale_committee_do_not_approve() -> None:
    opinions, proposal, risk_assessment, risk_decision = inputs()
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    conflict = CommitteeConflict(
        conflict_id="critical-conflict",
        conflict_type="governance",
        roles=(CommitteeMemberRole.PORTFOLIO_MANAGER, CommitteeMemberRole.RISK_OFFICER),
        description="Critical governance conflict",
        severity="critical",
        unresolved=True,
        recommended_followup="Return to committee",
    )
    conflicted = committee.model_copy(update={"conflicts": (conflict,)})
    assert (
        ChairmanService().review(conflicted, recommendation, proposal, risk_assessment, risk_decision)[1].decision
        is not ChairmanDecisionType.APPROVE_RESEARCH
    )

    dissent_inputs = inputs(
        directions=(AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BULLISH, AnalysisDirection.BEARISH),
        confidences=(0.8, 0.8, 0.8, 0.95),
    )
    dissent_committee, dissent_recommendation = InvestmentCommitteeService().review(*dissent_inputs)
    assert dissent_recommendation.dissenting_views
    assert (
        ChairmanService()
        .review(dissent_committee, dissent_recommendation, dissent_inputs[1], dissent_inputs[2], dissent_inputs[3])[1]
        .decision
        is not ChairmanDecisionType.APPROVE_RESEARCH
    )

    stale = committee.model_copy(update={"as_of": AS_OF - timedelta(days=8)})
    stale_proposal = proposal.model_copy(update={"as_of": AS_OF - timedelta(days=8)})
    stale_risk = risk_assessment.model_copy(update={"as_of": AS_OF - timedelta(days=8)})
    assessment, decision = ChairmanService().review(stale, recommendation, stale_proposal, stale_risk, risk_decision, AS_OF)
    assert any(item.category == "data_freshness" for item in assessment.governance_findings)
    assert decision.decision is not ChairmanDecisionType.APPROVE_RESEARCH


def test_future_timestamp_is_rejected() -> None:
    opinions, proposal, risk_assessment, risk_decision = inputs()
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    with pytest.raises(ValueError, match="Future"):
        ChairmanService().review(committee, recommendation, proposal, risk_assessment, risk_decision, AS_OF - timedelta(seconds=1))


def test_api_health_metadata_assess_review_error_and_no_execution_routes() -> None:
    opinions, proposal, risk_assessment, risk_decision = inputs()
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    payload = {
        "committee_assessment": committee.model_dump(mode="json"),
        "committee_recommendation": recommendation.model_dump(mode="json"),
        "proposal": proposal.model_dump(mode="json"),
        "risk_assessment": risk_assessment.model_dump(mode="json"),
        "risk_decision": risk_decision.model_dump(mode="json"),
    }
    client = TestClient(app)
    assert client.get("/chairman/health").json()["offline"] is True
    assert client.get("/chairman/metadata").json()["execution_authority"] is False
    assert client.post("/chairman/assess", json=payload).status_code == 200
    assert client.post("/chairman/review", json=payload).status_code == 200
    payload["risk_decision"]["assessment_id"] = "wrong"
    assert client.post("/chairman/review", json=payload).status_code == 422
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/chairman/execute" not in paths and "/chairman/order" not in paths


def test_cli_help_offline_review_deterministic_json_and_forbidden_imports(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        chairman_parser().parse_args(["--help"])
    opinions, proposal, risk_assessment, risk_decision = inputs()
    committee, recommendation = InvestmentCommitteeService().review(opinions, proposal, risk_assessment, risk_decision)
    values = {
        "assessment": committee,
        "recommendation": recommendation,
        "proposal": proposal,
        "risk-assessment": risk_assessment,
        "risk-decision": risk_decision,
    }
    paths: dict[str, Path] = {}
    for name, value in values.items():
        path = tmp_path / f"{name}.json"
        path.write_text(value.model_dump_json(), encoding="utf-8")
        paths[name] = path
    output = tmp_path / "output.json"
    argv = [
        "--assessment-json",
        str(paths["assessment"]),
        "--recommendation-json",
        str(paths["recommendation"]),
        "--proposal-json",
        str(paths["proposal"]),
        "--risk-assessment-json",
        str(paths["risk-assessment"]),
        "--risk-decision-json",
        str(paths["risk-decision"]),
        "--json",
        "--output",
        str(output),
    ]
    assert chairman_cli(argv) == chairman_cli(argv) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["decision"]["research_only"] is True
    forbidden = {"broker", "execution", "order", "requests", "httpx", "openai"}
    service_root = Path("app/services/chairman")
    for source in service_root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
        }
        assert not imported & forbidden
