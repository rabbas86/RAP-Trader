# Technical Analyst

The Phase 6 technical analyst is deterministic, offline, and research-only. Registered as `technical`, it consumes the immutable `FeatureSnapshot` produced by the Phase 6.5 Market Intelligence Feature Platform (MIFP) as its sole feature source.

## MIFP integration

The normal analysis path (`analyze()`) follows:

```
AnalystRequest
  → FeatureService.snapshot()          [MIFP — canonical feature source]
    → FeatureSnapshot                  [immutable, frozen, validated]
      → _snapshot_from_features()      [reads feature values, no formula duplication]
      → _evidence_from_features()      [feature values → EvidenceItem objects]
        → TechnicalEvidenceSynthesizer
          → AnalystOpinion             [research_only=True, suitable_for_live_trading=False]
```

**No indicator formulas are duplicated.**  SMA, EMA, ROC, RSI, MACD, ATR,
Bollinger Bands, OBV, VWAP, rolling volume average, relative volume, and
typical-price VWAP are all computed once in the MIFP feature generators and
read from `FeatureSnapshot`.  The Technical Analyst does not independently
recalculate any of these.

For market structure and support/resistance, the Technical Analyst reads
MIFP structure features (`structure.regime`, `structure.swing_count`,
`structure.bos_timestamp`, `structure.choch_timestamp`, etc.) and level
features (`support_resistance.nearest_support`, `support_resistance.nearest_resistance`,
etc.) from the snapshot rather than recomputing them.

## Snapshot contents

The `TechnicalAnalysisSnapshot` is built from MIFP feature values:

* **Indicator values** — SMA/MA values and slopes, EMA values and slopes,
  crossover state and age, ROC, RSI, MACD, MACD signal, MACD histogram,
  True Range, Wilder ATR, Bollinger Bands and bandwidth, OBV, rolling
  volume average, relative volume, and typical-price VWAP.
* **Structure** — regime classification, higher-highs/higher-lows/
  lower-highs/lower-lows counts, break-of-structure and change-of-character
  timestamps.
* **Levels** — nearest support/resistance prices, level count, touch count,
  broken count.

## Evidence categories

Evidence groups cover:

* **trend** — SMA/EMA slopes, crossover state, MACD direction
* **momentum** — ROC, RSI, MACD histogram
* **volatility** — ATR, Bollinger bandwidth
* **volume** — OBV, relative volume, VWAP
* **structure** — regime, swing count, BoS, CHoCH
* **levels** — support/resistance level counts, touches, breaks

Optional serialized `kronos_forecast` and `backtest_result` entries in
`extra_context` become forecast and calibrated backtest evidence. Strength,
confidence, freshness, coverage, agreement, contradiction, calibration, and
missing data determine direction and confidence deterministically.

## API and CLI

Use `POST /analysts/technical/analyze` for an opinion and
`GET /analysts/technical/snapshot?ticker=AAPL&timeframe=1d&lookback=60&as_of=...`
for a snapshot. The CLI accepts `--analyst-id technical` or `--analyst
technical-analyst`, `--input-json`, and `--as-json`.

## Safety guarantees

Every opinion has:

* `research_only=True`
* `suitable_for_live_trading=False`
* `decision_ready=False`
* A validated acyclic trace from request and source nodes through evidence
  to the opinion
* No future features included (`available_at ≤ as_of` enforced by `FeatureSnapshot`)

The analyst never emits orders, quantities, allocations, stops, or approvals.

## Data Platform integration (Phase 8A)

The Unified Research Data Platform (Phase 8A) provides normalized market data records
that can supplement or replace MIFP feature inputs. See `docs/DATA_PLATFORM.md`. The
Technical Analyst reads feature values from `FeatureSnapshot` (Phase 6.5) as its
sole feature source — no indicator formulas are duplicated from the data platform.
