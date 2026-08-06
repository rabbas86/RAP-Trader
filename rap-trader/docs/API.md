# API

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
