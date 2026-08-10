# Roadmap

- Phase 6: complete technical analyst architecture (implemented).
- Phase 6.5: Market Intelligence Feature Platform — hardened as canonical engineered-feature source (implemented).

- Phase 7.5 — Analyst lifecycle consolidated, Phase 8A introduced.
- Phase 8A — Unified Research Data Platform (implemented: deterministic, offline, point-in-time-safe data layer).

1. Phase 1 - safe modular foundation and in-memory paper simulation.
2. Phase 2 - validated market-data ingestion (complete: bounded deterministic mock, isolated yfinance adapter, policy-aware cache, strict UTC/provenance contracts, safe errors, and read-only API).
3. Phase 3 - offline Kronos integration and evaluation (complete: deterministic SMA crossover forecast over Phase 2 market data, cached predictions, read-only Kronos API endpoints, provenance fields on KronosPrediction).
4. Phase 4 - reproducible backtesting with costs and bias controls (complete).
5. Phase 5 - AI evidence fusion under deterministic risk controls.
6. Phase 6 - external paper-broker integration and reconciliation (complete: deterministic, research-only technical analyst).
7. Phase 6.5 - Market Intelligence Feature Platform (complete: canonical feature source, immutable FeatureSnapshot, deterministic cache identity, real dependency graph, no-lookahead guarantees).
8. Phase 7 - monitored live-trading readiness review; live operation remains separately gated.
# Phase 5

Common analyst opinion framework: contracts, evidence, confidence, freshness, traceability, persistence, descriptive aggregation, API, and CLI. No specialist intelligence or decisions.

# Phase 6

Technical Analyst specialist intelligence begins. Consumes historical market data through the Phase 6.5 Market Intelligence Feature Platform (MIFP) as the canonical feature source. See `docs/FEATURE_PLATFORM.md`.

# Phase 6.5

Market Intelligence Feature Platform: deterministic, immutable feature snapshots with full lineage, versioned schema, cache-safe identity, and no-lookahead guarantees. The Technical Analyst reads feature values from `FeatureSnapshot` rather than independently recalculating indicators.
# Phase 7 — Fundamental Analyst

Implemented: strict point-in-time financial models, deterministic normalization and formula services, shared evidence generation, contradiction-aware synthesis, generic API registration, offline CLI JSON input, safety checks, and documentation. See [FUNDAMENTAL_ANALYST.md](FUNDAMENTAL_ANALYST.md) and [PHASE_07_FUNDAMENTAL_ANALYST.md](phases/PHASE_07_FUNDAMENTAL_ANALYST.md).
# Phase 7.5 — complete

The analyst platform is consolidated behind `BaseAnalyst`, with one freshness, validation, confidence, and trace implementation. Mock, Technical, and Fundamental analysts retain backward-compatible APIs and now all emit provenance traces.

# Phase 8A — Unified Research Data Platform

Implemented as a deterministic, offline, read-only data layer that normalizes, versions, and serves research data from market, fundamental, macro, calendar, and news/event domains behind a single point-in-time-safe contract. Exposes `GET /data-platform/health`, `GET /data-platform/sources`, `GET /data-platform/domains`, `GET /data-platform/series`, `GET /data-platform/calendar`, and `POST /data-platform/snapshot`. CLI: `python -m app.cli.data_platform`. See [DATA_PLATFORM.md](DATA_PLATFORM.md) and [PHASE_08A_UNIFIED_DATA_PLATFORM.md](phases/PHASE_08A_UNIFIED_DATA_PLATFORM.md).
# Phase 8B — Macro Economist

Implemented as a deterministic, offline, research-only specialist analyst. Consumes `ResearchDataSnapshot` from the Phase 8A platform and produces Phase 5 `AnalystOpinion` objects through the Phase 7.5 analyst lifecycle. Classifies macro-economic regime via 8 specialist services (inflation, growth, employment, liquidity, monetary policy, yield curve, credit, business cycle) with priority-ordered regime classification. API: `GET /analysts/macro/health`, `GET /analysts/macro/metadata`, `POST /analysts/macro/analyze`. CLI: `python -m app.cli.analyst --analyst macro --input-snapshot <path>`. See [MACRO_ECONOMIST.md](MACRO_ECONOMIST.md) and [PHASE_08B_MACRO_ECONOMIST.md](phases/PHASE_08B_MACRO_ECONOMIST.md).
# Phase 9 — News Analyst (implemented)

Deterministic, offline, research-only news analyst consuming Phase 8A data platform records. See [NEWS_ANALYST.md](NEWS_ANALYST.md) and [PHASE_09_NEWS_ANALYST.md](phases/PHASE_09_NEWS_ANALYST.md).
# Phase 10 — Portfolio Manager

Implemented: deterministic offline opinion aggregation, conviction scoring, constrained research weights, turnover limits, point-in-time correlation, diversification, provenance, trace, API, and CLI. See [phase record](phases/PHASE_10_PORTFOLIO_MANAGER.md).
# Phase 11

The deterministic, offline Risk Officer is implemented with portfolio metrics, limits, stress testing, provenance, API/CLI access, and safety tests. It does not start or imply Phase 12.
