# API

## `GET /health`

Returns HTTP 200 with service health and the active trading mode.

## `GET /system/status`

Returns application name, environment, trading mode, live-trading flag, and version. Neither endpoint submits orders, and Phase 1 exposes no trading endpoint.

## `GET /market-data/health`

Returns provider health as `{"healthy": true}`.

## `GET /market-data/timeframes`

Returns supported timeframes as `{"timeframes": ["1m", "5m", "15m", "1h", "1d", "1w"]}`.

## `GET /market-data/bars`

Query parameters: `symbol` (required), `timeframe` (required; one of the supported timeframes), `start` (required, ISO 8601 datetime), `end` (required, ISO 8601 datetime), `limit` (optional, positive integer, max 10000).

Returns normalized historical bars for the requested symbol/timeframe range. All timestamps are UTC. The default provider is deterministic and makes no network calls; `yfinance` is an opt-in adapter available at the service boundary. No endpoint submits orders or connects to brokerage.
