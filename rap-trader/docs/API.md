# API

Phase 6 adds `GET /analysts/technical/snapshot` (`ticker`, `timeframe`, `lookback`, optional UTC `as_of`) and extends `POST /analysts/technical/analyze` with structured evidence.

## `GET /health`

Returns service health and the active trading mode. It does not submit orders.

## `GET /system/status`

Returns application name, environment, trading mode, live-trading flag, and version.

## `GET /market-data/health`

Returns provider name, configuration state, optional reachability, UTC check time, status, and detail. The yfinance adapter does not perform a network probe by default, so reachability is `null` and status is `degraded`.

## `GET /market-data/timeframes`

Returns supported timeframes as `{"timeframes": ["1m", "5m", "15m", "1h", "1d", "1w"]}`.

## `GET /market-data/bars`

Parameters are `symbol`, `timeframe`, timezone-aware ISO 8601 `start` and `end`, optional positive `limit`, `adjustment` (`raw`, `split_adjusted`, or `total_return_adjusted`), and `session` (`regular`, `extended`, or `all`). Defaults are `raw` and `regular`. Provider limits can be stricter than API validation; the mock defaults to 5,000 bars.

Results contain normalized bars plus requested and actual ranges, adjustment/session policy, provider, optional currency/exchange, partial-data status, and UTC retrieval time. Empty matches produce `NO_DATA`, never a successful empty result. Failures expose only `detail.code` and `detail.safe_message`; provider diagnostics remain internal.

The default provider is deterministic and offline. yfinance is opt-in. No market-data endpoint submits orders or connects to brokerage.

## `GET /kronos/health`

Returns Kronos service health: status, model_version (`offline-kronos-v0`), `live_trading_suitable` (always `false`), and the underlying market-data provider health snapshot. Does not submit orders or connect to brokers.

## `GET /kronos/prediction`

Parameters are `ticker`, `timeframe`, timezone-aware ISO 8601 `start` and `end`, and optional positive `limit`. Returns a `KronosPrediction` with direction (UP/DOWN/FLAT), confidence (0-1), expected_return, time_horizon, generated_at, model_version, and bar provenance (timeframe, source_provider, data_start, data_end).

The default `OfflineKronosService` applies a deterministic SMA crossover (5-period short vs 20-period long) over bars from the configured provider. Predictions are offline, reproducible, and not suitable for live trading. Insufficient bars or provider errors produce a FLAT fallback with confidence 0. Failures expose only safe error codes and messages.

## `POST /backtests/run`

Runs a walk-forward backtest with deterministic, offline defaults. Accepts a `BacktestRunRequest`
JSON body with fields: `ticker`, `timeframe`, `start`, `end` (all UTC), `lookback` (default 60),
`horizon` (default 5), `step` (default 5), `max_windows` (optional cap), `seed` (default 42),
`include_local_kronos` (default false), `research_simulation` (default false), `short_selling`
(default false), `leverage` (default 1.0), `transaction_cost_bps` (default 0.0), and
`slippage_bps` (default 0.0).

Returns a `BacktestRunResult` with `research_only=True`, `suitable_for_live_trading=False`,
per-provider aggregated metrics, regime distribution, and optional research signal / cost
results. No orders are submitted, no broker is connected, no model is downloaded, and no network
access occurs with default settings.

## `GET /backtests/providers`

Returns `{"providers": [...], "benchmark_only": true, "local_kronos_available": true}`.

## `GET /backtests/{backtest_id}`

Returns the full stored `BacktestRunResult`. Returns 404 if the backtest ID is not found.

## `GET /backtests/{backtest_id}/summary`

Returns a `BacktestSummary` with best provider by RMSE, mean MAE/RMSE per provider, regime
distribution, and window counts. Returns 404 if not found.

The backtesting engine enforces hard no-lookahead runtime guards: forecast timestamps cannot
overlap context bars, must match expected target timestamps exactly, and no bar beyond
`context_end` is ever returned. See `docs/BACKTESTING.md` for full documentation.
# Analyst endpoints (Phase 5)

- `GET /analysts` lists configured research analysts.
- `GET /analysts/{analyst_id}/health` reports health.
- `GET /analysts/{analyst_id}/metadata` reports capabilities and safety flags.
- `POST /analysts/{analyst_id}/analyze` accepts an `AnalystRequest` and returns an `AnalystOpinion`.
- `POST /analysts/opinions/aggregate` describes multiple opinions without making a decision.
- `GET /analysts/opinions/{opinion_id}` retrieves a stored opinion.

All analyst responses are research-only and cannot create trades. Public errors expose only stable codes and safe messages.

Phase 7.5 preserves these endpoints and schemas while consolidating their lifecycle. Mock, Technical, and Fundamental opinions now have deterministic trace DAGs retrievable from the analyst service via `trace_for(opinion_id)`; traces are internal provenance objects and do not alter `AnalystOpinion` JSON.

## Analyst endpoints (Phase 7 — Fundamental)

`GET /analysts/fundamental/health` returns `{"status":"healthy","detail":"deterministic offline fundamental formulas"}`.

`GET /analysts/fundamental/metadata` returns analyst capabilities including `analyst_id="fundamental"`, `role="FUNDAMENTAL"`, `research_only=true`, and `suitable_for_live_trading=false`.

`POST /analysts/fundamental/analyze` accepts an `AnalystRequest` whose `extra_context` contains a `fundamentals` key with a `CompanyFundamentals` JSON document. The analyst normalizes, validates, analyzes, and returns an `AnalystOpinion` with evidence grouped by category (growth, profitability, cash_flow, balance_sheet, capital_efficiency, valuation, earnings_quality, shareholder, data_quality). No market data is fetched; all inputs are caller-supplied.

## Data Platform endpoints (Phase 8A)

The Unified Research Data Platform provides read-only, offline, point-in-time-safe data endpoints. See [DATA_PLATFORM.md](../DATA_PLATFORM.md).

| Method | Path | Description |
|---|---|---|
| `GET` | `/data-platform/health` | Health status and platform version |
| `GET` | `/data-platform/sources` | List registered data sources |
| `GET` | `/data-platform/domains` | List supported data domains |
| `GET` | `/data-platform/series` | Query normalized records (domain, symbol, limit filters) |
| `GET` | `/data-platform/calendar` | Query calendar events (start, end, event_type filters) |
| `POST` | `/data-platform/snapshot` | Produce a point-in-time-safe `ResearchDataSnapshot` |

All endpoints require no network by default, return safe structured responses, and never expose internal exceptions. The POST `/data-platform/snapshot` accepts a `SnapshotRequest` JSON body with:

- `as_of` (required, ISO 8601 UTC timestamp)
- `domains` (optional, list of `DataDomain` values)
- `symbols` (optional, list of entity/symbol strings)
- `series_ids` (optional, list of series identifiers)
- `max_records` (optional, positive integer)
- `source_preferences` (optional, list of provider names)
- `allow_partial` (optional, default `false`)
- `research_only` (default `true`)
- `suitable_for_live_trading` (default `false`)

Snapshots are research-only (`research_only=true`, `suitable_for_live_trading=false`). Requests with `suitable_for_live_trading=true` are rejected at validation time.
