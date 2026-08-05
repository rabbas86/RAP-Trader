from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domain.models.market_data import (
    AdjustmentPolicy,
    HistoricalBarsRequest,
    HistoricalBarsResult,
    MarketDataError,
    ProviderHealth,
    SessionPolicy,
    Symbol,
    Timeframe,
)
from app.services.market_data import MarketDataProvider, MockMarketDataProvider

router = APIRouter(prefix="/market-data", tags=["market-data"])


@lru_cache
def get_market_data_provider() -> MarketDataProvider:
    return MockMarketDataProvider()


ProviderDependency = Annotated[MarketDataProvider, Depends(get_market_data_provider)]


@router.get("/health", response_model=ProviderHealth)
def health(provider: ProviderDependency) -> ProviderHealth:
    return provider.health()


@router.get("/timeframes")
def timeframes(provider: ProviderDependency) -> dict[str, list[str]]:
    return {"timeframes": provider.supported_timeframes()}


@router.get("/bars", response_model=HistoricalBarsResult)
def bars(
    provider: ProviderDependency,
    symbol: Annotated[str, Query(min_length=1)],
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: Annotated[int | None, Query(gt=0, le=100_000)] = None,
    adjustment: AdjustmentPolicy = "raw",
    session: SessionPolicy = "regular",
) -> HistoricalBarsResult:
    try:
        request = HistoricalBarsRequest(
            symbol=Symbol(symbol), timeframe=timeframe, start=start, end=end, limit=limit, adjustment=adjustment, session=session
        )
        return provider.get_bars(request)
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code.value, "safe_message": exc.safe_message}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "safe_message": "Invalid market data request"},
        ) from exc
