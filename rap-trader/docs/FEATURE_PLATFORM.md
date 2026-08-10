# Phase 6.5 — Market Intelligence Feature Platform

## Overview

Phase 6.5 introduces the Market Intelligence Feature Platform (MIFP) as the
canonical, deterministic source of engineered technical features.  The
platform produces an **immutable `FeatureSnapshot`** that the Phase 6 Technical
Analyst consumes to build evidence-based research opinions.

Key principles:

* **No formula duplication.**  Indicator formulas live exclusively in the
  feature generators (`app/services/features/generators/`).  The Technical
  Analyst reads deterministic outputs from `FeatureSnapshot` rather than
  re-running indicator calculations independently.
* **No lookahead.**  Every `FeatureValue` carries `observed_at`,
  `available_at`, and `generated_at`.  The `FeatureSnapshot` model validator
  rejects any feature whose `available_at` is later than the snapshot's
  `as_of`.
* **Cache identity safety.**  Cache keys encode lookback window, schema
  version, Kronos/backtest fingerprints, and a market-data configuration
  hash so that different inputs never collide.
* **Research-only.**  All opinions carry `research_only=True` and
  `suitable_for_live_trading=False`.  No orders, quantities, allocations,
  or real-broker connections are ever made.

## Architecture

```
MarketDataProvider (Phase 2)
       │
       ▼
FeatureService.snapshot()
       │
       ├── HistoricalBarsRequest ──► HistoricalBarsResult
       ├── FeatureRegistry.compute_many()   (topological sort)
       ├── ProvenanceRecorder.build()
       └── FeatureSnapshotCache.set()       (deterministic SHA-256 key)
       │
       ▼
FeatureSnapshot (immutable, frozen, validated)
       │
       ▼
TechnicalAnalyst.analyze()      (Phase 6)
       │
       ├── _snapshot_from_features()
       │     └── FeatureSnapshot → TechnicalAnalysisSnapshot
       ├── _evidence_from_features()
       │     └── FeatureSnapshot values → EvidenceItem[]
       └── TechnicalEvidenceSynthesizer
       │
       ▼
AnalystOpinion (research_only=True, suitable_for_live_trading=False)
```

## Feature snapshot contract

`FeatureSnapshot` is a frozen Pydantic model (`strict=True`, `frozen=True`,
`extra="forbid"`).  It is immutable after construction.  Key fields:

| Field | Type | Description |
|---|---|---|
| `snapshot_id` | `str` | Deterministic UUID v5 (namespace `NAMESPACE_URL`) |
| `ticker` | `str` | Uppercase symbol (1–10 chars) |
| `timeframe` | `Timeframe` | `1m`, `5m`, `15m`, `1h`, `1d`, `1w` |
| `as_of` | `UtcDatetime` | Evaluation time — no feature may be available later |
| `generated_at` | `UtcDatetime` | When this snapshot was materialized |
| `bars_analyzed` | `int` | Number of source bars consumed |
| `vector` | `FeatureVector` | Sorted, deduplicated tuple of `FeatureValue` |
| `provenance` | `FeatureProvenance` | Full lineage + input fingerprint |
| `stale` | `bool` | Whether the source data exceeded the freshness SLA |
| `age_seconds` | `float` | Age of source data in seconds |

### FeatureValue availability model

Each `FeatureValue` records three timestamps:

* **`observed_at`** — when the source data point (bar) was observed.  For
  price-derived features this is the timestamp of the bar itself.
* **`available_at`** — when the information became known and could legally
  be used without lookahead.  This is ≤ `observed_at` for source-bar features
  and equals the forecast generation time for Kronos features.
* **`generated_at`** — when the feature was computed by its generator.

The invariant chain is: `available_at ≤ observed_at ≤ generated_at`.

For **external features** (Kronos forecasts, backtest results),
`available_at` reflects when that external information became available —
not when the raw bar was observed.  This prevents the analyst from
implicitly assuming future forecasts.

## Cache key design

`build_cache_key()` in `app/services/features/cache.py` constructs a
`FeatureCacheKey` with deterministic SHA-256 hashing.  Every dimension that
affects the computation result is encoded:

| Dimension | Source |
|---|---|
| `ticker` | request |
| `timeframe` | request |
| `provider` | market-data provider identity |
| `adjustment` | adjustment policy |
| `session` | session policy |
| `as_of` | request |
| `lookback` | request |
| `configuration_hash` | request configuration tuple |
| `schema_version` | `FEATURE_SCHEMA_VERSION` |
| `kronos_fingerprint` | SHA-256 of Kronos forecast payload |
| `backtest_fingerprint` | SHA-256 of backtest metrics payload |

Only the **fingerprint** (hash) of variable-length payloads is stored in
the key — never the raw payload.  This means different Kronos forecasts or
backtest results with different content will never collide, but the cache
key itself never leaks sensitive payload data.

## Dependency graph

The `FeatureDependencyGraph` (backed by `FeatureRegistry`) records the
lineage of every feature.  Dependencies are **lineage dependencies** —
they describe which upstream features were consumed to produce a given
feature, not just execution ordering.

Direct dependencies include:

| Feature | Dependencies |
|---|---|
| `price.return_1` | `price.close` |
| `price.typical` | `price.high`, `price.low`, `price.close` |
| `trend.sma_*` | `price.close` |
| `trend.ema_*` | `price.close` |
| `momentum.macd` | `trend.ema_12`, `trend.ema_26` |
| `momentum.macd_signal` | `momentum.macd` |
| `momentum.macd_histogram` | `momentum.macd`, `momentum.macd_signal` |
| `volatility.bollinger_middle` | `trend.sma_20` |
| `volatility.bollinger_upper` | `trend.sma_20`, `price.close` |
| `volatility.bollinger_lower` | `trend.sma_20`, `price.close` |
| `volatility.atr_14` | `volatility.true_range` |
| `volatility.true_range` | `price.high`, `price.low`, `price.close` |
| `volume.relative_20` | `volume.average_20`, `price.close` |
| `structure.*` | `price.close` |
| `support_resistance.*` | `price.close` |
| `kronos.*`, `backtest.*` | `price.close` |

The graph is validated for acyclicity and reference integrity at
registration time.

## Versioning

| Constant | Value | Purpose |
|---|---|---|
| `FEATURE_SCHEMA_VERSION` | `"1.0.0"` | Model shape versioning |
| `PLATFORM_VERSION` | `"mifp-6.5.0"` | Platform identity |
| `GENERATOR_VERSION` | `"1.0.0"` | Bundled generator version |

These are embedded in:
* `FeatureMetadata.schema_version` and `platform_version`
* `FeatureProvenance.feature_schema_version` and `platform_version`
* `FeatureCacheKey.schema_version`

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/features/health` | Health check |
| `GET` | `/features/categories` | List feature categories |
| `POST` | `/features/snapshot` | Retrieve a `FeatureSnapshot` |

## CLI

```shell
python -m app.cli.features --ticker AAPL --as-of 2026-08-01T00:00:00+00:00 --summary
python -m app.cli.features --ticker AAPL --as-of 2026-08-01T00:00:00+00:00 --json
```

## Safety guarantees

* No broker, execution, risk, portfolio, committee, or Chairman imports.
* No LLM, model, or network access in the default offline path.
* No API keys, tokens, passwords, or secrets stored or logged.
* `research_only=True` and `suitable_for_live_trading=False` on every opinion.
* Future timestamps are rejected at the model and snapshot level.

## Phase 7 — Fundamental Analyst (non-MIFP)

The fundamental analyst is a standalone analyst that does **not** consume MIFP
`FeatureSnapshot`. It reads caller-supplied financial statements from the
`CompanyFundamentals` input contract and produces Phase 5 evidence/opinion
output. Market-derived inputs (price, market cap, enterprise value) may be
supplied directly in the fundamentals JSON; they are never fetched from MIFP or
any external source. Phase 8A introduces a Unified Research Data Platform that
can supply normalized, point-in-time-safe data records (including fundamental
filings via its `FundamentalsAdapter`) that feed the `CompanyFundamentals` input
contract. See `docs/DATA_PLATFORM.md`. The data platform does not duplicate MIFP
feature computation. See `docs/FUNDAMENTAL_ANALYST.md`.
# Phase 10 integration

The portfolio manager does not fetch or generate features. Analyst opinions may be based on feature snapshots, but Phase 10 consumes only the frozen opinion contract and records its provenance.
