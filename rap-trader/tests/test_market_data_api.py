from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.main import app

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
    assert response.json()["detail"] == {"code": "UNSUPPORTED_SYMBOL", "safe_message": "Symbol NVDA is not supported"}


def test_market_data_bars_rejects_invalid_range_and_timeframe() -> None:
    query = params()
    query["end"] = query["start"]
    assert client.get("/market-data/bars", params=query).status_code == 400
    query = params()
    query["timeframe"] = "2m"
    assert client.get("/market-data/bars", params=query).status_code == 422
