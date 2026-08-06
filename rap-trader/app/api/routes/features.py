"""Read-only Market Intelligence Feature Platform endpoints."""

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.domain.models.features import FeatureError, FeatureSnapshot, FeatureSnapshotRequest, FeatureStoreHealth
from app.domain.models.market_data import MarketDataError
from app.services.features import FeatureService

router = APIRouter(prefix="/features", tags=["features"])


@lru_cache
def get_feature_service() -> FeatureService:
    return FeatureService()


Service = Annotated[FeatureService, Depends(get_feature_service)]


@router.get("/health", response_model=FeatureStoreHealth)
def health(service: Service) -> FeatureStoreHealth:
    return service.health()


@router.get("/categories")
def categories(service: Service) -> dict[str, tuple[str, ...]]:
    return service.categories()


@router.post("/snapshot", response_model=FeatureSnapshot)
def snapshot(request: FeatureSnapshotRequest, service: Service) -> FeatureSnapshot:
    try:
        return service.snapshot(request)
    except FeatureError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code.value, "safe_message": exc.safe_message}) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code.value, "safe_message": exc.safe_message}) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "INVALID_REQUEST", "safe_message": "Invalid feature snapshot request"}
        ) from exc
