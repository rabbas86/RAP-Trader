# API

## `GET /health`

Returns HTTP 200 with service health and the active trading mode.

## `GET /system/status`

Returns application name, environment, trading mode, live-trading flag, and version. Neither endpoint submits orders, and Phase 1 exposes no trading endpoint.

## `GET /market-data/health`

Returns a `ProviderHealth` object: `provider` (str), `configured` (bool), `reachable` (bool or null), `checked_at` (UTC datetime), `status` (str), and `detail` (str).

## `GET /market-data/timeframes`

Returns supported timeframes as `{"timeframes": ["1m", "5m", "15m", "1h", "1d", "1w"]}`.

## `GET /market-data/bars`

Query parameters: `symbol` (required string), `timeframe` (required), `start` (required, ISO 8601 datetime), `end` (required, ISO 8601 datetime), `limit` (optional, positive integer, max 100000), `adjustment` (optional, `raw`/`split_adjusted`/`total_return_adjusted`; default `raw`), `session` (optional, `regular`/`extended`/`all`; default `regular`).

Returns a normalized `HistoricalBarsResult` as JSON. All timestamps are UTC. Errors return a structured `{"code": ..., "safe_message": ...}` body. The default provider is deterministic and makes no network calls; `yfinance` is an opt-in adapter available at the service boundary and requires no API key. No endpoint submits orders or connects to brokerage.
