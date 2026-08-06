# Safety

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
