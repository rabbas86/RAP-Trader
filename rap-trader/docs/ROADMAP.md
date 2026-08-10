# Roadmap

- Phase 6: complete technical analyst architecture (implemented).
- Phase 6.5: Market Intelligence Feature Platform — hardened as canonical engineered-feature source (implemented).

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
