# Architecture

RAP Trader is a modular monolith. API routes translate HTTP requests; domain models define contracts; services isolate market data, analysis, decisions, risk, portfolio state, brokerage, and execution.

Phase 2's market-data boundary is `MarketDataProvider`. `MockMarketDataProvider` is the default dependency and deterministically generates bars for a small allowlist without network access. `YFinanceMarketDataProvider` is an opt-in adapter that maps public yfinance responses into internal `OHLCVBar` contracts, applies bounded exponential-backoff retries and request timeouts, and translates failures into `MarketDataError`. Both implementations use the replaceable `AbstractCache`; the default `InMemoryCache` provides TTL expiry and least-recently-used bounded eviction.

Market-data models normalize timestamps to UTC and validate symbols, timeframes, date ranges, OHLC relationships, volume, ordering, and duplicate timestamps. The `/market-data` API is read-only and has no connection to decisions, risk assessment, execution, or brokerage.

The planned Kronos adapter will implement the existing prediction interface after offline validation. A future AI investment committee may fuse Kronos, technical, fundamental, and news evidence, but its output remains advisory. The intended execution flow is decision -> deterministic risk assessment -> `ExecutionService` -> broker adapter. `ExecutionService.execute_approved` rejects calls whose `risk_approved` argument is false.

Broker implementations conform to one interface. Phase 1 includes only an in-memory paper simulator with process-local order state and no network or credential dependencies; future adapters must preserve idempotency, explicit environment controls, and paper/live separation. Phase 1 exposes no order-submission API.

Production readiness requires an immutable audit trail correlating request, decision, risk assessment, and order IDs, including configuration/model versions and outcomes without secrets.
