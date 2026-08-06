from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domain.models import KronosPrediction, ProviderHealth, Timeframe
from app.services.kronos import KronosService, OfflineKronosService

router = APIRouter(prefix="/kronos", tags=["kronos"])


@lru_cache
def get_kronos_service() -> KronosService:
    return OfflineKronosService()


KronosDependency = Annotated[KronosService, Depends(get_kronos_service)]


@router.get("/health")
def health(service: KronosDependency) -> dict[str, object]:
    if not isinstance(service, OfflineKronosService):
        return {"status": "healthy", "model_version": "unknown", "live_trading_suitable": False}
    provider_health: ProviderHealth = service.provider.health()
    return {
        "status": "healthy" if provider_health.status == "healthy" else "degraded",
        "model_version": service.MODEL_VERSION,
        "live_trading_suitable": service.LIVE_TRADING_SUITABLE,
        "provider": provider_health.model_dump(mode="json"),
    }


@router.get("/prediction", response_model=KronosPrediction)
def prediction(
    service: KronosDependency,
    ticker: Annotated[str, Query(min_length=1)],
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: Annotated[int | None, Query(gt=0, le=100_000)] = None,
) -> KronosPrediction:
    try:
        return service.predict(ticker=ticker, timeframe=timeframe, start=start, end=end, limit=limit)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "safe_message": "Invalid Kronos prediction request"},
        ) from exc
