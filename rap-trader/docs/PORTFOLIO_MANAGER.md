# Portfolio Manager

Phase 10 is a deterministic, offline research component that converts point-in-time analyst opinions into constrained portfolio weights. Its only output is an immutable `PortfolioProposal`; it never creates trades, orders, or execution instructions.

## Pipeline

1. Reject duplicate and future opinions and ignore insufficient-evidence opinions.
2. Map directions to signed orientations (`BULLISH=+1`, `BEARISH=-1`, neutral/mixed `=0`).
3. Scale each contribution by confidence, freshness, and evidence quality.
4. Aggregate bounded asset conviction and agreement statistics.
5. Allocate by absolute conviction, then apply position, group, cash, exposure, count, short, and turnover constraints.
6. Record input/config/constraint fingerprints, opinion IDs, algorithm version, Git commit, every adjustment, and a valid construction DAG.

The implementation does not use network access, generative models, downloaded models, brokers, execution services, order requests, or the risk engine. Correlations use only supplied bars at or before `as_of`; missing or insufficient observations produce no estimate.

## API and CLI

The API exposes `GET /portfolio/health`, `GET /portfolio/metadata`, `POST /portfolio/validate`, and `POST /portfolio/propose`. There are deliberately no execute, rebalance, or order routes.

Run offline with:

```powershell
python -m app.cli portfolio --portfolio-json portfolio.json --opinions-json opinions.json --constraints-json constraints.json --as-of 2026-01-01T00:00:00+00:00 --json
```

Use `--summary` for a compact report and `--output PATH` to write the result. Identical inputs produce the same proposal ID and JSON payload except that health-check timestamps are observational.

## Safety semantics

All portfolio contracts are strict and immutable. Their safety flags are fixed to `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`. Weights and confidence values reject non-finite numbers. Snapshot and proposal timestamps cannot be in the future.

