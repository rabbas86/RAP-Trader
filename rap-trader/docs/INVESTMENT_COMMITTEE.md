# Investment Committee

Phase 12 is a deterministic, offline cross-functional research-governance layer. It preserves the Technical Analyst, Fundamental Analyst, Macro Economist, and News Analyst views; reviews the Phase 10 `PortfolioProposal`; and applies the Phase 11 `RiskAssessment` and `RiskDecision` before producing a `CommitteeAssessment` and `CommitteeRecommendation`.

Every output is `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false`. An approval means only that a research proposal passed committee policy. It cannot create an order, call a broker, execute a trade, or authorize live trading. `requires_chairman_review=true` records that the separate, unimplemented Chairman stage would still be required.

## Governance and precedence

The committee is not a weighted vote. It retains every member view, its confidence, freshness, warnings, limitations, source version, and provenance reference. Deterministic precedence is:

1. Invalid or mismatched point-in-time inputs are rejected safely.
2. Risk `REJECT` forces `reject_research_proposal` and cannot be overridden.
3. Risk `INSUFFICIENT_DATA` forces `insufficient_evidence`.
4. Risk `REQUIRE_MODIFICATION` forces `revise_research_proposal`.
5. Missing mandatory coverage, high unresolved conflicts, weak freshness/data quality, or weak confidence prevent approval.
6. Only coherent research, an acceptable portfolio, and approved risk may produce `approve_research_proposal`.

Risk approval is necessary, never sufficient. Committee policy defaults require all four specialists, full coverage, 0.75 freshness, 0.70 data quality, 0.60 committee confidence, no unresolved high conflict, mandatory risk approval, and a 0.75 dissent escalation threshold.

## Research case, conflict, and dissent

Research-case assembly preserves directional status, confidence, evidence coverage, freshness, warnings, limitations, and analyst version without immediately reducing them to one score. Alignment reports agreement, disagreement, confidence dispersion, coverage, freshness, majority direction, and strong minority roles.

Conflict classification covers specialist disagreement, company-versus-macro evidence, news-versus-fundamentals, portfolio-versus-risk, and concentration-versus-conviction. Unresolved high conflicts defer or revise according to policy. A high-confidence minority view remains a `CommitteeDissent`, reduces committee confidence, and may be blocking. Consensus is never fabricated.

Structured `CommitteeQuestion` records identify missing specialist research and required risk modifications. They are data records only; Phase 12 does not launch agents or conversations.

## Portfolio and risk review

Portfolio review inspects whether weights, cash, concentration, and conviction respect the research case. It does not rerun optimization or reconstruct the proposal. Risk governance retains blocking breaches and required modifications verbatim. No committee rule can override a Risk Officer rejection.

## Provenance and trace

Provenance includes every opinion ID and analyst version, proposal ID and algorithm version, risk assessment and decision IDs, risk service version, committee service version, a SHA-256 policy fingerprint, deterministic input fingerprint, and Git commit when available. The UUID5 trace is an acyclic research DAG from opinions through research case, alignment/conflicts, portfolio, risk assessment/decision, and committee assessment. It contains no execution nodes.

## API and CLI

The API exposes `GET /committee/health`, `GET /committee/metadata`, `POST /committee/assess`, and `POST /committee/review`. There is no execute, order, broker, or Chairman endpoint.

Run the offline CLI with:

```shell
python -m app.cli.committee --opinions-json opinions.json --proposal-json proposal.json --risk-assessment-json risk-assessment.json --risk-decision-json risk-decision.json --json
```

Optional flags are `--policy-json`, `--as-of`, `--summary`, and `--output`. Inputs must be complete local JSON documents with compatible UTC as-of timestamps.

## Limitations

The policy is intentionally conservative and rule-based. It does not forecast, optimize, converse, access a network, use an LLM, download a model, or validate market suitability. Outputs remain research artifacts and are not investment advice or execution authorization.
