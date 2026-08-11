"""Chairman governance questions derived from recorded gaps."""

from uuid import NAMESPACE_URL, uuid5

from app.domain.models.chairman import ChairmanFinding, ChairmanQuestion


class ChairmanQuestionService:
    def build(self, findings: tuple[ChairmanFinding, ...]) -> tuple[ChairmanQuestion, ...]:
        return tuple(
            ChairmanQuestion(
                question_id=str(uuid5(NAMESPACE_URL, f"chairman-question:{item.finding_id}")),
                topic=item.category,
                blocking=item.severity in {"high", "critical"},
                assigned_role="investment_committee" if item.category != "risk_precedence" else "risk_officer",
                description=f"Resolve governance finding: {item.summary}",
            )
            for item in findings
            if item.severity in {"high", "critical"}
        )
