"""Offline Chairman command-line governance review."""

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from app.domain.models.committee import CommitteeAssessment, CommitteeRecommendation
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.chairman import ChairmanConfig, ChairmanService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="chairman", description="Run an offline research-only Chairman governance review")
    result.add_argument("--assessment-json", required=True)
    result.add_argument("--recommendation-json", required=True)
    result.add_argument("--proposal-json", required=True)
    result.add_argument("--risk-assessment-json", required=True)
    result.add_argument("--risk-decision-json", required=True)
    result.add_argument("--config-json")
    result.add_argument("--as-of")
    output = result.add_mutually_exclusive_group()
    output.add_argument("--summary", action="store_true")
    output.add_argument("--json", action="store_true")
    result.add_argument("--output")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    committee = CommitteeAssessment.model_validate_json(Path(args.assessment_json).read_text(encoding="utf-8"))
    recommendation = CommitteeRecommendation.model_validate_json(Path(args.recommendation_json).read_text(encoding="utf-8"))
    proposal = PortfolioProposal.model_validate_json(Path(args.proposal_json).read_text(encoding="utf-8"))
    risk_assessment = RiskAssessment.model_validate_json(Path(args.risk_assessment_json).read_text(encoding="utf-8"))
    risk_decision = RiskDecision.model_validate_json(Path(args.risk_decision_json).read_text(encoding="utf-8"))
    config = ChairmanConfig.model_validate_json(Path(args.config_json).read_text(encoding="utf-8")) if args.config_json else None
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    assessment, decision = ChairmanService(config).review(committee, recommendation, proposal, risk_assessment, risk_decision, as_of)
    payload = {"assessment": assessment.model_dump(mode="json"), "decision": decision.model_dump(mode="json")}
    if args.summary:
        body = (
            f"Decision: {decision.decision.value}\nGovernance score: {assessment.governance_score:.3f}\n"
            "research_only=true; suitable_for_live_trading=false; decision_ready=false; execution_authority=false\n"
        )
    else:
        body = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
