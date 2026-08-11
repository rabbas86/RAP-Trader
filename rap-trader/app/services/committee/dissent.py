"""First-class minority-view preservation."""

from app.domain.models.committee import CommitteeDissent
from app.services.committee.alignment import CommitteeAlignment
from app.services.committee.research_case import ResearchCase


class CommitteeDissentService:
    def identify(self, case: ResearchCase, alignment: CommitteeAlignment) -> tuple[CommitteeDissent, ...]:
        return tuple(
            CommitteeDissent(
                dissenting_role=view.role,
                view=view.direction_or_status,
                confidence=view.confidence,
                reason=f"View differs from majority {alignment.majority_direction.value}",
                severity="high" if view.role in alignment.strong_minority_roles else "moderate",
                acknowledged=True,
                blocking=view.role in alignment.strong_minority_roles,
            )
            for view in case.views
            if view.direction_or_status != alignment.majority_direction.value
        )
