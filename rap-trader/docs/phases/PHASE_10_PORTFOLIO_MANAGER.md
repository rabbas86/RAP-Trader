# Phase 10 — Portfolio Manager

Phase 10 adds research portfolio construction on branch `feature/phase-10-portfolio-manager`.

## Delivered

- Strict frozen portfolio, contribution, conviction, constraint, and proposal domain contracts.
- Safe opinion validation, signed aggregation, transparent conviction, explicit universes, point-in-time correlations, and diversification metrics.
- Deterministic constraint projection, conviction-weighted construction, and bounded turnover scaling with adjustment records.
- Stable SHA-256 provenance and an acyclic opinion-to-proposal trace.
- Offline FastAPI and CLI surfaces that produce research weights only.

## Non-goals

This phase does not make trade decisions, recommend BUY/SELL orders, size orders, connect to providers, rebalance accounts, or execute anything. It has no dependency on broker, execution, order-request, risk-engine, network, LLM, or model-download facilities.

## Verification

The release gates are the full pytest suite, Ruff check and format check, and strict mypy over `app`. Tests cover contracts, validation, agreement, conviction, constraints, turnover, shorts, correlations, diversification, trace/provenance, API/CLI behavior, determinism, and forbidden dependencies.

