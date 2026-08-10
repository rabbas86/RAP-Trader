"""Offline-safe data-platform adapters."""

from .events import EventAdapter, EventsAdapter
from .fundamentals import FundamentalsAdapter
from .macro import MacroAdapter
from .market_data import MarketDataAdapter
from .mock import MockAdapter
from .news import NewsAdapter

__all__ = ["EventAdapter", "EventsAdapter", "FundamentalsAdapter", "MacroAdapter", "MarketDataAdapter", "MockAdapter", "NewsAdapter"]
