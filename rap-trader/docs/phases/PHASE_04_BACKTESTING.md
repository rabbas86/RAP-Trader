# Phase 4: Backtesting Framework

## Status: Complete

Phase 4 implements reproducible, offline, deterministic walk-forward backtesting for forecast
quality evaluation. It is **research-only** and does not submit orders, execute trades, or invoke
any broker, execution, risk-approval, portfolio-allocation, or live-trading component.

## Objectives

* Deterministic walk-forward evaluation of forecast providers.
* Explicit no-lookahead controls (enforced at runtime, not just documented).
* Forecast-versus-actual accuracy metrics.
* Benchmark provider ranking (Mock, SMA, LastValue, Drift).
* Market-regime classification per evaluation window.
* Research-only LONG/SHORT/FLAT signal simulation with performance attribution.
* Transaction-cost and slippage modeling.
* In-memory and atomic JSON file persistence.
* Read-only API endpoints and offline CLI.

## Key deliverables

### Domain models (`app/domain/models/backtesting.py`)

| Model | Purpose |
|---|---|
| `BacktestStatus` | PENDING / RUNNING / COMPLETED / FAILED |
| `BenchmarkProvider` | Enum of eligible benchmark providers |
| `ResearchSignal` | LONG / SHORT / FLAT (research-only) |
| `MarketRegime` | TRENDING_UP / TRENDING_DOWN / RANGE_BOUND / HIGH_VOLATILITY / LOW_VOLATILITY / UNKNOWN |
| `ForecastMetricName` | Enum of all metric names |
| `BacktestErrorCodes` | Stable error codes (LOOKAHEAD_DETECTED, FUTURE_INFORMATION, MISALIGNED_TIMESTAMPS, etc.) |
| `BacktestError` | Stable public error with private internal detail |
| `EvaluationWindow` | Walk-forward context/target window with strict validation |
| `ForecastMetrics` | MAE, RMSE, median AE, SMAPE, bias, max error, correlation, directional accuracy, sign accuracy, hit rate, interval coverage/width |
| `CostResult` | Gross/net PnL, total costs, turnover, commission, slippage, max drawdown |
| `ResearchSignalRow` | Single research-signal observation (timestamp, signal, position, price) |
| `ProviderBacktestResult` | Aggregated per-provider result with regime breakdown |
| `BacktestRunRequest` | Request payload with all configuration fields |
| `BacktestRunResult` | Full completed-backtest result |
| `BacktestSummary` | Lightweight summary for API responses |

### Services (`app/services/backtesting/`)

| Module | Key classes |
|---|---|
| `engine.py` | `EvaluationWindowGenerator`, `BacktestEngine` |
| `runner.py` | `BacktestRunner` |
| `evaluator.py` | `ForecastEvaluator` |
| `providers.py` | `BenchmarkForecastProvider`, `MockBenchmarkProvider`, `SMAForecastProvider`, `LastValueForecastProvider`, `DriftForecastProvider` |
| `regime.py` | `MarketRegimeClassifier`, `RegimeThresholds` |
| `research.py` | `ResearchSignalSimulator`, `SignalSimulationConfig` |
| `costs.py` | `CostConfig`, `TransactionCostModel`, `SlippageModel`, `ZeroCostModel`, `ZeroSlippageModel`, `FixedBpsCostModel`, `FixedBpsSlippageModel` |
| `store.py` | `BacktestResultStore`, `InMemoryBacktestResultStore`, `JSONFileBacktestResultStore` |

### API (`app/api/routes/backtests.py`)

* `POST /backtests/run` — run a walk-forward backtest
* `GET /backtests/providers` — list available providers
* `GET /backtests/{backtest_id}` — retrieve full result
* `GET /backtests/{backtest_id}/summary` — retrieve lightweight summary

### CLI (`app/cli/backtest.py`)

`python -m app.cli.backtest` — offline deterministic walk-forward backtest with mock market data
and benchmark providers. No server, no network, no model download.

### Configuration (`app/config.py`)

Two new settings:

* `backtest_offline_only` (default: `true`) — controls whether backtesting is restricted to offline mode
* `backtest_result_dir` (default: `"backtests"`) — directory for JSON result persistence

## No-lookahead controls

The `BacktestEngine` enforces five hard runtime invariants:

1. **Target bars never in context** — `_check_no_lookahead` raises `LOOKAHEAD_DETECTED`
2. **Forecast timestamps match expected targets** — `_check_alignment` raises `MISALIGNED_TIMESTAMPS`
3. **Target timestamps not in context** — `_check_no_target_in_context` raises `TARGET_IN_CONTEXT`
4. **No bars beyond context_end** — `_fetch_context_bars` post-check raises `FUTURE_INFORMATION`
5. **No duplicate timestamps** — `_check_no_duplicates` raises `DUPLICATE_TIMESTAMP`

Plus window generation checks:

6. **Regular spacing** — raises `IRREGULAR_SPACING`
7. **Sorted order** — raises `INVALID_REQUEST` (UNSORTED)
8. **Max windows cap** — raises `MAX_WINDOWS_EXCEEDED`

## Quality gates

All Phase 4 tests pass alongside all existing Phase 1-3 tests:

* `pytest` — 211 passed
* `ruff check .` — passed
* `ruff format --check .` — passed
* `mypy app --strict` — passed

## Safety properties verified

* No broker, execution, order, risk, or portfolio imports in any Phase 4 module.
* No live trading — `suitable_for_live_trading=False` on all results and providers.
* No production `TradeDecision` generation.
* No order creation.
* No position allocation.
* No model download in default (offline) path.
* No network access in default tests.
* No source-data mutation.
* Deterministic output for identical inputs.

See `docs/BACKTESTING.md` for full documentation and `docs/SAFETY.md` for safety notes.
