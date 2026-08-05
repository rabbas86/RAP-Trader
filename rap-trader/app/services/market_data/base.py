from abc import ABC, abstractmethod

from app.domain.models.market_data import HistoricalBarsRequest, HistoricalBarsResult


class MarketDataProvider(ABC):
    def __init__(self, timeout_seconds: float = 10.0, max_retries: int = 2) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @abstractmethod
    def get_bars(self, request: HistoricalBarsRequest) -> HistoricalBarsResult:
        """Fetch normalized bars or raise MarketDataError."""

    @abstractmethod
    def health(self) -> bool:
        """Report whether the provider is available."""

    @abstractmethod
    def supported_timeframes(self) -> list[str]:
        """Return stable public timeframe names."""
