# Architecture

RAP Trader is a modular monolith. API routes translate HTTP requests; domain models define contracts; services isolate market data, analysis, decisions, risk, portfolio state, brokerage, and execution.

Phase 2's boundary is `MarketDataProvider`. `MockMarketDataProvider` deterministically generates a bounded number of synthetic bars without network access. `YFinanceMarketDataProvider` is opt-in, maps and validates public responses, localizes source timestamps using exchange-timezone policy, applies bounded retries/timeouts, and translates failures into stable safe errors. Both use `AbstractCache`; SHA-256 keys include provider identity, adjustment/session semantics, and provider configuration to prevent cross-provider and cross-policy collisions. The default in-memory implementation provides TTL expiry and bounded LRU eviction.

Market-data models reject naive timestamps, normalize aware timestamps to UTC, support class-share symbols, and validate ranges, OHLC relationships, finite prices, volume, ordering, and duplicates. Successful results cannot be empty and carry complete range, policy, and retrieval provenance. Provider health is structured and intentionally non-invasive. The read-only market-data API has no connection to decisions, risk, execution, or brokerage.

The planned Kronos adapter remains future work after offline validation. A future evidence-fusion layer remains advisory. The execution path continues to require deterministic risk approval before `ExecutionService` can reach a broker adapter.

Phase 1 includes only an in-memory paper simulator with process-local state and no real-broker adapter. Future adapters must preserve idempotency, environment controls, and paper/live separation. Production readiness also requires an immutable audit trail without secrets.
