# Phase 7 — Fundamental Analyst

Phase 7 adds a deterministic, offline fundamental research analyst for equities. It consumes point-in-time `CompanyFundamentals` supplied by the caller and produces the shared Phase 5 `AnalystOpinion`. It never fetches data, invokes a model, creates an order, sizes a position, allocates capital, or emits a trading instruction.

## Contracts and chronology

Financial statements carry both the economic period end and the filing availability timestamp. All timestamps must be timezone-aware and are normalized to UTC. A period end or filing date cannot follow `available_at`, and the normalizer rejects any filing or restatement unavailable at the requested `as_of` time. Duplicate fiscal periods are resolved deterministically in favor of the latest available version; restatements and missing annual periods remain visible as warnings. No interpolation occurs.

The strict models cover income statements, balance sheets, cash flows, company-level market inputs, derived metrics, and snapshots. Non-finite numeric values are rejected. Missing gross profit and free cash flow are derived only from complete inputs; original source models are not mutated.

## Analysis

The service computes growth, margins and returns, cash conversion and distribution coverage, liquidity and leverage, capital efficiency, valuation, earnings quality, and shareholder capital allocation. Percentage growth is suppressed for zero or negative comparison bases. Valuation metrics appear only when their market inputs are explicitly supplied. Forward P/E, dividend yield, and PEG additionally require explicitly named values in `source_metadata`.

ROIC uses `NOPAT / Invested Capital`, where NOPAT is `EBIT × (1 − tax rate)`. Invested capital is total assets less current liabilities and accounts payable, plus cash and equivalents. This proxy and incomplete-input limitations are recorded as assumptions; the analyst makes no precision claim when inputs are absent.

Evidence is converted to the existing Phase 5 `EvidenceItem` contract with deterministic UUID5 identifiers, structured assumptions, warnings, limitations, provenance, and point-in-time chronology. Synthesis requires category coverage, penalizes stale and missing data, returns `MIXED` for strong contradictions, caps uncalibrated confidence at 0.65, and prevents valuation from automatically dominating business quality.

## Interfaces

The generic analyst API discovers `fundamental` through `AnalystService`. No separate snapshot endpoint is added. CLI usage:

```powershell
.venv/Scripts/python.exe -m app.cli.analyst --analyst fundamental --ticker TEST --timeframe 1d --as-of 2025-03-15T00:00:00+00:00 --input-fundamentals fundamentals.json --as-json
```

Every successful or insufficient opinion enforces `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`.
