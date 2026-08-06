"""Phase 4 CLI: ``python -m app.cli.backtest``

Runs a walk-forward backtest with deterministic, offline defaults.

Defaults:
* Offline market data (MockMarketDataProvider)
* Mock / benchmark Kronos forecasts
* CPU, no network, no model download
* No persistent output unless ``--output-dir`` is specified
* research_only=True, suitable_for_live_trading=False

Usage:
    python -m app.cli.backtest \\
        --ticker AAPL \\
        --timeframe 1d \\
        --start 2025-01-01 \\
        --end 2025-06-01 \\
        --lookback 60 \\
        --horizon 5 \\
        --step 5

No broker, execution, order, risk, or portfolio components are invoked.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import Any

from app.domain.models.backtesting import BacktestRunRequest, BacktestStatus
from app.services.backtesting.runner import BacktestRunner
from app.services.backtesting.store import JSONFileBacktestResultStore
from app.services.market_data import MockMarketDataProvider


def _parse_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime string into a UTC-aware datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.cli.backtest",
        description="Phase 4 deterministic walk-forward backtesting (offline, mock data).",
    )
    parser.add_argument("--ticker", default="AAPL", help="Ticker symbol (default: AAPL)")
    parser.add_argument("--timeframe", default="1d", choices=["1m", "5m", "15m", "1h", "1d", "1w"], help="Bar timeframe")
    parser.add_argument("--start", required=True, help="Start date (ISO 8601, e.g. 2025-01-01)")
    parser.add_argument("--end", required=True, help="End date (ISO 8601, e.g. 2025-06-01)")
    parser.add_argument("--lookback", type=int, default=60, help="Historical lookback bars (default: 60)")
    parser.add_argument("--horizon", type=int, default=5, help="Forecast horizon bars (default: 5)")
    parser.add_argument("--step", type=int, default=5, help="Walk-forward step in bars (default: 5)")
    parser.add_argument("--max-windows", type=int, default=None, help="Maximum windows (default: no cap)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed (default: 42, unused by deterministic providers)")
    parser.add_argument(
        "--include-local-kronos", action="store_true", default=False, help="Include LocalKronosProvider (requires model path)"
    )
    parser.add_argument("--research-simulation", action="store_true", default=False, help="Run research-only signal simulation")
    parser.add_argument("--short-selling", action="store_true", default=False, help="Allow short selling in research sim")
    parser.add_argument("--leverage", type=float, default=1.0, help="Leverage for research sim (default: 1.0)")
    parser.add_argument("--transaction-cost-bps", type=float, default=0.0, help="Transaction cost in bps (default: 0.0)")
    parser.add_argument("--slippage-bps", type=float, default=0.0, help="Slippage in bps (default: 0.0)")
    parser.add_argument("--output-dir", default=None, help="Directory to save JSON result (default: no persistence)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    request = BacktestRunRequest(
        ticker=args.ticker,
        timeframe=args.timeframe,
        start=_parse_datetime(args.start),
        end=_parse_datetime(args.end),
        lookback=args.lookback,
        horizon=args.horizon,
        step=args.step,
        max_windows=args.max_windows,
        seed=args.seed,
        include_local_kronos=args.include_local_kronos,
        research_simulation=args.research_simulation,
        short_selling=args.short_selling,
        leverage=args.leverage,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
    )

    runner = BacktestRunner(market_data_provider=MockMarketDataProvider())

    if args.verbose:
        print(f"Running backtest: {request.ticker} {request.timeframe} {request.start} -> {request.end}", file=sys.stderr)

    result = runner.run(request)

    if result.status == BacktestStatus.FAILED:
        print(f"Backtest FAILED: {result.error}", file=sys.stderr)
        return 1

    # Optionally persist
    if args.output_dir:
        store = JSONFileBacktestResultStore(args.output_dir)
        store.save(result)
        if args.verbose:
            print(f"Saved result to {args.output_dir}/{result.backtest_id}.json", file=sys.stderr)

    # Print summary
    summary: dict[str, Any] = {
        "backtest_id": result.backtest_id,
        "status": result.status.value,
        "windows_total": result.windows_total,
        "windows_evaluated": result.windows_evaluated,
        "research_only": result.research_only,
        "suitable_for_live_trading": result.suitable_for_live_trading,
        "providers": [
            {
                "provider": p.provider,
                "mean_mae": p.mean_metrics.mae,
                "mean_rmse": p.mean_metrics.rmse,
                "directional_accuracy": p.mean_metrics.directional_accuracy,
                "hit_rate": p.mean_metrics.hit_rate,
                "correlation": p.mean_metrics.correlation,
                "sample_count": p.mean_metrics.sample_count,
                "warning": p.warning,
            }
            for p in result.providers
        ],
        "regime_distribution": result.regime_distribution,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
