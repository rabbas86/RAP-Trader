from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.routes.kronos import get_kronos_service
from app.main import app
from app.services.kronos import OfflineKronosService

client = TestClient(app)


def params(ticker: str = "AAPL") -> dict[str, str]:
    return {
        "ticker": ticker,
        "timeframe": "1d",
        "start": datetime(2025, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "limit": "20",
    }


def test_kronos_health_reports_offline_safety() -> None:
    response = client.get("/kronos/health")
    assert response.status_code == 200
    assert response.json()["model_version"] == "offline-kronos-v0"
    assert response.json()["live_trading_suitable"] is False
    assert response.json()["provider"]["provider"] == "mock"


def test_kronos_prediction_is_read_only_and_has_provenance() -> None:
    response = client.get("/kronos/prediction", params=params(" aapl "))
    assert response.status_code == 200
    payload = response.json()
    assert payload["ticker"] == "AAPL"
    assert payload["timeframe"] == payload["time_horizon"] == "1d"
    assert payload["source_provider"] == "mock"
    assert payload["data_start"] is not None
    assert payload["data_end"] is not None


def test_kronos_prediction_rejects_invalid_request_safely() -> None:
    query = params()
    query["end"] = query["start"]
    response = client.get("/kronos/prediction", params=query)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_REQUEST"


def test_kronos_prediction_has_no_mutating_method() -> None:
    assert client.post("/kronos/prediction", params=params()).status_code == 405


def test_dependency_is_cached_offline_service() -> None:
    get_kronos_service.cache_clear()
    try:
        assert get_kronos_service() is get_kronos_service()
        assert isinstance(get_kronos_service(), OfflineKronosService)
    finally:
        get_kronos_service.cache_clear()
