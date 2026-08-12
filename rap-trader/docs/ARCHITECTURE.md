# Architecture

The research-governance hierarchy is specialist research -> Portfolio Manager proposal -> Risk Officer decision -> Investment Committee recommendation -> Chairman decision. The Chairman can make an outcome more conservative but cannot override risk, bypass committee review, or reach brokerage/execution.

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

# Phase 7.5 — Phase 8A boundary: Unified Research Data Platform

The Unified Research Data Platform (Phase 8A) is a deterministic, offline, read-only data layer that normalizes, versions, and serves research data from market, fundamental, macro, calendar, and news/event domains. It sits between Phase 7.5 analysts and Phase 6.5 MIFP:

```
Caller-supplied raw data
       │
       ▼
  Adapters (market, fundamentals, macro, events, news, mock)
       │  — normalize, fingerprint, assign source identity
       ▼
  DataNormalizationService (one canonical pass)
       │
       ▼
  InMemoryDataRecordStore / JSONFileDataRecordStore
       │  — query by domain, entity, series, period, as_of, revision
       ▼
  DataQualityService + PointInTimeRevisionService
       │  — quality scoring, revision lineage, no-lookahead enforcement
       ▼
  ResearchDataSnapshotService → ResearchDataSnapshot
       │  — immutable, deterministic, point-in-time safe
       ▼
  MIFP FeatureSnapshot (Phase 6.5)  OR  Phase 7.5 Analysts
```

The data platform produces `ResearchDataSnapshot` as the canonical point-in-time-safe input. MIFP (Phase 6.5) and Phase 7.5 analysts consume these snapshots. The data platform does not duplicate any MIFP feature computation and does not rewrite Technical or Fundamental analysts. It provides lightweight helpers/adapters so future analysts can consume `ResearchDataSnapshot`.

Key boundaries:
- **No data platform → analyst opinion.** The platform provides data, not analysis.
- **No data platform → MIFP feature computation.** The snapshot is the input; MIFP computes features.
- **No data platform → broker/execution/risk/portfolio.** Read-only, offline, research-only.
- **No data platform → network/LLM/model download.** All adapters are offline-capable by default.

See `docs/DATA_PLATFORM.md` and `docs/phases/PHASE_08A_UNIFIED_DATA_PLATFORM.md`.
# Phase 8B — Macro Economist

The Phase 8B Macro Economist (`app/services/macro_analysis/`) is a deterministic, offline, research-only specialist analyst. It consumes `ResearchDataSnapshot` from the Phase 8A platform and produces Phase 5 `AnalystOpinion` objects through the Phase 7.5 lifecycle. It auto-registers as `"macro"` in `AnalystService` and exposes `GET /analysts/macro/health`, `GET /analysts/macro/metadata`, `POST /analysts/macro/analyze`, and `python -m app.cli.analyst --analyst macro`.

The analyst is structured around 8 domain-specific specialist services (inflation, growth, employment, liquidity, monetary policy, yield curve, credit, business cycle), each no larger than ~60 lines, that produce deterministic `MacroSignal` values. `MacroRegimeService` fuses these into a single `MacroRegime` using a priority-ordered rule set. `MacroEvidenceFactory` converts signals into Phase 5 `EvidenceItem` records. `MacroOpinionSynthesisService` maps regime + signals to an `AnalysisDirection` with conflict-aware confidence.

Key safety properties: `research_only=true`, `suitable_for_live_trading=false`, `decision_ready=false` on every opinion; no network/LLM/model-download/broker/execution/risk/portfolio/committee imports; point-in-time via `ResearchDataSnapshotService.create_snapshot()` which applies `PointInTimeRevisionService.select()`. See `docs/MACRO_ECONOMIST.md` and `docs/phases/PHASE_08B_MACRO_ECONOMIST.md`.

# Phase 9 — News Analyst

The Phase 9 News Analyst (`app/services/news_analysis/`) is a deterministic, offline, research-only specialist analyst. It consumes event/news data from the Phase 8A Unified Research Data Platform (via `extra_context.events` or `extra_context.snapshot`) and produces Phase 5 `AnalystOpinion` objects through the Phase 7.5 lifecycle. It auto-registers as `"news"` in `AnalystService` and exposes `GET /analysts/news/health`, `GET /analysts/news/metadata`, `POST /analysts/news/analyze`, and `python -m app.cli.analyst --analyst news`.

The analyst is structured around 12 modular services: classification, source quality, novelty, decay, confirmation, event grouping, evidence, materiality, lifecycle, and synthesis. `NewsMaterialityService` scores whether an event carries enough signal. `EventLifecycleService` handles cancelled/superseded/resolved/archived events. `NewsDecayService` computes event-type-specific relevance decay (separate from the platform's generic `DataFreshnessService`). `NewsOpinionSynthesisService` combines classified events into a direction with metric-aware negative signal detection (adapting the Phase 7 pattern).

Key safety properties: `research_only=True`, `suitable_for_live_trading=False`, `decision_ready=False`; no network/LLM/model-download/broker/execution/risk/portfolio/committee imports; point-in-time filtering at extraction boundary with evidence validation as defense-in-depth; `generated_at = as_of` and `data_freshness.evaluated_at = as_of`; deterministic opinion IDs. See `docs/NEWS_ANALYST.md` and `docs/phases/PHASE_09_NEWS_ANALYST.md`.
# Phase 10 portfolio boundary

The portfolio manager consumes immutable analyst opinions and current research positions. Its deterministic pipeline ends at `PortfolioProposal`; it is isolated from broker, execution, order, and risk-engine services. See [Portfolio Manager](PORTFOLIO_MANAGER.md).
# Phase 11 risk boundary

The Phase 11 Risk Officer is implemented as deterministic, offline portfolio-proposal research review. It is separate from the Phase 1 `RiskEngine`, which provides execution/trade-level safety. Investment Committee and Chairman remain future phases.

`app/services/risk/` is an offline portfolio-review component downstream of the Portfolio Manager. It is separate from the Phase 1 `risk_engine`, consumes proposals read-only, and has no dependency on execution or governance components. Pure calculators feed limit evaluation, stress testing, an assessment, and a non-actionable research decision.
## Phase 12 Investment Committee

The offline committee sits after specialist research, portfolio construction, and Risk Officer review. Focused services assemble the research case, measure alignment, classify conflicts, preserve dissent, review portfolio implications, enforce risk precedence, create questions, and synthesize deterministic outputs. SHA-256 provenance and a UUID5 acyclic trace preserve every source. The layer has no dependency on broker, execution, order, risk-engine, Chairman, network, or model services.

## Canonical research runs

`ResearchRun` is the frozen, versioned identity and lifecycle contract for a complete decision run. `RunEvent` is its immutable, append-only causal record: positive sequence numbers and prior-event hashes form a verifiable chain, while correlation and causation IDs preserve provenance. UUID5 identities and SHA-256 fingerprints are derived only from normalized canonical content, including UTC timestamps and type-aware set ordering. Both contracts permanently enforce research-only, paper-only operation and `suitable_for_live_trading=false`.

# Phase 15C — Durable Artifact Store

Phase 15C adds durable persistence for the immutable `ArtifactEnvelope` contract defined in Phase 15B. This phase introduces a storage-backend-independent `ArtifactStore` abstraction with two canonical implementations: `InMemoryArtifactStore` for deterministic tests/development, and `FileArtifactStore` for durable local persistence.

## ArtifactStore responsibilities

`ArtifactStore` owns durable persistence, verified retrieval, deterministic listing, and direct provenance resolution. It does not modify `ArtifactEnvelope` contracts or payload schemas. Persistence does not confer execution authority, decision readiness, or live-trading suitability.

## Immutable append-only semantics

Artifacts are immutable once written. The store never mutates persisted content. Writes are content-addressed by deterministic `artifact_id`; any material change produces a new artifact identity.

## Idempotent writes

Repeating an identical `put()` for an existing `artifact_id` is a no-op and returns the existing artifact. No duplicate copies are created.

## Conflict rejection

A write that would replace an existing artifact with different bytes is rejected. The store fails closed instead of silently overwriting.

## Corruption detection

Every read verifies the persisted envelope:

1. valid `ArtifactEnvelope` JSON/model structure
2. valid `artifact_id` format
3. valid `payload_hash` format
4. payload still hashes to `payload_hash`
5. recomputed deterministic artifact identity equals persisted `artifact_id`
6. supported `schema_version`
7. valid non-empty provenance

If any check fails, the store raises `ArtifactCorruptedError` and does not return corrupted content.

## Atomic persistence

`FileArtifactStore` writes via `tempfile.mkstemp` + `os.replace`. Partial/temporary files are never exposed as final artifacts, and conflicting writes do not silently replace existing artifacts.

## Deterministic listing/query

`list_ids()` returns stable, sorted artifact IDs. Optional bounded filters (`artifact_type`, `logical_as_of`, `producer_version`) allow deterministic queries without turning the store into a general database.

## Direct provenance resolution

`get_direct_dependencies(artifact_id)` resolves only direct provenance references of kind `artifact`. It does not recursively walk a full DAG; full dependency reconstruction belongs to a later replay phase.

## Backend independence

The `ArtifactStore` interface is storage-backend independent. Future backends (object storage, SQL, ClickHouse) can implement the same contract without changing callers.

## Relationship to future replay

Phase 15C ends at durable artifact persistence and basic provenance retrieval. It does not implement `DecisionRun`, full replay DAGs, outcome attribution, broker integration, execution routing, or live trading.

# Phase 15B — Immutable Artifact Envelope and Canonical Artifact Hashing

Phase 15B adds the immutable artifact contract used to wrap durable research and paper-trading outputs in a replayable, auditable envelope. This phase defines the boundary and identity rules only; persistence, repositories, ledgers, and execution engines remain out of scope.

## Artifact boundary

All top-level RAP-Trader research or paper-trading outputs that need durable identity, lineage, or replay are wrapped in `ArtifactEnvelope`. The envelope never modifies the underlying payload schema; it stores metadata, provenance, and a SHA-256 payload hash derived from Phase 15A canonical bytes.

Included artifact types:
- `research_data_snapshot`
- `trade_decision`
- `historical_bars_result`
- `backtest_summary`
- `research_run`
- `run_event`

## Artifact inventory

| artifact | status | rationale |
| --- | --- | --- |
| `ResearchDataSnapshot` | included | canonical point-in-time input across data platform, features, analysts, backtests, and run events |
| `TradeDecision` | included | final deterministic output of the decision pipeline; must be replayable and auditable |
| `HistoricalBarsResult` | included | input boundary for analysis and backtesting; carries provider, session, adjustment, and lookahead-safe provenance |
| `BacktestSummary` | included | deterministic offline backtesting output used for research opinion and signal history |
| `ResearchRun` | included | top-level research run identity; envelopes preserve versioned run metadata in durable artifacts |
| `RunEvent` | included | causal event log for a run; envelope preserves prior-event hash chain and event provenance |
| `FeatureSnapshot` | excluded | MIFP internal state is already immutable and cached by deterministic identity; envelope boundary is deferred until feature replay is required |
| `FundamentalSnapshot` | excluded | fundamental input wrapper is already deterministic and consumed through `CompanyFundamentals`; canonicalization and replay are owned by the fundamental analyst boundary |
| `AnalystOpinion` / macro/news opinions | excluded | Phase 7.5 lifecycle outputs are already traceable and research-only; envelope boundary should be added at opinion persistence, not in the domain contract |
| `KronosPrediction` | excluded | deterministic, cached, research-only prediction output; envelope boundary is deferred to forecast persistence/replay |
| portfolio, risk, broker, execution outputs | excluded | paper/live execution, portfolio construction, broker integration, and live risk belong to later phases |

## Canonical payload hashing

`ArtifactEnvelope` relies on the approved Phase 15A canonical implementation in `app/domain/canonical.py`. The payload hash is `sha256_fingerprint(payload)`, which canonicalizes the payload to deterministic JSON bytes before hashing. Supported canonical content includes timestamps, enums, tuples, mappings, nested Pydantic models, and deterministic `set`/`frozenset` values. Do not introduce a second canonical JSON implementation.

## Artifact identity

`artifact_id` is deterministic and calculated only after validation and normalization. Identity material includes:
- `artifact_type`
- `schema_version`
- `logical_as_of`
- `producer_version`
- `payload_hash`
- normalized provenance references

Identity material is canonicalized and then hashed with SHA-256. The same immutable artifact material always yields the same `artifact_id`; any identity-bearing change produces a different `artifact_id`. Do not use `uuid4`; prefer content-addressed SHA-256 IDs for artifacts.

## Mandatory provenance

`ArtifactEnvelope` never silently accepts unknown provenance. Empty or missing provenance fails validation. Blank or malformed `ProvenanceReference` fields fail validation. `ProvenanceReferenceKind` restricts upstream sources to typed categories such as `research_run`, `artifact`, `research_data_snapshot`, `source_dataset`, `model_input`, or `deterministic_source`. If a source-less artifact must exist, represent it with an explicit typed provenance form rather than an empty collection.

## Replay and audit purpose

Envelopes make research and paper-trading outputs durable and replayable without changing existing domain schemas. They preserve existing canonical fingerprint behavior and research-only/paper-only safety controls. `verify_payload()` recomputes the canonical payload hash and compares it to the stored hash; tampered payloads are rejected.

## Non-goals

Phase 15B does not implement persistence, storage, databases, event replay engines, paper execution, portfolio construction, broker integration, live trading, experiment tracking, or distributed artifact repositories. Those remain future phases.
