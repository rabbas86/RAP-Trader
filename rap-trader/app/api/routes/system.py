from fastapi import APIRouter, Depends

from app import __version__
from app.config import Settings, get_settings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
def status(settings: Settings = Depends(get_settings)) -> dict[str, str | bool]:
    return {"application_name": settings.app_name, "environment": settings.app_env, "trading_mode": settings.trading_mode, "live_trading_enabled": settings.live_trading_enabled, "version": __version__}
