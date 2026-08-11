"""Offline, research-only Chairman governance endpoints."""

import json
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.domain.models.chairman import ChairmanAssessment, ChairmanDecision
from app.domain.models.committee import CommitteeAssessment, CommitteeRecommendation
from app.domain.models.market_data import UtcDatetime
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskDecision
from app.services.chairman import ChairmanConfig, ChairmanDecisionError, ChairmanService

router = APIRouter(prefix="/chairman", tags=["chairman"])


class ChairmanReviewRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    committee_assessment: CommitteeAssessment
    committee_recommendation: CommitteeRecommendation
    proposal: PortfolioProposal
    risk_assessment: RiskAssessment
    risk_decision: RiskDecision
    config: ChairmanConfig | None = None
    as_of: UtcDatetime | None = None


class ChairmanReviewResponse(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    assessment: ChairmanAssessment
    decision: ChairmanDecision


@lru_cache
def get_chairman_service() -> ChairmanService:
    return ChairmanService()


Service = Annotated[ChairmanService, Depends(get_chairman_service)]


def _request(payload: dict[str, Any]) -> ChairmanReviewRequest:
    return ChairmanReviewRequest.model_validate_json(json.dumps(payload))


def _error(exc: ValueError) -> HTTPException:
    if isinstance(exc, ChairmanDecisionError):
        return HTTPException(status_code=422, detail={"code": exc.code, "safe_message": exc.safe_message})
    return HTTPException(status_code=422, detail={"code": "INVALID_CHAIRMAN_INPUT", "safe_message": str(exc)})


@router.get("/health")
def health(service: Service) -> dict[str, object]:
    return service.health()


@router.get("/metadata")
def metadata(service: Service) -> dict[str, object]:
    return service.metadata()


@router.post("/assess", response_model=ChairmanAssessment)
def assess(payload: dict[str, Any], service: Service) -> ChairmanAssessment:
    try:
        request = _request(payload)
        selected = ChairmanService(request.config) if request.config else service
        return selected.assess(
            request.committee_assessment,
            request.committee_recommendation,
            request.proposal,
            request.risk_assessment,
            request.risk_decision,
            request.as_of,
        )
    except ValueError as exc:
        raise _error(exc) from exc


@router.post("/review", response_model=ChairmanReviewResponse)
def review(payload: dict[str, Any], service: Service) -> ChairmanReviewResponse:
    try:
        request = _request(payload)
        selected = ChairmanService(request.config) if request.config else service
        assessment, decision = selected.review(
            request.committee_assessment,
            request.committee_recommendation,
            request.proposal,
            request.risk_assessment,
            request.risk_decision,
            request.as_of,
        )
        return ChairmanReviewResponse(assessment=assessment, decision=decision)
    except ValueError as exc:
        raise _error(exc) from exc
