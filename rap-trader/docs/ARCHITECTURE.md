# Architecture

RAP Trader is a modular monolith. API routes translate HTTP requests; domain models define contracts; services isolate market data, analysis, decisions, risk, portfolio state, brokerage, and execution.

The planned Kronos adapter will implement the existing prediction interface after offline validation. A future AI investment committee may fuse Kronos, technical, fundamental, and news evidence, but its output remains advisory. The intended execution flow is decision -> deterministic risk assessment -> `ExecutionService` -> broker adapter. `ExecutionService.execute_approved` rejects calls whose `risk_approved` argument is false.

Broker implementations conform to one interface. Phase 1 includes only an in-memory paper simulator with process-local order state and no network or credential dependencies; future adapters must preserve idempotency, explicit environment controls, and paper/live separation. Phase 1 exposes no order-submission API.

Production readiness requires an immutable audit trail correlating request, decision, risk assessment, and order IDs, including configuration/model versions and outcomes without secrets.
