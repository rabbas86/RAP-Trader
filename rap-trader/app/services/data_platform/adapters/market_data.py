"""Adapter from the Phase 2 MarketDataProvider contract."""

from __future__ import annotations

from app.domain.models.data_platform import DataDomain, DataSourceIdentity, NormalizedDataRecord
from app.domain.models.market_data import HistoricalBarsRequest
from app.services.data_platform.adapters._common import make_record
from app.services.market_data.base import MarketDataProvider


class MarketDataAdapter:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider

    def fetch(self, request: HistoricalBarsRequest) -> tuple[NormalizedDataRecord, ...]:
        result = self.provider.get_bars(request)
        source = DataSourceIdentity(
            provider=result.provider,
            dataset=f"market_bars_{result.timeframe}",
            source_version="1",
            schema_version="1",
            offline_capable=True,
            authoritative=False,
            metadata={"adjustment": result.adjustment, "session": result.session},
        )
        records = []
        for bar in result.bars:
            record_id = f"market.{result.symbol}.{result.timeframe}.{int(bar.timestamp.timestamp())}"
            records.append(
                make_record(
                    record_id=record_id,
                    domain=DataDomain.MARKET,
                    value={"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume},
                    units="ohlcv",
                    currency=result.currency.upper() if result.currency else None,
                    observed_at=bar.timestamp,
                    available_at=min(result.retrieved_at, request.end),
                    symbol_or_entity=str(result.symbol),
                    series_id=f"OHLCV_{result.timeframe}",
                    source=source,
                    metadata={"exchange": result.exchange, "adjustment": result.adjustment, "session": result.session},
                )
            )
        return tuple(records)

    get_records = fetch


__all__ = ["MarketDataAdapter"]
