"""Command-line access to immutable market feature snapshots."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.models.features import FeatureSnapshotRequest
from app.services.features import FeatureService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a deterministic market-intelligence feature snapshot")
    result.add_argument("--ticker", required=True)
    result.add_argument("--timeframe", default="1d", choices=["1m", "5m", "15m", "1h", "1d", "1w"])
    result.add_argument("--as-of")
    result.add_argument("--lookback", type=int, default=100)
    output = result.add_mutually_exclusive_group()
    output.add_argument("--json", "--as-json", dest="as_json", action="store_true")
    output.add_argument("--summary", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC)
    request = FeatureSnapshotRequest(ticker=args.ticker, timeframe=args.timeframe, as_of=as_of, lookback=args.lookback)
    snapshot = FeatureService().snapshot(request)
    if args.as_json:
        print(snapshot.model_dump_json(indent=2))
        return 0
    categories: dict[str, int] = {}
    for item in snapshot.vector.values:
        categories[item.category.value] = categories.get(item.category.value, 0) + 1
    print(f"Snapshot: {snapshot.snapshot_id}")
    print(f"Instrument: {snapshot.ticker} {snapshot.timeframe} as_of={snapshot.as_of.isoformat()}")
    print(f"Features: {len(snapshot.vector.values)} ({', '.join(f'{key}={value}' for key, value in sorted(categories.items()))})")
    print(f"Freshness: stale={str(snapshot.stale).lower()} age_seconds={snapshot.age_seconds:.0f}")
    print(f"Input fingerprint: {snapshot.provenance.input_fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
