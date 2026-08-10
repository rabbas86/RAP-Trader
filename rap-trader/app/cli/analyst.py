"""Run the deterministic, offline research analyst."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models.analyst import AnalystRequest
from app.services.analyst import AnalystService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--analyst-id", "--analyst", dest="analyst_id", default="mock")
    result.add_argument("--ticker", required=True)
    result.add_argument("--timeframe", default="1d", choices=["1m", "5m", "15m", "1h", "1d", "1w"])
    result.add_argument("--start")
    result.add_argument("--end")
    result.add_argument("--lookback", type=int, default=60)
    result.add_argument("--horizon", type=int, default=5)
    result.add_argument("--as-of")
    result.add_argument("--as-json", action="store_true")
    result.add_argument("--asset-class", default="equity")
    result.add_argument("--input-json", help="Optional JSON object merged into the analyst request")
    result.add_argument("--input-fundamentals", help="Path to a CompanyFundamentals JSON document")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC)
    analyst_id = "technical" if args.analyst_id == "technical-analyst" else args.analyst_id
    supplied = json.loads(args.input_json) if args.input_json else {}
    extra_context = {"start": args.start, "end": args.end, **supplied.get("extra_context", {})}
    if analyst_id == "fundamental":
        if not args.input_fundamentals:
            result = parser()
            result.error("--input-fundamentals is required for the fundamental analyst")
        extra_context["fundamentals"] = json.loads(Path(args.input_fundamentals).read_text(encoding="utf-8"))
    request = AnalystRequest(
        analyst_id=analyst_id,
        ticker=args.ticker,
        timeframe=args.timeframe,
        as_of=as_of,
        lookback=args.lookback,
        horizon=args.horizon,
        asset_class=args.asset_class,
        extra_context=extra_context,
    )
    opinion = AnalystService().analyze(request)
    if args.as_json:
        print(opinion.model_dump_json(indent=2))
    else:
        print(f"Direction: {opinion.direction.value}")
        print(f"Confidence: {opinion.confidence.value:.3f} ({opinion.confidence.calibration_note})")
        print(f"Evidence: {len(opinion.evidence)} item(s)")
        for item in opinion.evidence:
            print(f"- [{item.summary.split(':', 1)[0]}] {item.evidence_type.value}: {item.summary}")
        if opinion.analyst_id == "technical":
            analyst = AnalystService().analyst("technical")
            snapshot = analyst.snapshot(request)  # type: ignore[attr-defined]
            structure = snapshot.structure
            print(
                f"Structure: {structure.regime}; BoS={structure.bos_timestamp}; CHoCH={structure.choch_timestamp}; swings={len(snapshot.swing_points)}"
            )
            print(
                "Levels: "
                + ", ".join(
                    f"{level.level_type} {level.price:.4f} touches={level.touch_count} broken={level.broken}" for level in snapshot.levels
                )
            )
        print(f"Assumptions: {[x.description for x in opinion.assumptions]}")
        print(f"Warnings: {[x.message for x in opinion.warnings]}")
        print(f"Limitations: {[x.message for x in opinion.limitations]}")
        print(f"Freshness: stale={opinion.data_freshness.is_stale}, age_seconds={opinion.data_freshness.age_seconds}")
        print(f"Provenance: {[p.source for e in opinion.evidence for p in e.provenance]}")
        print("suitable_for_live_trading=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
