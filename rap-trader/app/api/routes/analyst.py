"""Research-only analyst opinion endpoints."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.domain.models.analyst import AnalystError, AnalystMetadata, AnalystOpinion, AnalystRequest
from app.domain.models.market_data import Timeframe
from app.domain.models.technical import TechnicalAnalysisSnapshot
from app.services.analyst import AnalystService, OpinionAggregationService
from app.services.technical_analysis import TechnicalAnalyst

router = APIRouter(prefix="/analysts", tags=["analysts"])


@lru_cache
def get_analyst_service() -> AnalystService:
    return AnalystService()


Service = Annotated[AnalystService, Depends(get_analyst_service)]


def _error(exc: AnalystError) -> HTTPException:
    status = 404 if exc.code.value == "UNSUPPORTED_ANALYST" else 400
    return HTTPException(status_code=status, detail={"code": exc.code.value, "safe_message": exc.safe_message})


@router.get("", response_model=list[AnalystMetadata])
def analysts(service: Service) -> list[AnalystMetadata]:
    return service.list()


@router.get("/technical/snapshot", response_model=TechnicalAnalysisSnapshot)
def technical_snapshot(
    ticker: str, service: Service, timeframe: Timeframe = "1d", lookback: int = 60, as_of: datetime | None = None
) -> TechnicalAnalysisSnapshot:
    evaluation_time = as_of or datetime.now(UTC)
    try:
        request = AnalystRequest(
            analyst_id="technical",
            ticker=ticker,
            timeframe=timeframe,
            as_of=evaluation_time,
            lookback=lookback,
            horizon=1,
            asset_class="equity",
        )
        analyst = cast(TechnicalAnalyst, service.analyst("technical"))
        return analyst.snapshot(request)
    except (AnalystError, ValueError) as exc:
        if isinstance(exc, AnalystError):
            raise _error(exc) from exc
        raise HTTPException(status_code=422, detail={"code": "INVALID_REQUEST", "safe_message": str(exc)}) from exc


@router.get("/{analyst_id}/health")
def health(analyst_id: str, service: Service) -> Any:
    try:
        return service.analyst(analyst_id).health()
    except AnalystError as exc:
        raise _error(exc) from exc


@router.get("/{analyst_id}/metadata", response_model=AnalystMetadata)
def metadata(analyst_id: str, service: Service) -> AnalystMetadata:
    try:
        return service.analyst(analyst_id).metadata()
    except AnalystError as exc:
        raise _error(exc) from exc


@router.post("/{analyst_id}/analyze", response_model=AnalystOpinion)
def analyze(analyst_id: str, request: AnalystRequest, service: Service) -> AnalystOpinion:
    if request.analyst_id != analyst_id:
        raise HTTPException(status_code=400, detail={"code": "INVALID_REQUEST", "safe_message": "Path and request analyst IDs must match"})
    try:
        return service.analyze(request)
    except AnalystError as exc:
        raise _error(exc) from exc


@router.post("/opinions/aggregate")
def aggregate(opinions: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = [AnalystOpinion.model_validate_json(json.dumps(item)) for item in opinions]
    return OpinionAggregationService().aggregate(parsed)


@router.get("/opinions/{opinion_id}", response_model=AnalystOpinion)
def opinion(opinion_id: str, service: Service) -> AnalystOpinion:
    result = service.store.get(opinion_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "safe_message": "Opinion was not found"})
    return result
