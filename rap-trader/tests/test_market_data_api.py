from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.routes.market_data import get_market_data_provider
from app.domain.models import HistoricalBarsRequest, HistoricalBarsResult, MarketDataError, MarketDataErrorCode, ProviderHealth
from app.main import app
from app.services.market_data import MarketDataProvider

client = TestClient(app)


def params(symbol: str = "AAPL") -> dict[str, str]:
    return {
        "symbol": symbol,
        "timeframe": "1d",
        "start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "end": datetime(2026, 1, 3, tzinfo=UTC).isoformat(),
        "limit": "1",
    }


def test_market_data_health() -> None:
    payload = client.get("/market-data/health").json()
    assert payload["provider"] == "mock"
    assert payload["reachable"] is True


def test_market_data_timeframes() -> None:
    response = client.get("/market-data/timeframes")
    assert response.status_code == 200
    assert response.json()["timeframes"] == ["1m", "5m", "15m", "1h", "1d", "1w"]


def test_market_data_bars_returns_normalized_result() -> None:
    response = client.get("/market-data/bars", params=params(" aapl "))
    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "AAPL"
    assert payload["provider"] == "mock"
    assert len(payload["bars"]) == 1
    assert payload["bars"][0]["timestamp"].endswith("Z")


def test_market_data_bars_rejects_unsupported_symbol() -> None:
    response = client.get("/market-data/bars", params=params("NVDA"))
    assert response.status_code == 400
    assert response.json()["code"] == "HTTP_ERROR"
    assert response.json()["safe_message"] == "Symbol NVDA is not supported"


def test_market_data_bars_rejects_invalid_range_and_timeframe() -> None:
    query = params()
    query["end"] = query["start"]
    assert client.get("/market-data/bars", params=query).status_code == 400
    query = params()
    query["timeframe"] = "2m"
    assert client.get("/market-data/bars", params=query).status_code == 422


class FailingProvider(MarketDataProvider):
    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        raise MarketDataError(
            MarketDataErrorCode.PROVIDER_UNAVAILABLE,
            "Market data provider request failed",
            "failing",
            internal_detail="secret upstream exception",
        )

    def health(self) -> ProviderHealth:
        raise NotImplementedError

    def supported_timeframes(self) -> list[str]:
        return ["1d"]


def test_provider_exception_detail_is_not_exposed() -> None:
    app.dependency_overrides[get_market_data_provider] = FailingProvider
    try:
        response = client.get("/market-data/bars", params=params())
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 400
    assert response.json()["code"] == "HTTP_ERROR"
    assert response.json()["safe_message"] == "Market data provider request failed"
    assert "secret upstream exception" not in response.text
