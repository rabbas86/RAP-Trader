# Phase 08B — Macro Economist

## Status: Complete

## Summary

Adds a deterministic, offline, research-only Macro Economist to the analyst
platform. Consumes `ResearchDataSnapshot` from the Phase 8A Unified Research
Data Platform and produces Phase 5 `AnalystOpinion` objects through the Phase 7
analyst lifecycle (trace, health, metadata, API, CLI).

## Deliverables

### New files

```
app/services/macro_analysis/
├── __init__.py              Package exports
├── config.py                MacroAnalystConfig (frozen dataclass, all thresholds)
├── domain.py                MacroRegime + trend enums, category constants
├── observations.py          MacroObservation + ObservationExtractor
├── trends.py                Deterministic trend classification helpers
├── base.py                  MacroAnalysisService base + MacroSignal + build_signal_id
├── inflation.py             InflationAnalysisService
├── growth.py                GrowthAnalysisService
├── employment.py            EmploymentAnalysisService
├── liquidity.py             LiquidityAnalysisService
├── yield_curve.py           YieldCurveAnalysisService
├── credit.py                CreditAnalysisService
├── monetary_policy.py       MonetaryPolicyAnalysisService
├── business_cycle.py        BusinessCycleService
├── regime.py                MacroRegimeService (priority-ordered classification)
├── evidence.py              MacroEvidenceFactory (Phase 5 EvidenceItem builder)
├── synthesis.py             MacroOpinionSynthesisService
└── service.py               MacroAnalyst (Analyst subclass)
```

```
tests/test_macro_economist.py             50 tests covering all domains/regimes
docs/MACRO_ECONOMIST.md                   Full documentation
docs/phases/PHASE_08B_MACRO_ECONOMIST.md  This file
```

### Modified files

```
app/services/analyst/service.py           Added MacroAnalyst to default analysts
app/cli/analyst.py                        Added --analyst macro + --input-snapshot
```

## Architecture

```
ResearchDataSnapshot (Phase 8A)
        │
        ▼
ObservationExtractor         ──►  dict[series_id → list[MacroObservation]]
        │
        ├──► InflationAnalysisService     ─┐
        ├──► GrowthAnalysisService        ─┤
        ├──► EmploymentAnalysisService    ─┤
        ├──► LiquidityAnalysisService     ─┤  MacroSignal list
        ├──► MonetaryPolicyAnalysisService ─┤
        ├──► YieldCurveAnalysisService    ─┤
        ├──► CreditAnalysisService         ─┤
        ├──► BusinessCycleService         ─┘
        │
        ▼
MacroRegimeService.classify(signals) ──► RegimeResult (MacroRegime + confidence)
        │
        ▼
MacroEvidenceFactory.build(signals) ──► list[EvidenceItem]
        │
        ▼
MacroOpinionSynthesisService.synthesize(...) ──► SynthesisResult
        │
        ▼
AnalystOpinion (Phase 5 contract)
```

## Macro domains

1. **Inflation** — CPI, Core CPI, PCE, Core PCE trend classification.
2. **Growth** — GDP, GDP trend, PMI, industrial production, retail sales trend.
3. **Employment** — Unemployment rate, nonfarm payrolls level classification.
4. **Liquidity** — Broad money supply growth trend.
5. **Monetary policy** — Policy rate stance (restrictive/accommodative/neutral).
6. **Yield curve** — 2Y/10Y spread shape (inverted/normal/steepening).
7. **Credit** — Credit spread tightening/loosening/stable.
8. **Business cycle** — Composite phase from growth + employment + inflation.

## MacroRegime values

| Regime | Description |
|---|---|
| `EXPANSION` | Accelerating growth with supporting employment |
| `SLOW_EXPANSION` | Stable growth, stable inflation |
| `PEAK` | Decelerating growth, inverted yield curve |
| `SLOWDOWN` | Decelerating growth, non-inverted curve |
| `RECESSION` | Negative growth, stable/decelerating inflation |
| `RECOVERY` | Positive business cycle phase emerging |
| `TIGHTENING` | Restrictive monetary policy (growth signal absent) |
| `EASING` | Accommodative monetary policy (growth signal absent) |
| `STAGFLATION` | Accelerating inflation + negative growth |
| `INFLATION_SHOCK` | Accelerating inflation (growth not negative) |
| `DEFLATION_RISK` | Decelerating inflation + negative growth |
| `LIQUIDITY_EXPANSION` | Expanding money supply (growth signal absent) |
| `LIQUIDITY_CONTRACTION` | Contracting money supply (growth signal absent) |
| `UNKNOWN` | Insufficient or conflicting evidence |

## Regime precedence

1. Crisis regimes (STAGFLATION > INFLATION_SHOCK > DEFLATION_RISK) — highest priority
2. Growth + inflation driven (EXPANSION > RECOVERY > SLOW_EXPANSION > PEAK > SLOWDOWN > RECESSION)
3. Liquidity-driven (only when growth signal is absent)
4. Policy-driven (only when growth signal is absent)
5. Business cycle fallback
6. UNKNOWN (insufficient evidence)

Key invariant: a clear growth signal always overrides liquidity/policy regimes.
Liquidity/policy regimes only fire when `growth_enum is GrowthTrend.UNKNOWN`.

## Safety guarantees

* `research_only = true`
* `suitable_for_live_trading = false`
* `decision_ready = false`
* `analyst_role = MACRO`
* No network access, no LLM, no model download
* No imports of broker, execution, risk, portfolio, or committee modules
* No BUY/SELL semantics in any output
* Point-in-time: consumes only `ResearchDataSnapshot` records selected by
  `PointInTimeRevisionService` at `as_of` time

## Test coverage

50 tests in `tests/test_macro_economist.py`:

* Inflation: accelerating, decelerating, stable
* Growth: accelerating, negative
* Employment: strengthening, weakening
* Yield curve: inverted, normal
* Credit: tightening, loosening
* Business cycle: expansion, contraction
* Regime classification: expansion, recession, stagflation, tightening, easing,
  liquidity expansion/contraction, inflation shock
* Regime precedence: stagflation > inflation shock, recession > easing, peak > slowdown, unknown
* Conflicting evidence: mixed/neutral direction, reduced confidence
* Insufficient data: empty snapshot, non-macro records, single series, missing snapshot
* Determinism: identical results across calls, deterministic opinion_id
* Trace: DAG generation, insufficient-for edge
* Freshness & provenance: evidence timestamps, source attribution
* API: registration, health, metadata, analyze endpoints
* CLI: smoke test with snapshot file, missing snapshot error
* Safety: AST scan for forbidden imports, research_only verification, no BUY/SELL

## Quality gates

* pytest: 460 passed
* ruff check: clean
* ruff format: clean
* mypy --strict: clean
