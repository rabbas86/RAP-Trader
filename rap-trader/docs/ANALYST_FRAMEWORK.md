# Analyst Framework

The `technical` analyst adds six evidence categories, optional forecast/backtest evidence, confidence-capped deterministic synthesis, and a validated provenance DAG.

## Phase 5 boundary

Phase 5 defines a common, strict contract for research opinions. It does not implement specialist intelligence or create trades. An analyst reports a direction, bounded confidence, timestamped evidence, assumptions, warnings, limitations, freshness, and provenance.

The included `MockAnalyst` is deterministic and offline. Confidence is not certainty: uncalibrated values are capped, stale or conflicting evidence reduces confidence, and the framework never invents historical accuracy. Evidence records observation, availability, evaluation, and expiry times so lookahead and stale inputs can be rejected.

Opinion aggregation is descriptive only. It reports agreement, disagreement, direction counts, orientation, overlap, freshness, missing roles, and minority views. Every opinion and aggregate is research-only, unsuitable for live trading, and not decision-ready. Analysts cannot create trades.

Technical Analyst work begins in Phase 6. The Risk Officer, Investment Committee, and Chairman remain future phases. A prior committee-fusion idea is isolated under `app/experimental/committee_fusion` and is not production code.

## Phase 6.5 MIFP integration

The Technical Analyst (Phase 6) consumes the immutable `FeatureSnapshot` produced by the Market Intelligence Feature Platform (MIFP, Phase 6.5) as its canonical feature source. Feature values flow:

```
AnalystRequest → FeatureService → FeatureSnapshot → EvidenceItem → AnalystOpinion
```

All indicator formulas are computed once in MIFP feature generators; the Technical Analyst reads the resulting values from the snapshot and converts them into `EvidenceItem` objects. No indicator is independently recalculated. See `docs/FEATURE_PLATFORM.md` and `docs/TECHNICAL_ANALYST.md`.

## Phase 7 — Fundamental Analyst

The fundamental analyst consumes caller-supplied point-in-time financial statements and produces Phase 5 `AnalystOpinion` objects via the same evidence/synthesis contracts. It evaluates growth, profitability, cash flow, balance-sheet strength, capital efficiency, valuation, earnings quality, and shareholder capital allocation. ROIC, earnings-quality ratings, and all other metrics use documented deterministic formulas with no network access, no LLM, and no model download. See `docs/FUNDAMENTAL_ANALYST.md`.
# Phase 7.5 shared implementation

The framework is implemented in `app/services/analyst/framework/`. `BaseAnalyst` supplies default input validation, health, metadata, freshness, evidence validation, confidence assessment, deterministic trace storage, fail-safe insufficient opinions, and error translation. Existing imports from `app.services.analyst.service` remain compatible.

Specialists implement their supported inputs and domain analysis. The complete lifecycle is Validation → Normalization → Analysis → Evidence → Confidence → Opinion → Trace → Output. A trace is available through `trace_for(opinion_id)` for successful and insufficient opinions.
