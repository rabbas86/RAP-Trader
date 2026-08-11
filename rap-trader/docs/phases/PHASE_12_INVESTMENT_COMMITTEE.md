# Phase 12 — Investment Committee

Status: implemented.

Phase 12 adds strict frozen committee domain contracts, conservative policy configuration, point-in-time validation, research-case assembly, alignment and dispersion measures, conflict classification, first-class dissent, structured follow-up questions, portfolio review, non-overridable risk governance, deterministic deliberation, SHA-256 provenance, and UUID5 DAG tracing.

The four specialist roles are Technical Analyst, Fundamental Analyst, Macro Economist, and News Analyst. Portfolio Manager and Risk Officer views are included in the assessment. Chairman is deliberately excluded and remains separate.

Recommendation precedence guarantees that `RiskDecision.REJECT` becomes committee rejection, `INSUFFICIENT_DATA` cannot approve, and `REQUIRE_MODIFICATION` becomes revision. Approval is research-proposal governance only: all models remain research-only, unsuitable for live trading, and not decision-ready.

Surfaces: `GET /committee/health`, `GET /committee/metadata`, `POST /committee/assess`, `POST /committee/review`, and `python -m app.cli.committee`. There are no execution or order routes and no network, LLM, model-download, broker, execution-service, risk-engine, live-trading, or Chairman dependencies.

Verification is in `tests/test_investment_committee.py`, including the six mandatory governance cases, validation, alignment, conflicts, portfolio review, dissent, questions, provenance, trace, API, CLI, deterministic IDs, safety invariants, and forbidden-import scanning.
