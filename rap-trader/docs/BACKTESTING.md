# Backtesting

Phase 6 can consume a serialized Phase 4 `backtest_result` as calibrated research evidence without making an opinion decision-ready or live-trading suitable.

Phase 4 adds reproducible, offline, deterministic walk-forward backtesting for forecast quality
evaluation. Backtesting is strictly **research-only**: it does not submit orders, execute trades,
or invoke any broker, execution, risk-approval, portfolio-allocation, or live-trading component.

Every backtest result carries `research_only=True` and `suitable_for_live_trading=False`.

## Design goals

* **No lookahead.** Target-bar timestamps never appear in the context window. The engine enforces
  this at runtime with explicit guards.
* **Determinism.** Identical inputs always produce identical outputs. No randomness beyond seeded
  mock providers.
* **Full offline.** The default suite uses `MockMarketDataProvider` and deterministic benchmark
  forecast providers. No network access, no model download, no broker connection.
* **Walk-forward evaluation.** Historical data is split into non-overlapping evaluation windows,
  each with a context (history available to the forecast) and a target (future bars to compare
  against).

## Architecture

```
app/services/backtesting/
  engine.py     — No-lookahead walk-forward engine (BacktestEngine, EvaluationWindowGenerator)
  runner.py     — High-level orchestrator (BacktestRunner)
  evaluator.py  — Forecast-versus-actual metrics (ForecastEvaluator)
  providers.py  — Deterministic benchmark forecast providers
  regime.py     — Market-regime classification (MarketRegimeClassifier)
  research.py   — Research-only LONG/SHORT/FLAT signal simulation
  costs.py      — Transaction-cost and slippage models
  store.py      — Result persistence (in-memory + atomic JSON file)
  __init__.py   — Public exports
```

## Walk-forward evaluation

The `BacktestRunner` orchestrates the full pipeline:

1. Fetch the full historical dataset from a `MarketDataProvider` (default: `MockMarketDataProvider`).
2. Generate walk-forward evaluation windows using `EvaluationWindowGenerator`.
3. For each window, run each forecast provider through `BacktestEngine`.
4. Aggregate metrics, regime distribution, and (optionally) research signal simulation.
5. Return a `BacktestRunResult` with `research_only=True`.

### Context and target separation

Each `EvaluationWindow` defines:

* **context_start** to **context_end**: the historical bars available to the forecast provider.
* **target_start** to **target_end**: the future bars that actuals are compared against.

The gap between `context_end` and `target_start` is exactly one timeframe step, preventing overlap.

### Explicit lookahead prevention

`BacktestEngine` enforces hard runtime invariants:

1. **Target bars never appear in context.** Every forecast bar timestamp is checked against the
   context bar timestamps. Any overlap raises `BacktestError(LOOKAHEAD_DETECTED)`.
2. **Misaligned timestamps are rejected.** Forecast bar timestamps must match the expected target
   timestamps exactly. Any mismatch raises `BacktestError(MISALIGNED_TIMESTAMPS)`.
3. **Future information is blocked.** The engine only requests bars up to `context_end` from the
   market-data provider. Any bar beyond `context_end` raises `BacktestError(FUTURE_INFORMATION)`.
4. **Duplicate timestamps are rejected.**
5. **Maximum-window enforcement.** `max_windows` caps the total number of windows.

All comparisons use UTC-normalized timestamps.

## Forecast alignment

The engine generates expected target timestamps from `context_end + one_step * (1..horizon)`
and verifies that each forecast's bar timestamps match exactly. This prevents providers from
returning bars at shifted or wrong timestamps.

## Benchmark providers

Phase 4 includes four deterministic benchmark providers, all implementing the Phase 3
`KronosForecastProvider` interface:

| Provider | Model ID | Description |
|---|---|---|
| `MockKronosProvider` | `mock-kronos-v0` | Seeded random-walk synthetic forecast |
| `SMAForecastProvider` | `sma-baseline-v1` | 5/20-period SMA crossover (Phase 3, re-exported) |
| `LastValueForecastProvider` | `last-value-v1` | Repeats the last close for every future bar |
| `DriftForecastProvider` | `drift-v1` | Extrapolates the mean historical return |

All benchmark providers are deterministic, offline, and `LIVE_TRADING_SUITABLE = False`.

## Forecast accuracy metrics

`ForecastEvaluator` computes the following deterministic metrics on the close-price series:

* **MAE** — Mean Absolute Error
* **RMSE** — Root Mean Square Error
* **Median Absolute Error**
* **SMAPE** — Symmetric Mean Absolute Percentage Error
* **Bias** — mean(forecast - actual)
* **Max Error**
* **Correlation** — Pearson correlation (None if undefined)
* **Directional Accuracy** — fraction of direction predictions correct
* **Sign Accuracy** — sign(pred - actual_prev) == sign(actual - actual_prev)
* **Hit Rate** — fraction of times forecast direction matches actual direction
* **Interval Coverage** — fraction of actuals within the forecast range
* **Interval Width** — forecast range span / mean actual close

## Regime classification

`MarketRegimeClassifier` assigns each evaluation window to one of six regimes:

* `trending_up` — short SMA above long SMA by a threshold
* `trending_down` — short SMA below long SMA by a threshold
* `range_bound` — price stays within a narrow band
* `high_volatility` — standard deviation of returns exceeds a high threshold
* `low_volatility` — standard deviation of returns below a low threshold
* `unknown` — insufficient data

Thresholds are configurable via `RegimeThresholds`.

## Research-only signal simulation

When `research_simulation=True` is set in the request, `ResearchSignalSimulator` generates
LONG / SHORT / FLAT signals from forecast data and computes performance attribution:

* **LONG** — forecast final close exceeds context last close by a threshold (>1%)
* **SHORT** — forecast final close below context last close by a threshold (only when
  short-selling is enabled; otherwise FLAT)
* **FLAT** — forecast within threshold

Short-selling is **disabled by default**. Leverage defaults to **1.0**. The simulator computes
gross PnL, net PnL after transaction costs and slippage, max drawdown, turnover, and per-period
returns. All results carry `research_only=True` and `suitable_for_live_trading=False`.

## Transaction costs and slippage

`app/services/backtesting/costs.py` defines abstract interfaces and implementations:

* `ZeroCostModel` / `ZeroSlippageModel` — no costs (baseline)
* `FixedBpsCostModel` — fixed basis-points per round-trip trade
* `FixedBpsSlippageModel` — fixed basis-points slippage per trade

Costs and slippage are configurable per backtest via `transaction_cost_bps` and `slippage_bps`
request fields.

## Persistence

`app/services/backtesting/store.py` provides:

* `BacktestResultStore` — abstract interface
* `InMemoryBacktestResultStore` — thread-safe, TTL + LRU, process-local
* `JSONFileBacktestResultStore` — atomic JSON file writes to a project-local allowed directory

JSON file behavior:

* **Explicit output only** — nothing is written unless `save` is called.
* **Atomic writes** — results are written to a temp file first, then `os.replace`d into place.
* **Schema version** — every file includes `schema_version` ("1.0").
* **Safe filenames** — backtest IDs are sanitized to prevent path traversal.
* **No pickle** — JSON serialization only.

## API endpoints

All backtesting endpoints are read-only and computational. None submit orders or connect to brokers.

### `POST /backtests/run`

Runs a walk-forward backtest. Accepts a `BacktestRunRequest` JSON body:

| Field | Type | Default | Description |
|---|---|---|---|
| `ticker` | string | required | Ticker symbol (1-10 chars, uppercased) |
| `timeframe` | string | required | Bar timeframe: `1m`, `5m`, `15m`, `1h`, `1d`, `1w` |
| `start` | ISO 8601 datetime | required | Start date (UTC) |
| `end` | ISO 8601 datetime | required | End date (UTC) |
| `lookback` | int | 60 | Historical lookback bars |
| `horizon` | int | 5 | Forecast horizon bars |
| `step` | int | 5 | Walk-forward step in bars |
| `max_windows` | int | null | Maximum number of windows (null = no cap) |
| `seed` | int | 42 | Random seed (for reproducible providers) |
| `include_local_kronos` | bool | false | Include LocalKronosProvider (requires model path) |
| `research_simulation` | bool | false | Run research-only signal simulation |
| `short_selling` | bool | false | Allow short selling in research sim |
| `leverage` | float | 1.0 | Leverage for research sim (1.0-4.0) |
| `transaction_cost_bps` | float | 0.0 | Transaction cost in bps |
| `slippage_bps` | float | 0.0 | Slippage in bps |

Returns a `BacktestRunResult` with `research_only=True` and `suitable_for_live_trading=False`.

### `GET /backtests/providers`

Lists available forecast providers and flags.

### `GET /backtests/{backtest_id}`

Retrieves a full stored backtest result by ID.

### `GET /backtests/{backtest_id}/summary`

Retrieves a lightweight summary (best provider by RMSE, mean MAE/RMSE per provider, regime
distribution).

## CLI usage

```shell
python -m app.cli.backtest \
    --ticker AAPL \
    --timeframe 1d \
    --start 2025-01-01 \
    --end 2025-06-01 \
    --lookback 60 \
    --horizon 5 \
    --step 5 \
    --max-windows 10 \
    --output-dir backtest_results/
```

Defaults to offline mock data and mock/baseline Kronos forecasts. No server is started, no network
or model download occurs. Use `--research-simulation` to enable signal simulation with configurable
costs and slippage.

## Reproducibility metadata

Every `BacktestRunResult` includes:

* A deterministic `backtest_id` (SHA-256 hash of the request payload)
* `schema_version` ("1.0")
* `research_only=True`
* `suitable_for_live_trading=False`
* `created_at` and `completed_at` in UTC
* Per-provider mean metrics, regime breakdown, and optional cost results

## Bias and limitation disclosures

* **Overfitting risk.** Benchmarks are deterministic heuristics, not learned models. Results do not
  constitute investment advice.
* **Survivorship bias.** Mock data does not exhibit survivorship bias; real data may.
* **Selection bias.** Provider ranking is based on historical fit over the evaluation period.
* **Data quality.** Mock data is synthetic and not exchange-calendar accurate. Bar timestamps
  are regularly spaced for testing but not aligned to real trading calendars.
* **No live trading.** All results are research-only. See `docs/SAFETY.md`.

## No-lookahead controls summary

| Control | Guard | Error code |
|---|---|---|
| Forecast timestamps not in context | `_check_no_lookahead` | `LOOKAHEAD_DETECTED` |
| Forecast timestamps match expected targets | `_check_alignment` | `MISALIGNED_TIMESTAMPS` |
| Target timestamps not in context | `_check_no_target_in_context` | `TARGET_IN_CONTEXT` |
| No bars beyond context_end | `_fetch_context_bars` post-check | `FUTURE_INFORMATION` |
| No duplicate timestamps | `_check_no_duplicates` | `DUPLICATE_TIMESTAMP` |
| Regular spacing in input | `generate` spacing check | `IRREGULAR_SPACING` |
| Max windows cap | `generate` max_windows check | `MAX_WINDOWS_EXCEEDED` |
