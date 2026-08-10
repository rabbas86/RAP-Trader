# Risk Officer

Phase 11 adds a deterministic, offline, portfolio-level risk review of a Phase 10 `PortfolioProposal`. It produces an immutable `RiskAssessment` and `RiskDecision`; both are permanently marked `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`.

## Inputs and outputs

The required input is the complete proposal. Optional caller-supplied inputs are point-in-time `HistoricalBarsResult` records, liquidity observations, and a `RiskConstraintSet`. The service never retrieves data. Missing, stale, future, or insufficient observations are reported rather than fabricated.

The assessment includes concentration, exposure, volatility, correlation, drawdown, historical VaR/CVaR, liquidity, turnover, data-quality, limit-breach, and stress results. IDs are UUID5 values derived from stable SHA-256 JSON fingerprints. The trace is a validated DAG from proposal through metrics, breaches, stress, assessment, and decision.

## Statistical conventions

Daily close-to-close returns are used. Annualized daily volatility uses `sqrt(252)`. Portfolio history is an aligned weighted return series. Historical VaR is the positive magnitude of the lower-tail return percentile; CVaR is the positive magnitude of the mean return at or below that cutoff. VaR/CVaR are invalid below `min_sample_size`.

Correlation uses Pearson correlation on common timestamps. High-correlation clusters use the configured threshold (default `0.70`). Drawdown is the peak-to-trough loss magnitude. No result is a forecast.

## Stress scenarios

The ten fixed scenarios are `market_down_10`, `market_down_20`, `top_position_down_25`, `sector_down_20`, `volatility_double`, `correlation_spike`, `credit_spreads_widen`, `rates_up_100bps`, `liquidity_haircut_50`, and `combined_risk_off`. Impacts use transparent linear sensitivities to supplied exposure metadata. They do not model nonlinear pricing, market depth, fills, costs, or executable size.

## Decisions

`approve` requires sufficient data, no breaches, and stress losses within limits. Correctable soft breaches produce `require_modification`. Critical hard limits, catastrophic stress, or multiple severe breaches produce `reject`. Missing, stale, or insufficient required history produces `insufficient_data`. Analyst confidence cannot override a limit.

## Offline usage

```text
python -m app.cli.risk --proposal-json proposal.json --history-json history.json --liquidity-json liquidity.json --as-of 2025-01-10T00:00:00+00:00 --json
```

API endpoints are `GET /risk/health`, `GET /risk/metadata`, `POST /risk/assess`, and `POST /risk/review`. POST bodies contain `proposal` and may contain `historical_bars`, `liquidity_inputs`, and `constraints`.

The Risk Officer does not place trades, construct executable instructions, estimate fills, or connect to external services.

