"""Cross-specialist alignment metrics."""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.analyst import AnalysisDirection
from app.domain.models.committee import CommitteeMemberRole
from app.services.committee.research_case import ResearchCase


class CommitteeAlignment(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    directional_agreement: float = Field(ge=0, le=1)
    disagreement: float = Field(ge=0, le=1)
    confidence_dispersion: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    freshness: float = Field(ge=0, le=1)
    majority_direction: AnalysisDirection
    strong_minority_roles: tuple[CommitteeMemberRole, ...]


class CommitteeAlignmentService:
    def calculate(self, case: ResearchCase, required_count: int, dissent_threshold: float) -> CommitteeAlignment:
        directional = [view for view in case.views if view.direction_or_status in {"BULLISH", "BEARISH", "NEUTRAL"}]
        counts = {
            direction: sum(view.direction_or_status == direction.value for view in directional)
            for direction in (AnalysisDirection.BULLISH, AnalysisDirection.BEARISH, AnalysisDirection.NEUTRAL)
        }
        majority = max(counts, key=lambda item: (counts[item], item.value)) if directional else AnalysisDirection.INSUFFICIENT_EVIDENCE
        agreement = counts.get(majority, 0) / len(directional) if directional else 0.0
        confidences = [view.confidence for view in case.views]
        dispersion = max(confidences) - min(confidences) if confidences else 1.0
        fresh = sum(not view.freshness.is_stale for view in case.views if view.freshness is not None)
        freshness = fresh / len(case.views) if case.views else 0.0
        minority = tuple(
            view.role for view in case.views if view.direction_or_status != majority.value and view.confidence >= dissent_threshold
        )
        return CommitteeAlignment(
            directional_agreement=agreement,
            disagreement=1 - agreement,
            confidence_dispersion=dispersion,
            coverage=min(1.0, len(case.views) / required_count) if required_count else 1.0,
            freshness=freshness,
            majority_direction=majority,
            strong_minority_roles=minority,
        )
