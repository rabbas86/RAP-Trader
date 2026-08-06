# RAP Trader

Phase 3 adds deterministic offline Kronos forecasting over the Phase 2 market-data boundary. Live trading remains disabled, no real-broker adapter or order API exists, and forecasts are not investment advice.

RAP Trader is a modular foundation for an AI-assisted US-equities paper-trading platform. Phase 3 adds deterministic offline Kronos forecasting on top of the Phase 1 safety foundation and Phase 2 validated market data. Live trading remains disabled, no real-broker adapter or order API exists, and paper orders/cache entries remain process-local.

The default market-data provider is deterministic, synthetic, and offline. The isolated yfinance adapter is opt-in and uses no paid service. The default Kronos service is an offline simple-moving-average (SMA) crossover model that consumes normalized historical bars; it is deterministic, offline, and not suitable for live trading.

## Install and run locally

Python 3.12 or newer is required.

```shell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Market data

Read-only endpoints are `GET /market-data/health`, `GET /market-data/timeframes`, and `GET /market-data/bars`. Bar queries accept a symbol, timeframe, timezone-aware start/end, optional limit, adjustment, and session. Mock symbols are AAPL, MSFT, GOOG, TSLA, SPY, BRK.B, and BF.B; timeframes are 1m, 5m, 15m, 1h, 1d, and 1w. Mock output is synthetic and not exchange-calendar accurate. Limits and date-range policies bound generation.

Adjustment policies are `raw` (reported OHLC), `split_adjusted` (split-adjusted OHLC), and `total_return_adjusted` (splits plus distributions; currently rejected). Session policies are `regular`, `extended`, and `all`; `regular` is the default. All accepted timestamps and provenance times are normalized to UTC. yfinance translates class-share symbols such as BRK.B to BRK-B.

## Kronos offline forecasting

Read-only endpoints are `GET /kronos/health` and `GET /kronos/prediction`. The prediction endpoint accepts `ticker`, `timeframe`, timezone-aware ISO 8601 `start`, `end`, and an optional `limit`. It returns a `KronosPrediction` with direction (UP/DOWN/FLAT), confidence (0-1), expected_return, time_horizon, generated_at, model_version, and bar provenance (timeframe, source_provider, data_start, data_end).

The default `OfflineKronosService` fetches historical bars from the configured `MarketDataProvider` (mock by default) and applies a deterministic SMA crossover strategy: a 5-period short SMA versus a 20-period long SMA. The prediction is UP when the short SMA exceeds the long SMA, DOWN when below, and FLAT when equal or insufficient data. All computations are offline and deterministic for identical inputs.

Predictions are advisory only and are not investment advice. The `WaitDecisionEngine` remains the default decision path and always produces WAIT. Risk controls are unchanged.

## Test and check

```shell
pytest -v
ruff check .
ruff format --check .
mypy app --strict
```

## Structure

- `app/api`: HTTP routes
- `app/domain/models`: validated contracts
- `app/services`: integration boundaries and deterministic business services
- `tests`: unit and API tests
- `docs`: API, architecture, roadmap, and safety notes

## Safety limitations

Predictions and decisions are placeholders, not investment advice. Market data may be delayed, incomplete, synthetic, or adjusted and is never an execution quote. Kronos predictions are deterministic offline signals and are not suitable for live trading. Deterministic risk controls remain mandatory and no decision model can override them. See `docs/SAFETY.md`.
