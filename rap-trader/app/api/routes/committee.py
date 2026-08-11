"""Offline, research-only Investment Committee endpoints."""

import json
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.domain.models.analyst import AnalystOpinion
from app.domain.models.committee import CommitteeAssessment, CommitteeRecommendation
from app.domain.models.market_data import UtcDatetime
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.committee import CommitteeConfig, CommitteeError, InvestmentCommitteeService

router = APIRouter(prefix="/committee", tags=["committee"])


class CommitteeReviewRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    opinions: list[AnalystOpinion]
    proposal: PortfolioProposal
    risk_assessment: RiskAssessment
    risk_decision: RiskDecision
    policy: CommitteeConfig | None = None
    as_of: UtcDatetime | None = None


class CommitteeReviewResponse(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    assessment: CommitteeAssessment
    recommendation: CommitteeRecommendation


@lru_cache
def get_committee_service() -> InvestmentCommitteeService:
    return InvestmentCommitteeService()


Service = Annotated[InvestmentCommitteeService, Depends(get_committee_service)]


def _request(payload: dict[str, Any]) -> CommitteeReviewRequest:
    return CommitteeReviewRequest.model_validate_json(json.dumps(payload))


def _error(exc: ValueError) -> HTTPException:
    if isinstance(exc, CommitteeError):
        return HTTPException(status_code=422, detail={"code": exc.code, "safe_message": exc.safe_message})
    return HTTPException(status_code=422, detail={"code": "INVALID_COMMITTEE_INPUT", "safe_message": str(exc)})


@router.get("/health")
def health(service: Service) -> dict[str, object]:
    return service.health()


@router.get("/metadata")
def metadata(service: Service) -> dict[str, object]:
    return service.metadata()


@router.post("/assess", response_model=CommitteeAssessment)
def assess(payload: dict[str, Any], service: Service) -> CommitteeAssessment:
    try:
        request = _request(payload)
        selected = InvestmentCommitteeService(request.policy) if request.policy else service
        return selected.assess(request.opinions, request.proposal, request.risk_assessment, request.risk_decision, request.as_of)
    except ValueError as exc:
        raise _error(exc) from exc


@router.post("/review", response_model=CommitteeReviewResponse)
def review(payload: dict[str, Any], service: Service) -> CommitteeReviewResponse:
    try:
        request = _request(payload)
        selected = InvestmentCommitteeService(request.policy) if request.policy else service
        assessment, recommendation = selected.review(
            request.opinions, request.proposal, request.risk_assessment, request.risk_decision, request.as_of
        )
        return CommitteeReviewResponse(assessment=assessment, recommendation=recommendation)
    except ValueError as exc:
        raise _error(exc) from exc
