# Risk Officer

Phase 11 adds a deterministic, offline, portfolio-level risk review of a Phase 10 `PortfolioProposal`. It produces an immutable `RiskAssessment` and `RiskDecision`; both are permanently marked `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`.

Phase 11 is implemented. It is distinct from the Phase 1 `RiskEngine`, which remains the execution/trade-level safety control. Phase 12 Investment Committee and Phase 13 Chairman consume its outputs for research governance; neither can override Risk `REJECT` or authorize execution.

## Inputs and outputs

The required input is the complete proposal. Optional caller-supplied inputs are point-in-time `HistoricalBarsResult` records, liquidity observations, and a `RiskConstraintSet`. The service never retrieves data. Missing, stale, future, or insufficient observations are reported rather than fabricated.

The assessment includes concentration, exposure, volatility, correlation, drawdown, historical VaR/CVaR, liquidity, turnover, data-quality, limit-breach, and stress results. IDs are UUID5 values derived from stable SHA-256 JSON fingerprints. The trace is a validated DAG from proposal through metrics, breaches, stress, assessment, and decision.

## Statistical conventions

Daily close-to-close returns are used. Annualized daily volatility uses `sqrt(252)`. Portfolio history is an aligned weighted return series. Historical VaR is the positive magnitude of the lower-tail return percentile; CVaR is the positive magnitude of the mean return at or below that cutoff. VaR/CVaR are invalid below `min_sample_size`.

Correlation uses Pearson correlation on common timestamps. High-correlation clusters use the configured threshold (default `0.70`). Drawdown is the peak-to-trough loss magnitude. No result is a forecast.

## Stress scenarios

The ten fixed scenarios are `market_down_10`, `market_down_20`, `top_position_down_25`, `sector_down_20`, `approximate_volatility_spike`, canonical `correlation_to_one`, `credit_spreads_widen`, `rates_up_100bps`, `liquidity_haircut_50`, and `combined_risk_off`. The volatility scenario is explicitly an approximate fixed linear sensitivity; it does not claim to double portfolio variance. Impacts use transparent linear sensitivities to supplied exposure metadata. Every scenario is hypothetical, not a forecast. They do not model nonlinear pricing, market depth, fills, costs, or executable size.

## Decisions

`approve` requires sufficient data, no breaches, and stress losses within limits. Correctable soft breaches produce `require_modification`. Critical hard limits, catastrophic stress, or multiple severe breaches produce `reject`. Missing, stale, or insufficient required history produces `insufficient_data`. Analyst confidence cannot override a limit.

## Offline usage

```text
python -m app.cli.risk --proposal-json proposal.json --history-json history.json --liquidity-json liquidity.json --as-of 2025-01-10T00:00:00+00:00 --json
```

API endpoints are `GET /risk/health`, `GET /risk/metadata`, `POST /risk/assess`, and `POST /risk/review`. POST bodies contain `proposal` and may contain `historical_bars`, `liquidity_inputs`, and `constraints`.

The Risk Officer does not place trades, construct executable instructions, estimate fills, or connect to external services.
## Phase 12 governance precedence

The Investment Committee consumes the Risk Officer assessment and decision without weakening them. `REJECT` is non-overridable, `INSUFFICIENT_DATA` blocks approval, and `REQUIRE_MODIFICATION` forces revision. `APPROVE` is necessary but not sufficient for committee approval.
