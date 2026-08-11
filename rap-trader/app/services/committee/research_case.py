"""Research-case assembly without collapsing specialist views."""

from pydantic import BaseModel, ConfigDict

from app.domain.models.analyst import AnalysisDirection, AnalystOpinion, AnalystRole
from app.domain.models.committee import CommitteeMemberRole, CommitteeMemberView

_ROLES = {
    AnalystRole.TECHNICAL: CommitteeMemberRole.TECHNICAL_ANALYST,
    AnalystRole.FUNDAMENTAL: CommitteeMemberRole.FUNDAMENTAL_ANALYST,
    AnalystRole.MACRO: CommitteeMemberRole.MACRO_ECONOMIST,
    AnalystRole.NEWS: CommitteeMemberRole.NEWS_ANALYST,
}


class ResearchCase(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    views: tuple[CommitteeMemberView, ...]
    directions: dict[CommitteeMemberRole, AnalysisDirection]
    missing_roles: tuple[CommitteeMemberRole, ...]


class ResearchCaseAssemblyService:
    def assemble(self, opinions: list[AnalystOpinion], required: tuple[CommitteeMemberRole, ...]) -> ResearchCase:
        views = tuple(
            CommitteeMemberView(
                role=_ROLES[item.analyst_role],
                source_id=item.opinion_id,
                source_version=item.model_identity.model_version if item.model_identity else "unspecified",
                direction_or_status=item.direction.value,
                confidence=item.confidence.value,
                freshness=item.data_freshness,
                summary=f"{item.analyst_role.value} view is {item.direction.value}",
                warnings=tuple(w.message for w in item.warnings),
                limitations=tuple(w.message for w in item.limitations),
                available_at=item.generated_at,
                provenance_reference=f"opinion:{item.opinion_id}",
            )
            for item in sorted(opinions, key=lambda value: value.analyst_role.value)
            if item.analyst_role in _ROLES
        )
        present = {view.role for view in views}
        return ResearchCase(
            views=views,
            directions={view.role: AnalysisDirection(view.direction_or_status) for view in views},
            missing_roles=tuple(role for role in required if role not in present),
        )
