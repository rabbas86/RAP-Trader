"""Offline Investment Committee command-line review."""

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.domain.models.analyst import AnalystOpinion
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.committee import CommitteeConfig, InvestmentCommitteeService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="committee", description="Run an offline research-only Investment Committee review")
    result.add_argument("--opinions-json", required=True)
    result.add_argument("--proposal-json", required=True)
    result.add_argument("--risk-assessment-json", required=True)
    result.add_argument("--risk-decision-json", required=True)
    result.add_argument("--policy-json")
    result.add_argument("--as-of")
    output = result.add_mutually_exclusive_group()
    output.add_argument("--summary", action="store_true")
    output.add_argument("--json", action="store_true")
    result.add_argument("--output")
    return result


def _read(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    opinions = TypeAdapter(list[AnalystOpinion]).validate_json(Path(args.opinions_json).read_text(encoding="utf-8"))
    proposal = PortfolioProposal.model_validate_json(Path(args.proposal_json).read_text(encoding="utf-8"))
    assessment = RiskAssessment.model_validate_json(Path(args.risk_assessment_json).read_text(encoding="utf-8"))
    decision = RiskDecision.model_validate_json(Path(args.risk_decision_json).read_text(encoding="utf-8"))
    policy = CommitteeConfig.model_validate_json(Path(args.policy_json).read_text(encoding="utf-8")) if args.policy_json else None
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    committee_assessment, recommendation = InvestmentCommitteeService(policy).review(opinions, proposal, assessment, decision, as_of)
    payload = {
        "assessment": committee_assessment.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json"),
        "alignment": committee_assessment.research_alignment,
        "conflicts": [item.model_dump(mode="json") for item in committee_assessment.conflicts],
        "dissent": [item.model_dump(mode="json") for item in recommendation.dissenting_views],
        "portfolio_review": {"required_modifications": recommendation.required_modifications},
        "risk_outcome": committee_assessment.risk_decision.value,
    }
    if args.summary:
        body = (
            f"Recommendation: {recommendation.recommendation.value}\n"
            f"Committee confidence: {recommendation.confidence:.3f}; alignment: {committee_assessment.research_alignment:.3f}\n"
            "requires_chairman_review=true; research_only=true; suitable_for_live_trading=false; decision_ready=false\n"
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
