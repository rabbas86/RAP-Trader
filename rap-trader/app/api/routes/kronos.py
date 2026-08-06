"""Read-only Kronos forecast API endpoints.

All endpoints are computational — they do not mutate state, trigger trades,
or invoke any broker, order, risk, or execution service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import Settings, get_settings
from app.domain.models.kronos import (
    KronosError,
    KronosErrorCodes,
    KronosForecast,
    KronosForecastMetrics,
    KronosForecastRequest,
    KronosModelMetadata,
    KronosProviderHealth,
)
from app.services.kronos import (
    KronosForecastProvider,
    LocalKronosProvider,
    MockKronosProvider,
    SMAForecastProvider,
)

router = APIRouter(prefix="/kronos", tags=["kronos"])


def _build_provider(settings: Settings) -> KronosForecastProvider:
    """Build a KronosForecastProvider from settings (called once per app config)."""
    provider_name = settings.kronos_provider
    model_id = settings.kronos_model_id
    if provider_name == "mock":
        return MockKronosProvider()
    if provider_name == "sma":
        return SMAForecastProvider()
    if provider_name == "local":
        return LocalKronosProvider(
            model_id=model_id,
            model_path=settings.kronos_model_path,
            tokenizer_path=settings.kronos_tokenizer_path,
            device=settings.kronos_device,
            offline_only=settings.kronos_offline_only,
        )
    raise ValueError(f"Unknown Kronos provider: {provider_name}")


# Module-level provider instance, built lazily from the active settings.
_kronos_provider: KronosForecastProvider | None = None


def get_kronos_provider() -> KronosForecastProvider:
    """FastAPI dependency that resolves the active Kronos provider singleton."""
    global _kronos_provider
    if _kronos_provider is None:
        _kronos_provider = _build_provider(get_settings())
    assert _kronos_provider is not None
    return _kronos_provider


ProviderDependency = Annotated[KronosForecastProvider, Depends(get_kronos_provider)]


@router.get("/health", response_model=KronosProviderHealth)
def health(provider: ProviderDependency) -> KronosProviderHealth:
    return provider.health()


@router.get("/models")
def models(provider: ProviderDependency) -> dict[str, list[KronosModelMetadata]]:
    supported = provider.supported_models()
    return {"models": [provider.model_metadata(m) for m in supported]}


@router.post("/forecast", response_model=KronosForecast)
def forecast(
    provider: ProviderDependency,
    request: KronosForecastRequest,
) -> KronosForecast:
    try:
        return provider.forecast(request)
    except KronosError as exc:
        status = 503 if exc.code in (KronosErrorCodes.PROVIDER_UNAVAILABLE, KronosErrorCodes.MODEL_LOAD_FAILED) else 400
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code.value, "safe_message": exc.safe_message},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "safe_message": str(exc)},
        ) from exc


@router.post("/forecast/metrics", response_model=KronosForecastMetrics)
def forecast_metrics(
    forecast_result: KronosForecast,
    flat_threshold: Annotated[float, Query(ge=0, le=1)] = 0.005,
) -> KronosForecastMetrics:
    """Compute deterministic metrics from a KronosForecast JSON body."""
    from app.services.kronos import KronosForecastMetricsService

    service = KronosForecastMetricsService(model_version=forecast_result.model_id, flat_threshold=flat_threshold)
    return service.compute(forecast_result)
