from typing import Annotated

from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings, get_settings

router = APIRouter(prefix="/system", tags=["system"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


def _readiness_checks(settings: Settings) -> dict[str, dict[str, str]]:
    config_safe = (
        (settings.kronos_offline_only or settings.allow_non_offline_kronos)
        and (settings.backtest_offline_only or settings.allow_non_offline_backtest)
        and (settings.trading_mode != "live" or settings.live_trading_enabled)
    )
    live_requested = settings.trading_mode == "live" or settings.live_trading_enabled
    environment_safe = not live_requested or (settings.app_env.casefold() in {"staging", "production"} and settings.allow_live_trading)
    return {
        "config_invariants": {
            "status": "pass" if config_safe else "fail",
            "safe_message": "Runtime safety invariants are satisfied" if config_safe else "Runtime safety invariants are not satisfied",
        },
        "environment": {
            "status": "pass" if environment_safe else "fail",
            "safe_message": "Environment safety checks passed" if environment_safe else "Environment safety checks failed",
        },
    }


@router.get("/status")
def status(settings: SettingsDependency) -> dict[str, str | bool]:
    return {
        "application_name": settings.app_name,
        "environment": settings.app_env,
        "trading_mode": settings.trading_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "version": __version__,
    }


@router.get("/readiness")
def readiness(settings: SettingsDependency) -> dict[str, str | dict[str, dict[str, str]]]:
    checks = _readiness_checks(settings)
    overall_status = "ready" if all(check["status"] == "pass" for check in checks.values()) else "degraded"
    return {"status": overall_status, "checks": checks}
