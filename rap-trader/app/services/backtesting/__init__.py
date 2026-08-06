"""Phase 4: Backtesting services package.

Contains walk-forward window generation, a no-lookahead evaluation engine,
forecast evaluation and metrics, market-regime classification, research
signal simulation, cost/slippage models, result persistence, and the
high-level backtest runner.

No module under this package imports or calls Broker, PaperBroker,
ExecutionService, OrderRequest, RiskEngine, PortfolioManager, Chairman,
or any Investment Committee agent.
"""

from app.services.backtesting.costs import (
    CostConfig,
    FixedBpsCostModel,
    FixedBpsSlippageModel,
    SlippageModel,
    TransactionCostModel,
    ZeroCostModel,
    ZeroSlippageModel,
)
from app.services.backtesting.engine import BacktestEngine, EvaluationWindowGenerator
from app.services.backtesting.evaluator import ForecastEvaluator
from app.services.backtesting.providers import (
    BenchmarkForecastProvider,
    DriftForecastProvider,
    LastValueForecastProvider,
    MockBenchmarkProvider,
)
from app.services.backtesting.regime import MarketRegimeClassifier, RegimeThresholds
from app.services.backtesting.research import ResearchSignalSimulator, SignalSimulationConfig
from app.services.backtesting.runner import BacktestRunner
from app.services.backtesting.store import (
    BacktestResultStore,
    InMemoryBacktestResultStore,
    JSONFileBacktestResultStore,
)
from app.services.kronos import SMAForecastProvider

__all__ = [
    "BacktestEngine",
    "BacktestResultStore",
    "BacktestRunner",
    "BenchmarkForecastProvider",
    "CostConfig",
    "DriftForecastProvider",
    "EvaluationWindowGenerator",
    "FixedBpsCostModel",
    "FixedBpsSlippageModel",
    "ForecastEvaluator",
    "InMemoryBacktestResultStore",
    "JSONFileBacktestResultStore",
    "LastValueForecastProvider",
    "MarketRegimeClassifier",
    "MockBenchmarkProvider",
    "RegimeThresholds",
    "ResearchSignalSimulator",
    "SMAForecastProvider",
    "SignalSimulationConfig",
    "SlippageModel",
    "TransactionCostModel",
    "ZeroCostModel",
    "ZeroSlippageModel",
]
