from datetime import UTC, datetime, timedelta

import pytest

from app.domain.models import (
    HistoricalBarsRequest,
    HistoricalBarsResult,
    KronosPrediction,
    MarketDataError,
    MarketDataErrorCode,
    OHLCVBar,
    ProviderHealth,
)
from app.services.kronos import OfflineKronosService
from app.services.market_data import MarketDataProvider


class StaticProvider(MarketDataProvider):
    def __init__(self, closes: list[float] | None = None, fail: bool = False) -> None:
        super().__init__()
        self.closes = closes if closes is not None else [100.0] * 20
        self.fail = fail
        self.calls = 0

    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        self.calls += 1
        if self.fail:
            raise MarketDataError(MarketDataErrorCode.PROVIDER_UNAVAILABLE, "Unavailable", "static")
        start = request.end - timedelta(days=len(self.closes))
        bars = [
            OHLCVBar(timestamp=start + timedelta(days=index), open=close, high=close, low=close, close=close, volume=100)
            for index, close in enumerate(self.closes)
        ]
        return HistoricalBarsResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            bars=bars,
            provider="static",
            requested_start=request.start,
            requested_end=request.end,
            actual_start=bars[0].timestamp,
            actual_end=bars[-1].timestamp,
            adjustment=request.adjustment,
            session=request.session,
            retrieved_at=datetime(2026, 1, 2, tzinfo=UTC),
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider="static",
            configured=True,
            reachable=not self.fail,
            checked_at=datetime(2026, 1, 2, tzinfo=UTC),
            status="healthy",
            detail="test",
        )

    def supported_timeframes(self) -> list[str]:
        return ["1d"]


def predict(service: OfflineKronosService) -> KronosPrediction:
    return service.predict("aapl", timeframe="1d", start=datetime(2025, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC), limit=20)


@pytest.mark.parametrize(
    ("closes", "direction"), [([100.0] * 15 + [110.0] * 5, "UP"), ([110.0] * 15 + [100.0] * 5, "DOWN"), ([100.0] * 20, "FLAT")]
)
def test_sma_direction_and_metrics(closes: list[float], direction: str) -> None:
    result = predict(OfflineKronosService(provider=StaticProvider(closes)))
    short_sma = sum(closes[-5:]) / 5
    long_sma = sum(closes) / 20
    assert result.direction == direction
    assert result.expected_return == pytest.approx((short_sma - long_sma) / long_sma)
    assert result.confidence == pytest.approx(abs(short_sma - long_sma) / closes[-1])
    assert result.timeframe == result.time_horizon == "1d"
    assert result.source_provider == "static"
    assert result.model_version == "offline-kronos-v0"


def test_prediction_is_deterministic_and_cached() -> None:
    provider = StaticProvider([100.0] * 15 + [110.0] * 5)
    service = OfflineKronosService(provider=provider)
    assert predict(service) == predict(service)
    assert provider.calls == 1


def test_market_data_error_falls_back_to_flat_and_is_cached() -> None:
    provider = StaticProvider(fail=True)
    service = OfflineKronosService(provider=provider)
    first = predict(service)
    assert first == predict(service)
    assert first.direction == "FLAT"
    assert first.confidence == first.expected_return == 0
    assert first.source_provider == "static"
    assert provider.calls == 1


def test_insufficient_history_falls_back_to_flat() -> None:
    result = predict(OfflineKronosService(provider=StaticProvider([100.0] * 19)))
    assert result.direction == "FLAT"
    assert result.confidence == 0


def test_default_provider_is_offline_and_deterministic() -> None:
    assert OfflineKronosService().predict("AAPL") == OfflineKronosService().predict("AAPL")
    assert OfflineKronosService().predict("AAPL").source_provider == "mock"
    assert OfflineKronosService.LIVE_TRADING_SUITABLE is False
