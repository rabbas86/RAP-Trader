# Phase 13 — Chairman

Phase 13 implements the final research-governance authority above the Investment Committee and Risk Officer. The implementation is deterministic, point-in-time validated, provenance-complete, traceable, and entirely offline.

The flow is `PortfolioProposal + RiskAssessment + RiskDecision + CommitteeAssessment + CommitteeRecommendation -> Governance Review -> ChairmanAssessment -> ChairmanDecision`. The trace contains no order, broker, trading, or execution node.

Decision outcomes are `approve_research`, `revise_research`, `reject_research`, `defer`, and `insufficient_evidence`. All are research-only and none authorizes execution. Risk rejection is absolute; insufficient risk evidence cannot be promoted; committee advice can only be retained or made more conservative.
