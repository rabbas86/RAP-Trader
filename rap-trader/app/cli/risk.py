"""Offline portfolio risk-review command line interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskConstraintSet
from app.services.risk import RiskOfficerService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="risk", description="Review a research-only portfolio proposal offline")
    result.add_argument("--proposal-json", required=True)
    result.add_argument("--history-json")
    result.add_argument("--liquidity-json")
    result.add_argument("--constraints-json")
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
    proposal = PortfolioProposal.model_validate_json(Path(args.proposal_json).read_text(encoding="utf-8"))
    if args.as_of and datetime.fromisoformat(args.as_of) != proposal.as_of:
        raise ValueError("--as-of must equal the proposal as_of timestamp")
    history = [HistoricalBarsResult.model_validate_json(json.dumps(item)) for item in _read(args.history_json)] if args.history_json else []
    liquidity: dict[str, dict[str, float]] = _read(args.liquidity_json) if args.liquidity_json else {}
    constraints = (
        RiskConstraintSet.model_validate_json(Path(args.constraints_json).read_text(encoding="utf-8")) if args.constraints_json else None
    )
    assessment, decision = RiskOfficerService().review(proposal, history, liquidity, constraints)
    if args.summary:
        body = (
            f"Risk assessment {assessment.assessment_id}\n"
            f"Decision: {decision.decision.value}; score: {assessment.overall_risk_score:.2f}; breaches: {len(assessment.breaches)}\n"
            "research_only=true; suitable_for_live_trading=false; decision_ready=false\n"
        )
    else:
        body = (
            json.dumps(
                {"assessment": assessment.model_dump(mode="json"), "decision": decision.model_dump(mode="json")}, indent=2, sort_keys=True
            )
            + "\n"
        )
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
