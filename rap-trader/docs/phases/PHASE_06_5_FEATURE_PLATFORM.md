# Phase 6.5 — Market Intelligence Feature Platform

## Status: Complete

Phase 6.5 hardens the Market Intelligence Feature Platform (MIFP) as the
canonical, deterministic source of engineered technical features and makes the
Phase 6 Technical Analyst consume it end-to-end.

## Goals

1. **Feature cache identity** — deterministic SHA-256 cache keys that encode
   lookback, schema version, Kronos/backtest fingerprints, and market-data
   configuration hash.
2. **Real feature availability / provenance** — `FeatureValue` gains
   `available_at`, `generated_at`, and `source_fingerprint`; snapshot-level
   no-lookahead validator enforces `available_at ≤ as_of`.
3. **Real dependency graph** — every registered feature has correct direct
   dependencies matching its formula (MACD → EMAs, Bollinger → SMA + close,
   ATR → true_range, etc.).
4. **MIFP as canonical technical feature source** — `TechnicalAnalyst.analyze()`
   consumes a `FeatureSnapshot` from `FeatureService` rather than
   independently recalculating indicator formulas.

## Completed changes

### New files

* `app/services/features/versioning.py` — `FEATURE_SCHEMA_VERSION`,
  `PLATFORM_VERSION`, `GENERATOR_VERSION` constants.
* `docs/FEATURE_PLATFORM.md` — full platform documentation.

### Modified files

* `app/domain/models/features.py` — `FeatureValue` extended with
  `available_at`, `generated_at`, `source_fingerprint`; `FeatureMetadata`
  gains `schema_version` / `platform_version`; `FeatureProvenance` gains
  `feature_schema_version` / `platform_version`; `FeatureSnapshot` gains
  `bars_analyzed`; model validators enforce no-lookahead.
* `app/services/features/cache.py` — `FeatureCacheKey` extended with
  `lookback`, `schema_version`, `kronos_fingerprint`, `backtest_fingerprint`;
  centralized `build_cache_key()` function; `kronos_fingerprint()` and
  `backtest_fingerprint()` helpers.
* `app/services/features/provenance.py` — `build_provenance()` records
  schema/platform/generator versions.
* `app/services/features/service.py` — real dependency graph
  (`_REAL_DEPENDENCIES`), centralized cache-key builder, Kronos/backtest
  input normalization, `bars_analyzed` in snapshot.
* `app/services/technical_analysis/service.py` — `analyze()` consumes
  `FeatureSnapshot` from `FeatureService`; `_snapshot_from_features()` and
  `_evidence_from_features()` build `TechnicalAnalysisSnapshot` and
  `EvidenceItem` objects from feature values without recalculating formulas.
* `tests/test_features_platform.py` — comprehensive regression tests for
  cache identity, availability, dependency graph, and MIFP integration.

## Cache identity

The cache key is built by `build_cache_key()` and includes:

| Dimension | Why |
|---|---|
| `ticker`, `timeframe` | What is being analyzed |
| `provider` | Prevents cross-provider collisions |
| `adjustment`, `session` | Policy semantics affect bar values |
| `as_of` | Evaluation point in time |
| `lookback` | Different window sizes yield different features |
| `configuration_hash` | Encodes feature selection and configuration |
| `schema_version` | Incompatible model shapes must not collide |
| `kronos_fingerprint` | Different forecast content invalidates cache |
| `backtest_fingerprint` | Different backtest results invalidate cache |

Only SHA-256 fingerprints of variable-length payloads are stored in the key.

## Availability semantics

| Timestamp | Meaning |
|---|---|
| `observed_at` | When the source data point was observed |
| `available_at` | When the information became known (≤ `observed_at`) |
| `generated_at` | When the feature was computed |

Invariants: `available_at ≤ observed_at ≤ generated_at`.

For external features (Kronos, backtests), `available_at` reflects when the
external information became available, not the bar timestamp.

## Dependency graph semantics

Dependencies are **lineage dependencies**: they describe which upstream
features were consumed to produce a given feature.  They are used for:

* Topological ordering at computation time
* Provenance recording
* Cache invalidation (changing an upstream feature invalidates dependents)

## Technical Analyst MIFP integration

```
AnalystRequest
  → FeatureService.snapshot()          [MIFP]
    → FeatureSnapshot                  [immutable, validated]
      → _snapshot_from_features()      [no formula duplication]
      → _evidence_from_features()      [feature values → EvidenceItem]
        → TechnicalEvidenceSynthesizer
          → AnalystOpinion             [research_only=True]
```

The Technical Analyst no longer independently calculates SMA, EMA, RSI,
MACD, ATR, Bollinger, OBV, VWAP, market structure, or support/resistance.
All values are read from `FeatureSnapshot`.

## Quality gates

| Gate | Result |
|---|---|
| `pytest` | 277 passed |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 121 files already formatted |
| `mypy app --strict` | Success: no issues found |

## Safety review

* MIFP has no imports of Broker, PaperBroker, ExecutionService, OrderRequest,
  RiskEngine, PortfolioManager, Investment Committee, or Chairman.
* No LLM, model, or network access in the default offline path.
* No credentials, API keys, or secrets stored or logged.
* All TechnicalAnalyst opinions: `research_only=True`,
  `suitable_for_live_trading=False`.
