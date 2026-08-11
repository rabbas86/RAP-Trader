"""Deterministic cross-functional conflict classification."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.committee import CommitteeConflict, CommitteeMemberRole
from app.services.committee.portfolio_review import PortfolioReview
from app.services.committee.research_case import ResearchCase
from app.services.committee.risk_governance import RiskGovernance


class CommitteeConflictService:
    def identify(self, case: ResearchCase, portfolio: PortfolioReview, risk: RiskGovernance) -> tuple[CommitteeConflict, ...]:
        results: list[CommitteeConflict] = []
        pairs = [
            ("technical_vs_fundamental", CommitteeMemberRole.TECHNICAL_ANALYST, CommitteeMemberRole.FUNDAMENTAL_ANALYST),
            ("company_vs_macro", CommitteeMemberRole.FUNDAMENTAL_ANALYST, CommitteeMemberRole.MACRO_ECONOMIST),
            ("news_vs_fundamental", CommitteeMemberRole.NEWS_ANALYST, CommitteeMemberRole.FUNDAMENTAL_ANALYST),
        ]
        for kind, left, right in pairs:
            if left in case.directions and right in case.directions and case.directions[left] != case.directions[right]:
                refs = tuple(view.source_id for view in case.views if view.role in {left, right})
                results.append(self._make(kind, (left, right), f"{left.value} and {right.value} disagree", "high", refs))
        if portfolio.required_modifications or risk.required_modifications:
            results.append(
                self._make(
                    "portfolio_vs_risk",
                    (CommitteeMemberRole.PORTFOLIO_MANAGER, CommitteeMemberRole.RISK_OFFICER),
                    "Portfolio proposal requires governance modification",
                    "high",
                    tuple(risk.blocking_findings),
                )
            )
        if portfolio.required_modifications and any("Concentration" in item for item in portfolio.findings):
            results.append(
                self._make(
                    "concentration_vs_conviction",
                    (CommitteeMemberRole.PORTFOLIO_MANAGER,),
                    "Concentration is not supported by committee policy",
                    "high",
                    (),
                )
            )
        return tuple(results)

    @staticmethod
    def _make(
        kind: str, roles: tuple[CommitteeMemberRole, ...], description: str, severity: str, refs: tuple[str, ...]
    ) -> CommitteeConflict:
        return CommitteeConflict(
            conflict_id=str(uuid5(NAMESPACE_URL, f"committee-conflict:{kind}:{':'.join(refs)}")),
            conflict_type=kind,
            roles=roles,
            description=description,
            severity=severity,
            unresolved=True,
            evidence_references=refs,
            recommended_followup="Resolve and document this conflict before approval",
        )
