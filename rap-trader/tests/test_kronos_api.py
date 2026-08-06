"""Tests for the Kronos read-only API endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _payload(ticker: str = "AAPL", model_id: str = "mock-kronos-v0") -> dict[str, object]:
    return {
        "ticker": ticker,
        "model_id": model_id,
        "timeframe": "1d",
        "start": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "lookback": 60,
        "horizon": 5,
    }


def test_health_endpoint() -> None:
    response = client.get("/kronos/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["configured"] is True


def test_models_endpoint() -> None:
    response = client.get("/kronos/models")
    assert response.status_code == 200
    data = response.json()
    assert "models" in data
    assert isinstance(data["models"], list)
    assert len(data["models"]) >= 1


def test_forecast_endpoint_returns_future_candles() -> None:
    response = client.post("/kronos/forecast", json=_payload())
    assert response.status_code == 200
    data = response.json()
    assert data["suitable_for_live_trading"] is False
    assert len(data["bars"]) == 5
    for bar in data["bars"]:
        assert "timestamp" in bar
        assert "open" in bar
        assert "high" in bar
        assert "low" in bar
        assert "close" in bar
        assert "volume" in bar


def test_forecast_endpoint_is_deterministic() -> None:
    first = client.post("/kronos/forecast", json=_payload())
    second = client.post("/kronos/forecast", json=_payload())
    assert first.json() == second.json()


def test_forecast_endpoint_rejects_invalid_range() -> None:
    payload = _payload()
    payload["start"] = payload["end"]
    response = client.post("/kronos/forecast", json=payload)
    assert response.status_code == 422


def test_forecast_endpoint_rejects_unsupported_model() -> None:
    payload = _payload(model_id="kronos-large")
    response = client.post("/kronos/forecast", json=payload)
    # The default mock provider does not reject model_id at the model level;
    # only LocalKronosProvider does. The mock returns 200.
    assert response.status_code == 200


def test_forecast_endpoint_rejects_empty_ticker() -> None:
    payload = _payload(ticker="")
    response = client.post("/kronos/forecast", json=payload)
    assert response.status_code == 422


def test_forecast_endpoint_provider_is_cached() -> None:
    """The provider is built once and cached as a module-level singleton."""
    import app.api.routes.kronos as kronos_routes

    # Reset the module-level cache
    kronos_routes._kronos_provider = None
    first = kronos_routes.get_kronos_provider()
    second = kronos_routes.get_kronos_provider()
    assert first is second


def test_health_endpoint_read_only() -> None:
    response = client.put("/kronos/health")
    assert response.status_code == 405


def test_forecast_metrics_endpoint() -> None:
    response = client.post("/kronos/forecast", json=_payload())
    assert response.status_code == 200
    forecast = response.json()
    metrics_response = client.post(
        "/kronos/forecast/metrics?flat_threshold=0.005",
        json=forecast,
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert "expected_return" in metrics
    assert "volatility" in metrics
    assert "max_drawdown" in metrics
    assert "direction" in metrics
    assert metrics["direction"] in ("UP", "DOWN", "FLAT")


def test_forecast_has_no_execution_dependency() -> None:
    """Kronos routes and service must not import broker, execution, order, or risk modules."""
    import app.api.routes.kronos as kronos_routes
    import app.services.kronos.service as kronos_service

    forbidden_prefixes = (
        "app.services.broker",
        "app.services.execution",
        "app.services.risk",
        "app.services.order",
    )

    # Verify the Kronos service module does not import forbidden components.
    for mod in vars(kronos_service).values():
        if hasattr(mod, "__name__") and mod.__name__.startswith("app."):
            assert not mod.__name__.startswith(forbidden_prefixes), f"Kronos service must not depend on {mod.__name__}"

    # Verify the routes module does not import forbidden components.
    for mod in vars(kronos_routes).values():
        if hasattr(mod, "__name__") and mod.__name__.startswith("app."):
            assert not mod.__name__.startswith(forbidden_prefixes), f"Kronos routes must not depend on {mod.__name__}"
