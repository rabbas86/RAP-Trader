from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def test_health_returns_200() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "rap-trader", "trading_mode": "paper"}


def test_trading_mode_defaults_to_paper() -> None:
    assert Settings(_env_file=None).trading_mode == "paper"  # type: ignore[call-arg]
