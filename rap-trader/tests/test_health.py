import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app


def test_health_returns_200() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "rap-trader", "trading_mode": "paper"}


def test_trading_mode_defaults_to_paper() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.trading_mode == "paper"
    assert settings.live_trading_enabled is False


def test_live_mode_requires_explicit_live_trading_enablement() -> None:
    with pytest.raises(ValidationError, match="TRADING_MODE=live requires LIVE_TRADING_ENABLED=true"):
        Settings(_env_file=None, TRADING_MODE="live", LIVE_TRADING_ENABLED=False)  # type: ignore[call-arg]
