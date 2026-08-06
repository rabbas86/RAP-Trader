# Safety

- Live trading remains disabled by default. Phase 1 has no real broker integration or order-submission API.
- No LLM, model, UI, or decision service may bypass deterministic risk controls; `ExecutionService` rejects execution without risk approval.
- Secrets and API keys must never be committed or logged.
- Market-data errors expose stable codes and safe messages only; provider diagnostics remain internal.
- Historical data is not an execution quote and may be delayed, partial, synthetic, or adjusted according to explicit policy.
- Requests are bounded by provider limit and date-range rules. Naive timestamps are rejected to prevent silent timezone and DST interpretation errors.
- Paper trading and realistic backtesting are mandatory before later readiness reviews.
- Strategy, model, configuration, or prompt changes require documented validation and regression testing.
- Future live mode requires explicit configuration, operational controls, monitoring, reconciliation, and human approval beyond Phase 1.
