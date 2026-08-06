"""Read-only backtesting API endpoints.

Endpoints:

* ``POST /backtests/run`` — run a walk-forward backtest.
* ``GET /backtests/providers`` — list available benchmark providers.
* ``GET /backtests/{backtest_id}`` — retrieve a full backtest result.
* ``GET /backtests/{backtest_id}/summary`` — retrieve a lightweight summary.

All endpoints are computational and read-only — they do not submit orders,
trigger trades, or invoke any broker, execution, order, risk, or portfolio
service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.config import Settings, get_settings
from app.domain.models import BacktestErrorCodes
from app.domain.models.backtesting import (
    BacktestRunRequest,
    BacktestRunResult,
    BacktestSummary,
)
from app.services.backtesting.runner import BacktestRunner
from app.services.backtesting.store import (
    BacktestResultStore,
    JSONFileBacktestResultStore,
)
from app.services.market_data import MockMarketDataProvider

router = APIRouter(prefix="/backtests", tags=["backtests"])


def get_backtest_store(settings: Annotated[Settings, Depends(get_settings)]) -> BacktestResultStore:
    """Resolve the result store from settings."""
    return JSONFileBacktestResultStore(settings.backtest_result_dir)


def get_runner(settings: Annotated[Settings, Depends(get_settings)]) -> BacktestRunner:
    """Resolve the backtest runner from settings."""
    return BacktestRunner(market_data_provider=MockMarketDataProvider())


@router.post("/run", response_model=BacktestRunResult)
def run_backtest(
    request: BacktestRunRequest,
    runner: Annotated[BacktestRunner, Depends(get_runner)],
    store: Annotated[BacktestResultStore, Depends(get_backtest_store)],
) -> BacktestRunResult:
    """Run a walk-forward backtest.

    The default behavior is offline, deterministic, using mock market
    data and mock/baseline Kronos forecasts.  No network, model download,
    or broker connection occurs.
    """
    result = runner.run(request)
    if result.error is not None:
        raise HTTPException(
            status_code=400,
            detail={"code": "BACKTEST_FAILED", "safe_message": result.error},
        )
    store.save(result)
    return result


@router.get("/providers")
def list_providers() -> dict[str, list[str] | bool]:
    """List available forecast providers for backtesting."""
    return {
        "providers": [
            "MockKronosProvider",
            "SMAForecastProvider",
            "LastValueForecastProvider",
            "DriftForecastProvider",
        ],
        "benchmark_only": True,
        "local_kronos_available": True,
    }


@router.get("/{backtest_id}", response_model=BacktestRunResult)
def get_backtest(
    backtest_id: str,
    store: Annotated[BacktestResultStore, Depends(get_backtest_store)],
) -> BacktestRunResult:
    """Retrieve a full backtest result by ID."""
    result = store.load(backtest_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": BacktestErrorCodes.NOT_FOUND.value, "safe_message": f"Backtest {backtest_id} not found"},
        )
    return result


@router.get("/{backtest_id}/summary", response_model=BacktestSummary)
def get_backtest_summary(
    backtest_id: str,
    store: Annotated[BacktestResultStore, Depends(get_backtest_store)],
) -> BacktestSummary:
    """Retrieve a lightweight summary of a backtest result."""
    result = store.load(backtest_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail={"code": BacktestErrorCodes.NOT_FOUND.value, "safe_message": f"Backtest {backtest_id} not found"},
        )

    mean_mae: dict[str, float] = {}
    mean_rmse: dict[str, float] = {}
    best_provider: str | None = None
    best_rmse: float | None = None

    for p in result.providers:
        mean_mae[p.provider] = p.mean_metrics.mae
        mean_rmse[p.provider] = p.mean_metrics.rmse
        if best_rmse is None or p.mean_metrics.rmse < best_rmse:
            best_rmse = p.mean_metrics.rmse
            best_provider = p.provider

    return BacktestSummary(
        backtest_id=result.backtest_id,
        ticker=result.request.ticker,
        timeframe=result.request.timeframe,
        status=result.status,
        research_only=True,
        suitable_for_live_trading=False,
        providers=[p.provider for p in result.providers],
        windows_total=result.windows_total,
        windows_evaluated=result.windows_evaluated,
        mean_mae_by_provider=mean_mae,
        mean_rmse_by_provider=mean_rmse,
        best_provider_by_rmse=best_provider,
        regime_distribution=result.regime_distribution,
        created_at=result.created_at,
    )
