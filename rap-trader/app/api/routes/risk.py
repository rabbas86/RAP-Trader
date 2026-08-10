"""Research-only portfolio Risk Officer endpoints."""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import RiskAssessment, RiskConstraintSet, RiskDecision
from app.services.risk import RiskError, RiskErrorCode, RiskOfficerService

router = APIRouter(prefix="/risk", tags=["risk"])


class RiskReviewRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    proposal_id: str | None = None
    proposal: PortfolioProposal
    historical_bars: list[HistoricalBarsResult] = Field(default_factory=list)
    liquidity_inputs: dict[str, dict[str, float]] = Field(default_factory=dict)
    constraints: RiskConstraintSet | None = None


class RiskReviewResponse(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)
    assessment: RiskAssessment
    decision: RiskDecision


@lru_cache
def get_risk_service() -> RiskOfficerService:
    return RiskOfficerService()


Service = Annotated[RiskOfficerService, Depends(get_risk_service)]


def _safe_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, RiskError):
        return HTTPException(status_code=422, detail={"code": exc.code, "safe_message": exc.safe_message})
    return HTTPException(status_code=422, detail={"code": "INVALID_RISK_INPUT", "safe_message": str(exc)})


def _request(payload: dict[str, Any]) -> RiskReviewRequest:
    request = RiskReviewRequest.model_validate_json(json.dumps(payload))
    if request.proposal_id is not None and request.proposal_id != request.proposal.proposal_id:
        raise RiskError(RiskErrorCode.INVALID_INPUT, "proposal_id does not match the supplied proposal")
    return request


@router.get("/health")
def health(service: Service) -> dict[str, object]:
    return service.health()


@router.get("/metadata")
def metadata(service: Service) -> dict[str, object]:
    return service.metadata()


@router.post("/assess", response_model=RiskAssessment)
def assess(payload: dict[str, Any], service: Service) -> RiskAssessment:
    try:
        request = _request(payload)
        return service.assess(request.proposal, request.historical_bars, request.liquidity_inputs, request.constraints)
    except ValueError as exc:
        raise _safe_error(exc) from exc


@router.post("/review", response_model=RiskReviewResponse)
def review(payload: dict[str, Any], service: Service) -> RiskReviewResponse:
    try:
        request = _request(payload)
        assessment, decision = service.review(request.proposal, request.historical_bars, request.liquidity_inputs, request.constraints)
        return RiskReviewResponse(assessment=assessment, decision=decision)
    except ValueError as exc:
        raise _safe_error(exc) from exc
