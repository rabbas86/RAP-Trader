# Kronos Forecasting

Kronos is the forecasting layer of RAP Trader. It produces deterministic, offline OHLCV bar
forecasts that are consumed by downstream analysis, backtesting, and (eventually) decision
engines.

## Provider architecture

Kronos providers implement the `KronosForecastProvider` interface defined in
`app/services/kronos/service.py`:

```
KronosForecastProvider (ABC)
  ├── MockKronosProvider        — deterministic synthetic forecast
  ├── SMAForecastProvider       — SMA crossover baseline (NOT the official model)
  └── LocalKronosProvider       — adapter for the official Kronos model (lazy-loaded)
```

### MockKronosProvider

* **Model ID:** `mock-kronos-v0`
* Deterministic, fully offline. Produces synthetic future OHLCV bars using a seeded random
  walk. The seed is derived from request parameters (ticker, timeframe, start, end, lookback,
  horizon).
* `LIVE_TRADING_SUITABLE = False`

### SMAForecastProvider

* **Model ID:** `sma-baseline-v1`
* Applies a 5-period short SMA vs 20-period long SMA crossover over historical bars.
* This is a **heuristic baseline**, explicitly NOT the official Kronos foundation model.
* Falls back to a flat zero-confidence forecast when insufficient data.
* `LIVE_TRADING_SUITABLE = False`

### LocalKronosProvider

* **Model IDs:** `kronos-mini`, `kronos-small`, `kronos-base`
* Adapter for the official Kronos foundation model. Model and tokenizer are loaded lazily on
  first forecast call via `from model import Kronos, KronosPredictor, KronosTokenizer`.
* No model is downloaded at import or startup. Inference runs only on explicit forecast calls.
* `offline_only=True` by default prevents remote model loading.
* `LIVE_TRADING_SUITABLE = False`

## Forecast request contract

`KronosForecastRequest` is validated with strict Pydantic models:

| Field | Constraint |
|---|---|
| `ticker` | 1-10 chars, uppercased |
| `model_id` | Must be a supported model identifier |
| `timeframe` | `1m`, `5m`, `15m`, `1h`, `1d`, `1w` |
| `start` / `end` | UTC-aware, `start < end` |
| `lookback` | 1-10,000 |
| `horizon` | 1-100 |

## Forecast result

`KronosForecast` contains future OHLCV bars with full provenance:

* `requested_start` / `requested_end` — what was asked for
* `actual_start` / `actual_end` — what the forecast covers
* `lookback_bars` / `horizon` — bar counts
* `generated_at` — UTC timestamp
* `suitable_for_live_trading` — always `False` for all Kronos providers
* `warning` — optional advisory text

## KronosPrediction

The API-level prediction (`GET /kronos/prediction`) wraps the forecast with direction metrics:

* `direction`: UP / DOWN / FLAT
* `confidence`: 0-1
* `expected_return`: total expected return over the horizon
* `time_horizon`: number of bars
* `model_version`: provider model identifier
* Bar provenance: `timeframe`, `source_provider`, `data_start`, `data_end`

## Safety

* All Kronos providers have `LIVE_TRADING_SUITABLE = False`.
* Predictions are not investment advice.
* The `OfflineKronosService` (Phase 3 API wrapper) fails closed to a zero-confidence `FLAT`
  forecast when the provider cannot produce a result.
* Kronos uses no network LLM or live data stream. Its default market-data dependency is
  deterministic and offline.
* `model`/`torch` imports are deferred to `LocalKronosProvider` and never occur in the default
  offline path.

## Cache isolation

All Kronos providers use `AbstractCache` with SHA-256 keys that include:

* Provider identity
* Ticker, timeframe, start, end
* Lookback, horizon
* Model ID / version

This prevents cross-provider cache collisions.

## Backtesting integration

Phase 4 backtesting uses Kronos providers (including `MockKronosProvider` and `SMAForecastProvider`)
as forecast sources in the walk-forward evaluation loop. See `docs/BACKTESTING.md`.
