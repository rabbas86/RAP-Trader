# Safety

- Live trading is disabled by default. Phase 1 contains no real broker integration or order-submission API.
- No LLM, model, user interface, or decision service may bypass deterministic risk controls; `ExecutionService` raises `PermissionError` when risk approval is false.
- Secrets and API keys must never be committed or logged.
- Market-data adapters must never leak provider internals (column names, exception details, or raw response objects) to callers; all failures are translated to structured `MarketDataError` with stable codes and safe messages.
- Paper trading and realistic backtesting are mandatory before later readiness reviews.
- Strategy, model, configuration, or prompt changes require documented validation and regression testing.
- Enabling future live mode requires explicit configuration, operational controls, monitoring, reconciliation, and human approval beyond Phase 1.
