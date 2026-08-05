from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domain.models.market_data import HistoricalBarsRequest, HistoricalBarsResult, MarketDataError, Symbol, Timeframe
from app.services.market_data import MarketDataProvider, MockMarketDataProvider

router = APIRouter(prefix="/market-data", tags=["market-data"])


@lru_cache
def get_market_data_provider() -> MarketDataProvider:
    return MockMarketDataProvider()


ProviderDependency = Annotated[MarketDataProvider, Depends(get_market_data_provider)]


@router.get("/health")
def health(provider: ProviderDependency) -> dict[str, bool]:
    return {"healthy": provider.health()}


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
    limit: Annotated[int | None, Query(gt=0, le=10_000)] = None,
) -> HistoricalBarsResult:
    try:
        request = HistoricalBarsRequest(symbol=Symbol(symbol), timeframe=timeframe, start=start, end=end, limit=limit)
        return provider.get_bars(request)
    except (MarketDataError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
