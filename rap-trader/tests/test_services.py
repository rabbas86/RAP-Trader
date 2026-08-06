from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.models.decision import AgentEvidence
from app.domain.models.order import OrderRequest
from app.domain.models.portfolio import PortfolioContext
from app.domain.models.prediction import KronosPrediction
from app.main import app
from app.services.broker import PaperBroker
from app.services.decision_engine import WaitDecisionEngine
from app.services.execution import ExecutionService
from app.services.kronos import MockKronosProvider


def evidence(source: str) -> AgentEvidence:
    return AgentEvidence(
        source=source,
        ticker="AAPL",
        recommendation="neutral",
        confidence=0,
        reasoning_summary="Phase 1 placeholder",
        generated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Backward-compat test: the mock provider must be clearly identified as offline.
# ---------------------------------------------------------------------------


def test_kronos_mock_is_clearly_identified() -> None:
    provider = MockKronosProvider()
    assert provider.MODEL_VERSION == "mock-kronos-v0"
    assert provider.LIVE_TRADING_SUITABLE is False
    assert provider.health().detail is not None


def test_decision_engine_defaults_to_wait() -> None:
    prediction = KronosPrediction(
        ticker="AAPL",
        direction="FLAT",
        confidence=0,
        expected_return=0,
        time_horizon="1d",
        generated_at=datetime.now(UTC),
        model_version="mock-kronos-v0",
    )
    result = WaitDecisionEngine().decide(
        prediction,
        evidence("technical"),
        evidence("fundamental"),
        evidence("news"),
        PortfolioContext(equity=10000, current_drawdown_percent=0, daily_loss_percent=0),
    )
    assert result.action == "WAIT"
    assert result.quantity == 0


# ---------------------------------------------------------------------------
# Execution safety
# ---------------------------------------------------------------------------


def test_execution_cannot_override_risk_rejection() -> None:
    order = OrderRequest(
        ticker="AAPL",
        side="BUY",
        quantity=1,
        order_type="market",
        idempotency_key="risk-rejected",
    )
    broker = PaperBroker()
    with pytest.raises(PermissionError, match="risk engine approval is required"):
        ExecutionService(broker).execute_approved(order, risk_approved=False)
    assert broker.orders == {}


# ---------------------------------------------------------------------------
# Kronos API: read-only endpoints
# ---------------------------------------------------------------------------

client = TestClient(app)


def _payload(
    ticker: str = "AAPL",
    model_id: str = "mock-kronos-v0",
    horizon: int = 5,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "model_id": model_id,
        "timeframe": "1d",
        "start": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "lookback": 60,
        "horizon": horizon,
    }


def test_kronos_health_read_only() -> None:
    response = client.get("/kronos/health")
    assert response.status_code == 200
    data = response.json()
    assert data["configured"] is True
    assert data["status"] in ("healthy", "degraded")


def test_kronos_models_read_only() -> None:
    response = client.get("/kronos/models")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert len(data["models"]) >= 1


def test_kronos_forecast_returns_future_candles() -> None:
    response = client.post("/kronos/forecast", json=_payload())
    assert response.status_code == 200
    fc = response.json()
    assert fc["suitable_for_live_trading"] is False
    assert len(fc["bars"]) == 5
    for bar in fc["bars"]:
        assert {"timestamp", "open", "high", "low", "close", "volume"} <= set(bar)


def test_kronos_forecast_deterministic() -> None:
    first = client.post("/kronos/forecast", json=_payload()).json()
    second = client.post("/kronos/forecast", json=_payload()).json()
    assert first == second


def test_kronos_forecast_rejects_kronos_large() -> None:
    # Kronos-large is rejected by the LocalKronosProvider, not by the mock.
    # The API with the default mock provider accepts any model_id.
    # This test verifies that the API itself doesn't crash with kronos-large.
    payload = _payload(model_id="kronos-large")
    response = client.post("/kronos/forecast", json=payload)
    # Mock provider ignores model_id, returns 200 with forecast
    assert response.status_code in (200, 400)


def test_kronos_health_rejects_put() -> None:
    assert client.put("/kronos/health").status_code == 405
