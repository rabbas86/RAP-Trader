from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings

router = APIRouter(tags=["health"])
SettingsDependency = Annotated[Settings, Depends(get_settings)]


@router.get("/health")
def health(settings: SettingsDependency) -> dict[str, str]:
    return {"status": "healthy", "service": settings.app_name, "trading_mode": settings.trading_mode}
