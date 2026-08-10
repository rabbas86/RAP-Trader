# Architecture

Technical analysis separates indicator orchestration, causal structure, level clustering, and deterministic synthesis. A trace DAG links requests and sources through evidence to research opinions.

RAP Trader is a modular monolith. API routes translate HTTP requests; domain models define contracts; services isolate market data, analysis, decisions, risk, portfolio state, brokerage, and execution.

Phase 2's boundary is `MarketDataProvider`. `MockMarketDataProvider` deterministically generates a bounded number of synthetic bars without network access. `YFinanceMarketDataProvider` is opt-in, maps and validates public responses, localizes source timestamps using exchange-timezone policy, applies bounded retries/timeouts, and translates failures into stable safe errors. Both use `AbstractCache`; SHA-256 keys include provider identity, adjustment/session semantics, and provider configuration to prevent cross-provider and cross-policy collisions. The default in-memory implementation provides TTL expiry and bounded LRU eviction.

Market-data models reject naive timestamps, normalize aware timestamps to UTC, support class-share symbols, and validate ranges, OHLC relationships, finite prices, volume, ordering, and duplicates. Successful results cannot be empty and carry complete range, policy, and retrieval provenance. Provider health is structured and intentionally non-invasive. The read-only market-data API has no connection to decisions, risk, execution, or brokerage.

Phase 3 adds `OfflineKronosService`, which consumes normalized historical bars from a `MarketDataProvider` and applies a deterministic simple-moving-average (SMA) crossover strategy (5-period short vs 20-period long SMA). It produces `KronosPrediction` results that include bar provenance (timeframe, source_provider, data_start, data_end). The service is fully offline, deterministic for identical inputs, and `LIVE_TRADING_SUITABLE = False`. Predictions are cached using the same `AbstractCache` and `cache_key_builder` pattern as market data, with provider-type isolation to prevent cross-provider cache collisions. The `KronosService` ABC signature was extended with optional `timeframe`, `start`, `end`, and `limit` parameters (all with safe defaults) while preserving the existing `predict(ticker)` contract. The `MockKronosService` was updated to populate the new provenance fields on `KronosPrediction`. The read-only Kronos API exposes `/kronos/health` and `/kronos/prediction`; it does not submit orders or connect to brokers. Provider failures or insufficient history fail closed to a zero-confidence `FLAT` forecast.

The future Kronos data flow is: API endpoint -> `OfflineKronosService` -> `MarketDataProvider.get_bars` -> normalized `HistoricalBarsResult` -> SMA computation -> `KronosPrediction` (cached). Future evidence-fusion and backtesting phases may consume these predictions; the execution path continues to require deterministic risk approval before `ExecutionService` can reach a broker adapter.

Phase 4 adds `app/services/backtesting/` — a deterministic, offline, walk-forward backtesting framework. `BacktestRunner` fetches historical bars from a `MarketDataProvider`, splits them into non-overlapping evaluation windows via `EvaluationWindowGenerator`, and for each window runs forecast providers through `BacktestEngine`. The engine enforces hard runtime no-lookahead guards: forecast timestamps cannot overlap context bars, must match expected target timestamps exactly, and no bar beyond `context_end` is ever returned. `ForecastEvaluator` computes deterministic accuracy metrics (MAE, RMSE, SMAPE, correlation, directional accuracy, hit rate, interval coverage). `MarketRegimeClassifier` labels each window. When research simulation is enabled, `ResearchSignalSimulator` generates LONG/SHORT/FLAT signals with cost/slippage attribution. Results persist via `InMemoryBacktestResultStore` or `JSONFileBacktestResultStore` (atomic writes, schema versioning, path-traversal protection). All results carry `research_only=True` and `suitable_for_live_trading=False`. The backtesting API exposes `POST /backtests/run`, `GET /backtests/providers`, `GET /backtests/{id}`, and `GET /backtests/{id}/summary`. The CLI (`python -m app.cli.backtest`) runs fully offline with mock data and benchmark providers. No broker, execution, order, risk, or portfolio components are invoked. See `docs/BACKTESTING.md` and `docs/phases/PHASE_04_BACKTESTING.md`.

Phase 1 includes only an in-memory paper simulator with process-local state and no real-broker adapter. Future adapters must preserve idempotency, environment controls, and paper/live separation. Production readiness also requires an immutable audit trail without secrets.
# Phase 5 analyst boundary

The analyst layer depends only on domain contracts and its local opinion infrastructure. It has no dependency on risk, portfolio, broker, execution, committee, or chairman components. API and CLI callers receive research opinions; descriptive aggregation cannot promote them to decisions. Deferred committee fusion is isolated in `app/experimental` and has no production imports.

# Phase 6.5 — Market Intelligence Feature Platform boundary

The Market Intelligence Feature Platform (MIFP) is the canonical, deterministic source of engineered technical features. `FeatureService` consumes normalized bars from a Phase 2 `MarketDataProvider`, runs registered feature generators in topological (dependency) order, and produces an immutable `FeatureSnapshot` with full provenance.

## FeatureService pipeline

```
MarketDataProvider.get_bars()
  → HistoricalBarsResult (validated, UTC-normalized, OHLC-checked)
  → FeatureRegistry.compute_many() (topological sort over FeatureDependencyGraph)
  → FeatureValue[] (each with observed_at, available_at, generated_at, source_fingerprint)
  → ProvenanceRecorder.build() (source fingerprint, generator versions, dependency graph)
  → build_cache_key() (SHA-256 key encoding ticker, timeframe, provider, adjustment,
     session, as_of, lookback, configuration_hash, schema_version, kronos_fp, backtest_fp)
  → FeatureSnapshotCache.set() (immutable, frozen, deeply-validated)
```

## Data flow boundaries

* **No lookahead.** `FeatureSnapshot` model validator rejects any feature whose `available_at > as_of`. For price-derived features, `available_at` equals the bar timestamp. For external features (Kronos, backtests), `available_at` reflects when the external information became available.
* **Immutable snapshot.** `FeatureSnapshot` is frozen (`strict=True`, `frozen=True`, `extra="forbid"`). Tests verify that mutation raises `ValidationError` and that serialization round-trips identically.
* **Deterministic identity.** Cache keys are SHA-256 hashes of all computation-relevant dimensions. Different lookbacks, Kronos inputs, backtest inputs, or schema versions never collide.
* **No external dependencies.** MIFP has no imports of broker, execution, risk, portfolio, committee, or chairman components. No LLM, no network, no model download, no credentials.

See `docs/FEATURE_PLATFORM.md` and `docs/phases/PHASE_06_5_FEATURE_PLATFORM.md`.

## Technical Analyst integration

The Phase 6 Technical Analyst (`app/services/technical_analysis/service.py`) consumes `FeatureSnapshot` from `FeatureService` as its sole feature source. The normal analysis path (`analyze()`) never independently recalculates SMA, EMA, RSI, MACD, ATR, Bollinger, OBV, VWAP, market structure, or support/resistance — all values are read from the immutable snapshot. See `docs/TECHNICAL_ANALYST.md`.

# Phase 7 — Fundamental Analyst

The Phase 7 Fundamental Analyst (`app/services/fundamental_analysis/service.py`) is an offline, deterministic, research-only analyst that consumes caller-supplied point-in-time `CompanyFundamentals` and produces the shared Phase 5 `AnalystOpinion`. It depends on:

- `app/domain/models/fundamental.py` — strict financial statement models
- `app/services/fundamental_analysis/` — normalization, analysis, evidence, synthesis
- `app/domain/models/analyst.py` — shared `Analyst` ABC, `EvidenceItem`, `AnalystOpinion`
- `app/services/analyst/service.py` — analyst registry (auto-registers `"fundamental"`)

The fundamental analyst does **not** consume MIFP `FeatureSnapshot` — accounting analysis is independent of engineered market features. Market-derived inputs (price, market cap, EV) are optional and caller-supplied in the `CompanyFundamentals` payload. See `docs/FUNDAMENTAL_ANALYST.md`.
# Phase 7.5 analyst lifecycle

The canonical analyst framework lives in `app/services/analyst/framework/`. `BaseAnalyst` coordinates Validation → Normalization → Analysis → Evidence → Confidence → Opinion → Trace → Output, while specialist packages own only their domain calculations and evidence semantics. Shared services are re-exported through the legacy analyst service module.
