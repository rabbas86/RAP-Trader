"""Structured research-gap questions; no recursive agents or conversations."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.committee import CommitteeMemberRole, CommitteeQuestion
from app.services.committee.research_case import ResearchCase
from app.services.committee.risk_governance import RiskGovernance


class CommitteeQuestionService:
    def build(self, case: ResearchCase, risk: RiskGovernance) -> tuple[CommitteeQuestion, ...]:
        questions = [
            self._question(f"missing-{role.value}", "missing_research", f"Obtain the missing {role.value} view", (role,), "critical", True)
            for role in case.missing_roles
        ]
        if risk.required_modifications:
            questions.append(
                self._question(
                    "risk-modifications",
                    "risk_modification",
                    "How will the portfolio satisfy the Risk Officer's required modifications?",
                    (CommitteeMemberRole.PORTFOLIO_MANAGER, CommitteeMemberRole.RISK_OFFICER),
                    "critical",
                    True,
                )
            )
        return tuple(questions)

    @staticmethod
    def _question(
        key: str, topic: str, description: str, roles: tuple[CommitteeMemberRole, ...], priority: str, blocking: bool
    ) -> CommitteeQuestion:
        return CommitteeQuestion(
            question_id=str(uuid5(NAMESPACE_URL, f"committee-question:{key}")),
            topic=topic,
            description=description,
            requested_from=roles,
            priority=priority,
            blocking=blocking,
            evidence_gap=description,
        )
