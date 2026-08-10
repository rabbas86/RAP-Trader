# Phase 8B Macro Economist

## Overview

The Macro Economist is a deterministic, offline, research-only specialist analyst
that classifies the global macro-economic regime from `ResearchDataSnapshot`
data provided by the [Phase 8A Unified Research Data Platform](DATA_PLATFORM.md).

It integrates into the [Analyst Platform](ANALYST_PLATFORM.md) lifecycle
(Phase 5 opinion contracts, Phase 7 trace/audit DAG) and is exposed through the
same API and CLI surface as the existing Fundamental and Technical analysts.

## Safety guarantees

| Property | Value |
|---|---|
| Research only | `true` |
| Suitable for live trading | `false` |
| Decision ready | `false` |
| Network access (default path) | none |
| LLM / model download | none |
| Broker / execution / risk | never imported or called |
| BUY / SELL semantics | never emitted |

Every opinion carries:

* `research_only = true`
* `suitable_for_live_trading = false`
* `decision_ready = false`
* `analyst_role = MACRO`
* A `NO_TRADING` limitation

## Input: ResearchDataSnapshot

The Macro Economist consumes a `ResearchDataSnapshot` passed via
`AnalystRequest.extra_context.snapshot`. The snapshot is produced by the
Phase 8A platform, which applies:

* **Point-in-time revision selection** — `PointInTimeRevisionService.select()`
  ensures only revisions available at `as_of` are included.
* **Data validation** — `DataValidationService` rejects records with
  chronology violations, NaN/Infinity values, or forbidden flags.
* **Normalization** — `DataNormalizationService` canonicalizes symbols,
  currencies, and units.

The Macro Economist never queries raw data providers, never uses
latest-known revisions, and never bypasses Phase 8A revision logic.

## Specialist services

Each macro domain is handled by a small, deterministic service class. No god
service — each maps a single trend enum from a specific series category.

| Service | Domain | Trend enum | Threshold source |
|---|---|---|---|
| `InflationAnalysisService` | CPI / PCE | `InflationTrend` | `config.inflation_*` |
| `GrowthAnalysisService` | GDP / GDP_trend / PMI | `GrowthTrend` | `config.growth_*` |
| `EmploymentAnalysisService` | Unemployment / Payrolls | `EmploymentTrend` | `config.unemployment_*` |
| `LiquidityAnalysisService` | Money supply | `LiquidityTrend` | `config.money_supply_*` |
| `MonetaryPolicyAnalysisService` | Policy rate | `PolicyStance` | `config.policy_rate_*` |
| `YieldCurveAnalysisService` | 2Y/10Y spread | `YieldCurveTrend` | `config.yield_curve_*` |
| `CreditAnalysisService` | Credit spreads | `CreditCondition` | `config.credit_spread_*` |
| `BusinessCycleService` | GDP + employment + inflation | `BusinessCyclePhase` | Composite |

## Trend classification logic

Each service reduces a sorted series of `MacroObservation` values (oldest first)
to a trend enum using deterministic thresholds:

### Inflation

1. Delta = latest − prior (percentage points).
2. If `latest ≥ inflation_high_warning`:
   * `delta > inflation_stability_threshold` → `ACCELERATING`
   * `delta < -inflation_stability_threshold` → `DECELERATING`
   * otherwise → `STABLE`
3. If `latest < inflation_high_warning`:
   Same threshold logic applies.

### Growth

1. If `latest < growth_negative_threshold` (default 0.0%) → `NEGATIVE`.
2. Else if `delta > growth_accelerating_threshold` (default 2.0pp) → `ACCELERATING`.
3. Else if `delta < -growth_decelerating_threshold` (default -1.0pp) → `DECELERATING`.
4. Otherwise → `STABLE`.

### Employment

Classified by absolute unemployment rate level (not delta):

* `latest < unemployment_low_threshold` (default 4.0%) → `STRENGTHENING`
* `latest > unemployment_high_threshold` (default 6.0%) → `WEAKENING`
* Otherwise → `STABLE`

### Liquidity

1. If `delta < -money_supply_contraction_threshold` → `CONTRACTING`.
2. If `delta > money_supply_contraction_threshold` → `EXPANDING`.
3. Otherwise → `STABLE`.

### Policy rate

* `latest ≥ policy_rate_high_threshold` (default 5.0%) → `RESTRICTIVE`
* `latest ≤ policy_rate_low_threshold` (default 2.0%) → `ACCOMMODATIVE`
* Otherwise → `NEUTRAL`

### Yield curve

* `latest < yield_curve_inversion_threshold` (default 0.0pp) → `INVERTED`
* `delta > inversion_threshold` → `STEEPENING`
* Otherwise → `NORMAL`

### Credit spreads

* `latest > credit_spread_tightening_threshold` (default 2.0pp) → `TIGHTENING`
* `latest < threshold × 0.5` → `LOOSENING`
* Otherwise → `STABLE`

### Business cycle

Fuses growth + employment + inflation trends:

* growth is `NEGATIVE` → `CONTRACTION`
* growth is `ACCELERATING` and employment is not `WEAKENING` → `EXPANSION`
* growth is `DECELERATING` → `PEAK`
* Otherwise → `UNKNOWN`

## Regime classification

`MacroRegimeService` fuses all trend signals into a single `MacroRegime` using
a priority-ordered rule set. Each regime has an associated confidence score.

### Priority order

| Step | Regime(s) | Triggers | Confidence |
|---|---|---|---|
| 1 | `STAGFLATION` | Accelerating inflation + negative growth | 0.75 |
| 2 | `INFLATION_SHOCK` | Accelerating inflation (non-negative growth) | 0.65 |
| 3 | `DEFLATION_RISK` | Decelerating inflation + negative growth | 0.65 |
| 4 | `EXPANSION` | Growth accelerating | 0.60 |
| 5 | `RECOVERY` | Business cycle shows recovery phase | 0.60 |
| 6 | `SLOW_EXPANSION` | Growth stable, inflation stable | 0.50 |
| 7 | `PEAK` | Growth decelerating + yield curve inverted | 0.55 |
| 8 | `SLOWDOWN` | Growth decelerating (non-inverted curve) | 0.55 |
| 9 | `RECESSION` | Growth negative, non-accelerating inflation | 0.60 |
| 10 | `TIGHTENING` | Policy restrictive (growth signal absent) | 0.40 |
| 11 | `EASING` | Policy accommodative (growth signal absent) | 0.40 |
| 12 | `LIQUIDITY_EXPANSION` | Liquidity expanding (growth signal absent) | 0.45 |
| 13 | `LIQUIDITY_CONTRACTION` | Liquidity contracting (growth signal absent) | 0.45 |
| 14 | `UNKNOWN` | Insufficient evidence (< `min_regime_categories`) | 0.20 |
| 15 | `UNKNOWN` | Weak/conflicting evidence, indeterminate | 0.30 |

### Key design principles

* **Crisis regimes take priority**: STAGFLATION, INFLATION_SHOCK, and
  DEFLATION_RISK are evaluated first and require `has_enough` evidence
  (`signal_count ≥ min_regime_categories`).
* **Growth + inflation driven regimes** are evaluated before liquidity/policy
  regimes so a clear growth signal is never masked by a secondary liquidity or
  policy reading.
* **Liquidity/policy regimes** only fire when the growth signal is absent,
  preventing a single policy-rate reading from overriding a clear growth trend.
* **Insufficient evidence** always falls through to `UNKNOWN` rather than
  forcing a directional call.

### Why liquidity/policy are secondary

A rising policy rate during an expansion (liquidity_easing = False, policy = RESTRICTIVE,
growth = ACCELERATING) is classified as EXPANSION, not TIGHTENING. The liquidity
and policy rules only apply when the growth signal is unknown, ensuring the
regime reflects the dominant economic force rather than a single indicator.

## Evidence generation

`MacroEvidenceFactory` converts each `MacroSignal` into a Phase 5
`EvidenceItem` with:

* `evidence_type = "macroeconomic"`
* `confidence` (from signal, capped by `uncalibrated_confidence_cap = 0.65`)
* `strength` (STRONG/MODERATE/WEAK based on confidence thresholds)
* `observed_at`, `available_at`, `evaluated_at`, `valid_until`
* `assumptions` (point-in-time deterministic snapshot)
* `warnings` (STABLE/UNKNOWN trends get a macro warning)
* `limitations` (threshold-based classification, no structural-shift awareness)
* `provenance` (source provider:dataset, retrieval timestamp)

## Synthesis

`MacroOpinionSynthesisService` maps regime + signals to an
`AnalysisDirection` (NEUTRAL/BULLISH/BEARISH/MIXED/INSUFFICIENT_EVIDENCE).

### Conflict handling

* Signal-level conflict is computed: if both positive-trend and negative-trend
  signals exist, `conflict_fraction` increases.
* Regime-level conflict is computed from the positive/negative score balance.
* The final `conflict_fraction` is the `max` of both.
* `confidence` is reduced proportionally to `conflict_fraction`.

### Direction rules

| Regime(s) | Condition | Direction |
|---|---|---|
| Crisis regimes | `conflict_fraction ≥ 0.30` | MIXED |
| Crisis regimes | `conflict_fraction < 0.30` | BEARISH |
| Bullish regimes | `conflict_fraction ≥ 0.30` | MIXED |
| Bullish regimes | `conflict_fraction < 0.30` | BULLISH |
| Neutral regimes | `conflict_fraction ≥ 0.30` | MIXED |
| Neutral regimes | `conflict_fraction < 0.30`, score > 0.15 | NEUTRAL |
| Neutral regimes | otherwise | INSUFFICIENT_EVIDENCE |

## Data sparsity and stale data

* If fewer than 2 signals are produced, the opinion is marked
  `INSUFFICIENT_EVIDENCE` with an `INSUFFICIENT_DATA` warning.
* If the latest observation is older than `stale_threshold` (default 7 days),
  the evidence is rejected by `EvidenceValidator` with a
  `"Expired or stale evidence was rejected"` warning.
* Single-series snapshots produce no regime classification.

## API

### GET /analysts/macro/health

Returns `{"status": "healthy"}`.

### GET /analysts/macro/metadata

Returns analyst metadata including role, research_only, suitable_for_live_trading,
and supported timeframes (`1d`, `1w`, `1mo`, `1q`).

### POST /analysts/macro/analyze

Accepts an `AnalystRequest` JSON body with:

```json
{
  "analyst_id": "macro",
  "ticker": "US",
  "timeframe": "1d",
  "as_of": "2026-08-01T12:00:00Z",
  "lookback": 60,
  "horizon": 30,
  "asset_class": "macro",
  "extra_context": {
    "snapshot": { ... ResearchDataSnapshot JSON ... }
  }
}
```

Returns an `AnalystOpinion` JSON object.

## CLI

```
python -m app.cli.analyst \
  --analyst macro \
  --ticker US \
  --timeframe 1d \
  --lookback 60 \
  --horizon 30 \
  --asset-class macro \
  --as-of 2026-08-01T12:00:00Z \
  --input-snapshot snapshot.json \
  --as-json
```

## Configuration

All thresholds are in `MacroAnalystConfig` (`app/services/macro_analysis/config.py`)
as frozen dataclass fields with documented defaults. Adjusting a threshold
requires code review and is auditable via git history.
# Portfolio contribution

Macro opinions can contribute to Phase 10 conviction when they cover an explicit portfolio symbol. Mixed or neutral directions have zero signed orientation and do not become order instructions.
