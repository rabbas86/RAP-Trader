# Chairman

Phase 13 adds the deterministic, offline Chairman research-governance authority. It consumes the Phase 12 `CommitteeAssessment` and `CommitteeRecommendation`, the Phase 10 `PortfolioProposal`, and the Phase 11 `RiskAssessment` and `RiskDecision`. It produces an immutable `ChairmanAssessment` and `ChairmanDecision`.

The Chairman is not an analyst, portfolio manager, risk engine, or execution engine. It does not create orders, execute trades, call a broker or `ExecutionService`, bypass the Risk Officer, or bypass the Investment Committee. Every output is `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`. `approve_research` means only that the research-governance record passed Chairman policy.

## Precedence

Inputs fail closed in this order: invalid inputs; Risk `REJECT`; Risk `INSUFFICIENT_DATA`; missing governance; critical unresolved conflict; committee revision; committee approval. A Risk `REJECT` is never overridden, and insufficient risk data never becomes approval. The Chairman may be more conservative than the committee, never less.

Governance covers committee completeness, required roles, risk precedence, conflicts, dissent, proposal consistency, freshness, data quality, provenance, trace completeness, and missing evidence. Findings create blocking questions where appropriate. SHA-256 fingerprints and UUID5 identifiers make identical reviews deterministic.

The API exposes `GET /chairman/health`, `GET /chairman/metadata`, `POST /chairman/assess`, and `POST /chairman/review`. There is deliberately no execution endpoint. Run `python -m app.cli.chairman --help` for the offline CLI.
