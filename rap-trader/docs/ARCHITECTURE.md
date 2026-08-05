# Architecture

RAP Trader is a modular monolith. API routes translate HTTP requests; domain models define contracts; services isolate market data, analysis, decisions, risk, portfolio state, brokerage, and execution.

The planned Kronos adapter will implement the existing prediction interface after offline validation. A future AI investment committee may fuse Kronos, technical, fundamental, and news evidence, but its output remains advisory. Execution follows a strict decision → deterministic risk assessment → broker adapter flow. Risk rejection cannot be overridden.

Broker implementations conform to one interface. Phase 1 includes only an in-memory paper adapter; future adapters must preserve idempotency, explicit environment controls, and paper/live separation.

Production readiness requires an immutable audit trail correlating request, decision, risk assessment, and order IDs, including configuration/model versions and outcomes without secrets.
