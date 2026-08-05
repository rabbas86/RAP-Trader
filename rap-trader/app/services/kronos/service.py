from abc import ABC, abstractmethod
from datetime import UTC, datetime

from app.domain.models.prediction import KronosPrediction


class KronosService(ABC):
    @abstractmethod
    def predict(self, ticker: str) -> KronosPrediction:
        """Create a market prediction."""


class MockKronosService(KronosService):
    LIVE_TRADING_SUITABLE = False

    def predict(self, ticker: str) -> KronosPrediction:
        return KronosPrediction(ticker=ticker.upper(), direction="FLAT", confidence=0, expected_return=0, time_horizon="none (mock only; not suitable for live trading)", generated_at=datetime.now(UTC), model_version="mock-kronos-v0")
