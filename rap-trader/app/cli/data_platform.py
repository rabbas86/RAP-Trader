"""Command-line access to the unified research data platform."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models.data_platform import DataDomain, SnapshotRequest
from app.services.data_platform import DataPlatformService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Query the deterministic offline research data platform")
    result.add_argument("--as-of", default=None, help="Point-in-time cutoff (ISO 8601, UTC)")
    result.add_argument("--domain", choices=[d.value for d in DataDomain], action="append", default=None)
    result.add_argument("--symbol", action="append", default=None)
    result.add_argument("--series", action="append", default=None)
    result.add_argument("--limit", type=int, default=None)
    result.add_argument("--max-records", type=int, default=None)
    result.add_argument("--input-json", help="Optional JSON SnapshotRequest merged with CLI flags")
    output = result.add_mutually_exclusive_group()
    output.add_argument("--json", "--as-json", dest="as_json", action="store_true")
    output.add_argument("--summary", action="store_true")
    output.add_argument("--output", help="Optional output file path for JSON")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(UTC)
    request = SnapshotRequest(
        as_of=as_of,
        domains=tuple(DataDomain(d) for d in args.domain) if args.domain else (),
        symbols=tuple(args.symbol) if args.symbol else (),
        series_ids=tuple(args.series) if args.series else (),
        max_records=args.max_records,
    )
    service = DataPlatformService()
    if args.input_json:
        raw = Path(args.input_json).read_text(encoding="utf-8")
        override = json.loads(raw)
        request = request.model_copy(update={k: v for k, v in override.items() if k in SnapshotRequest.model_fields})
    snapshot = service.snapshot(request)
    if args.output:
        Path(args.output).write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
        return 0
    if args.as_json:
        print(snapshot.model_dump_json(indent=2))
        return 0
    print(f"Snapshot: {snapshot.snapshot_id}")
    print(f"as_of={snapshot.as_of.isoformat()}")
    print(f"Domains: {', '.join(d.value for d in snapshot.requested_domains)}")
    print(f"Records: {len(snapshot.records)}")
    for domain in snapshot.requested_domains:
        count = sum(1 for record in snapshot.records if record.domain == domain)
        print(f"  {domain.value}: {count}")
    print(f"Quality: avg_score={snapshot.quality_summary.average_score:.3f}")
    print(f"Provenance: {', '.join(sorted(snapshot.source_versions))}")
    print("research_only=true suitable_for_live_trading=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
