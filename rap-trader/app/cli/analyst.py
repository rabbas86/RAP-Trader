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
    result.add_argument("--input-snapshot", help="Path to a ResearchDataSnapshot JSON document (for the macro and news analysts)")
    result.add_argument("--input-events", help="Path to a JSON array of EventRecord documents (for the news analyst)")
    result.add_argument("--max-events", type=int, default=0, help="Optional maximum number of events to process (news analyst)")
    result.add_argument("--entity-filter", default=None, help="Optional entity filter for news events")
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
    if analyst_id == "macro":
        if not args.input_snapshot:
            result = parser()
            result.error("--input-snapshot is required for the macro analyst")
        extra_context["snapshot"] = json.loads(Path(args.input_snapshot).read_text(encoding="utf-8"))
        if args.asset_class == "equity":
            args.asset_class = "macro"
    if analyst_id == "news":
        if not args.input_snapshot and not args.input_events:
            result = parser()
            result.error("--input-snapshot or --input-events is required for the news analyst")
        if args.input_snapshot:
            extra_context["snapshot"] = json.loads(Path(args.input_snapshot).read_text(encoding="utf-8"))
        if args.input_events:
            extra_context["events"] = json.loads(Path(args.input_events).read_text(encoding="utf-8"))
        if args.max_events and args.max_events > 0:
            extra_context["max_events"] = args.max_events
        if args.entity_filter:
            extra_context["entity_filter"] = args.entity_filter
        if args.asset_class == "equity":
            args.asset_class = "macro"
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
        if opinion.analyst_id == "macro":
            regimes = [item.summary for item in opinion.evidence if "regime" in item.summary.lower()]
            if regimes:
                print(f"Regime: {regimes[0]}")
        if opinion.analyst_id == "news":
            orientations = [item.summary for item in opinion.evidence if "orientation" in item.summary]
            if orientations:
                print(f"News orientation: {orientations[0].split('|')[-1].strip()}")
            print("Event clusters: " + ", ".join(f"{item.summary.split(':')[0]}={item.strength.value}" for item in opinion.evidence))
        print(f"Assumptions: {[x.description for x in opinion.assumptions]}")
        print(f"Warnings: {[x.message for x in opinion.warnings]}")
        print(f"Limitations: {[x.message for x in opinion.limitations]}")
        print(f"Freshness: stale={opinion.data_freshness.is_stale}, age_seconds={opinion.data_freshness.age_seconds}")
        print(f"Provenance: {[p.source for e in opinion.evidence for p in e.provenance]}")
        print("suitable_for_live_trading=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
