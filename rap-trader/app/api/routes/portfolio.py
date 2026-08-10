"""Research-only portfolio manager endpoints."""

import json
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.domain.models.portfolio import PortfolioProposal
from app.services.portfolio import PortfolioManagerService, PortfolioProposalRequest
from app.services.portfolio.validation import PortfolioValidationError

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@lru_cache
def get_portfolio_service() -> PortfolioManagerService:
    return PortfolioManagerService()


Service = Annotated[PortfolioManagerService, Depends(get_portfolio_service)]


def _safe_error(exc: ValueError) -> HTTPException:
    if isinstance(exc, PortfolioValidationError):
        return HTTPException(status_code=422, detail={"code": exc.code, "safe_message": exc.safe_message})
    return HTTPException(status_code=422, detail={"code": "INVALID_PORTFOLIO_INPUT", "safe_message": str(exc)})


@router.get("/health")
def health(service: Service) -> dict[str, object]:
    return service.health()


@router.get("/metadata")
def metadata(service: Service) -> dict[str, object]:
    return service.metadata()


def _request(payload: dict[str, Any]) -> PortfolioProposalRequest:
    return PortfolioProposalRequest.model_validate_json(json.dumps(payload))


@router.post("/propose", response_model=PortfolioProposal)
def propose(payload: dict[str, Any], service: Service) -> PortfolioProposal:
    try:
        return service.propose(_request(payload))
    except ValueError as exc:
        raise _safe_error(exc) from exc


@router.post("/validate")
def validate(payload: dict[str, Any], service: Service) -> dict[str, object]:
    try:
        return service.validate(_request(payload))
    except ValueError as exc:
        raise _safe_error(exc) from exc
