"""Offline portfolio proposal command line interface."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from app.services.portfolio import PortfolioManagerService, PortfolioProposalRequest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="portfolio", description="Build deterministic research-only portfolio weights offline")
    result.add_argument("--portfolio-json", required=True)
    result.add_argument("--opinions-json", required=True)
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
    payload: dict[str, Any] = {"portfolio": _read(args.portfolio_json), "opinions": _read(args.opinions_json)}
    if args.constraints_json:
        payload["constraints"] = _read(args.constraints_json)
    if args.as_of:
        payload["as_of"] = datetime.fromisoformat(args.as_of)
    request = PortfolioProposalRequest.model_validate_json(json.dumps(payload, default=lambda value: value.isoformat()))
    proposal = PortfolioManagerService().propose(request)
    if args.summary:
        body = (
            f"Portfolio proposal {proposal.proposal_id}\n"
            f"Positions: {len(proposal.positions)}; cash: {proposal.cash_weight:.6f}; turnover: {proposal.turnover:.6f}\n"
            "research_only=true; suitable_for_live_trading=false; decision_ready=false\n"
        )
    else:
        body = proposal.model_dump_json(indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
