# Phase 5: Analyst Framework

## Scope

Phase 5 supplies strict Pydantic opinion/evidence models, the `Analyst` interface, deterministic `MockAnalyst`, confidence and freshness assessment, evidence validation, trace DAG validation, descriptive opinion aggregation, safe stores, API endpoints, and CLI output.

It defines contracts, not specialist intelligence. Technical Analyst begins in Phase 6. The Phase 11 Risk Officer is now implemented as portfolio-proposal research review; Investment Committee and Chairman remain future work. No analyst can create trades, quantities, allocations, orders, or risk approvals.

## Safety boundaries

- `decision_ready=false`, `suitable_for_live_trading=false`, and `research_only=true` are invariant.
- Confidence is bounded and is not certainty; uncalibrated confidence is capped without invented accuracy.
- Evidence that was unavailable at evaluation time is rejected as lookahead.
- Stale and expired evidence is rejected unless an explicit research configuration allows it.
- Trace URIs reject local absolute paths and credentials.
- Aggregation is descriptive only.
