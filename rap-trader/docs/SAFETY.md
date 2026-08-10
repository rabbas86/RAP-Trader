# Safety

Phase 6 is research-only, excludes observations after `as_of`, and has no broker, execution, risk-approval, portfolio-allocation, committee, Chairman, or live-trading integration.

- Offline Kronos predictions are not investment advice, are explicitly unsuitable for live trading, and cannot submit orders or bypass risk controls.
- Kronos uses no network LLM or live stream. Its default market-data dependency is deterministic and offline; provider errors fail to `FLAT` with zero confidence.
- Live trading remains disabled by default. Phase 1 has no real broker integration or order-submission API.
- No LLM, model, UI, or decision service may bypass deterministic risk controls; `ExecutionService` rejects execution without risk approval.
- Secrets and API keys must never be committed or logged.
- Market-data errors expose stable codes and safe messages only; provider diagnostics remain internal.
- Historical data is not an execution quote and may be delayed, partial, synthetic, or adjusted according to explicit policy.
- Requests are bounded by provider limit and date-range rules. Naive timestamps are rejected to prevent silent timezone and DST interpretation errors.
- Paper trading and realistic backtesting are mandatory before later readiness reviews.
- Strategy, model, configuration, or prompt changes require documented validation and regression testing.
- Future live mode requires explicit configuration, operational controls, monitoring, reconciliation, and human approval beyond Phase 1.
- Phase 3 Kronos predictions are deterministic offline SMA crossover signals only, marked `LIVE_TRADING_SUITABLE = False`. They are not investment advice and do not bypass risk controls. Predictions are cached and reproducible for identical inputs.
- Phase 4 backtesting is research-only. It does not submit orders, execute trades, or invoke any broker, execution, order, risk, or portfolio service. Every backtest result carries `research_only=True` and `suitable_for_live_trading=False`.
- The backtesting engine enforces hard no-lookahead runtime guards: forecast timestamps cannot overlap context bars, must match expected target timestamps exactly, and no bar beyond `context_end` is ever returned by the market-data provider.
- Backtest providers are deterministic benchmarks (Mock, SMA, LastValue, Drift), not learned models. Results do not constitute investment advice and are subject to overfitting risk.
- Default backtests use offline mock market data. No network access or model download occurs. LocalKronosProvider remains opt-in and lazily loaded.
# Phase 5 analyst safety

Analysts cannot create trades. Their outputs permanently declare `decision_ready=false`, `suitable_for_live_trading=false`, and `research_only=true`. Confidence is not certainty and uncalibrated confidence is capped. Availability timestamps prevent lookahead, freshness policies reject stale evidence, and provenance/trace validation prevents local path and credential leakage. Aggregation is descriptive only; Risk Officer, Committee, and Chairman are future phases.

# Phase 6.5 safety — Market Intelligence Feature Platform

The Market Intelligence Feature Platform (MIFP) is research-only and does not connect to any execution, risk, portfolio, broker, committee, or Chairman service.

- **No broker or execution.** MIFP source tree contains no imports of `Broker`, `PaperBroker`, `ExecutionService`, `OrderRequest`, `RiskEngine`, `PortfolioManager`, `InvestmentCommittee`, or `Chairman`. Tests assert this with a forbidden-token scan.
- **No LLM or model download.** Feature generators are deterministic mathematical functions on historical bar data. No neural model, LLM, or network call occurs in the standard test path. `LocalKronosProvider` remains opt-in and lazily loaded.
- **No network.** The default `MockMarketDataProvider` generates synthetic bars deterministically from a seed. No external API call is made during feature computation or analysis.
- **No credentials.** MIFP never reads, stores, or transmits API keys, tokens, passwords, or secrets.
- **No-lookahead enforcement.** Every `FeatureValue` carries `observed_at`, `available_at`, and `generated_at`. The `FeatureSnapshot` model validator rejects any feature whose `available_at > as_of`. For external features (Kronos forecasts, backtest results), `available_at` reflects when the external information became available, not the bar timestamp — preventing implicit future information leakage.
- **Immutable snapshots.** `FeatureSnapshot` is frozen (`strict=True`, `frozen=True`, `extra="forbid"`). Tests verify mutation raises `ValidationError` and that serialization round-trips identically.
- **Cache identity safety.** Cache keys are SHA-256 hashes of all computation-relevant dimensions (ticker, timeframe, provider, adjustment, session, as_of, lookback, configuration hash, schema version, Kronos/backtest fingerprints). Different inputs never collide. Only fingerprints (not raw payloads) are stored in keys.
- **Versioned schemas.** `FEATURE_SCHEMA_VERSION`, `PLATFORM_VERSION`, and `GENERATOR_VERSION` are embedded in `FeatureMetadata`, `FeatureProvenance`, and cache keys. Incompatible versions do not share cached results.
- **Technical Analyst remains research-only.** Every opinion carries `research_only=True`, `suitable_for_live_trading=False`, and `decision_ready=False`. The opinion is not a trading decision and cannot be escalated to one without explicit future Phase 7 readiness review.
