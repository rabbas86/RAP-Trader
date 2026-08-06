"""Phase 4: Backtesting domain models.

This module defines the domain contracts for reproducible, offline,
deterministic walk-forward backtesting of forecast quality.

All models are strictly validated and marked ``suitable_for_live_trading=False``
where applicable. No broker, execution, order, risk, or portfolio components
are imported or invoked from this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.market_data import Timeframe, UtcDatetime, _require_aware_utc

# ---------------------------------------------------------------------------
# Enums and literals
# ---------------------------------------------------------------------------

BACKTEST_SCHEMA_VERSION = "1.0"
"""Schema version for serialized backtest results."""


class BacktestStatus(StrEnum):
    """Lifecycle state of a backtest run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BenchmarkProvider(StrEnum):
    """Forecast providers eligible for backtesting comparison."""

    MOCK = "MockKronosProvider"
    SMA = "SMAForecastProvider"
    LAST_VALUE = "LastValueForecastProvider"
    DRIFT = "DriftForecastProvider"


class ResearchSignal(StrEnum):
    """Research-only signal values (no production decision path)."""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class MarketRegime(StrEnum):
    """Deterministic market-regime classification for an evaluation window."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGE_BOUND = "range_bound"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


class ForecastMetricName(StrEnum):
    """Names of every metric computed for a forecast evaluation."""

    MAE = "mae"
    RMSE = "rmse"
    MEDIAN_ABSOLUTE_ERROR = "median_absolute_error"
    SMAPE = "symmetric_mape"
    BIAS = "bias"
    MAX_ERROR = "max_error"
    CORRELATION = "correlation"
    DIRECTIONAL_ACCURACY = "directional_accuracy"
    SIGN_ACCURACY = "sign_accuracy"
    HIT_RATE = "hit_rate"
    INTERVAL_COVERAGE = "interval_coverage"
    INTERVAL_WIDTH = "interval_width"
    SAMPLE_COUNT = "sample_count"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BacktestErrorCodes(StrEnum):
    """Stable, safe error codes for backtesting operations."""

    INVALID_REQUEST = "INVALID_REQUEST"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    LOOKAHEAD_DETECTED = "LOOKAHEAD_DETECTED"
    DATA_LEAKAGE_DETECTED = "DATA_LEAKAGE_DETECTED"
    TARGET_IN_CONTEXT = "TARGET_IN_CONTEXT"
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    MISALIGNED_TIMESTAMPS = "MISALIGNED_TIMESTAMPS"
    FUTURE_INFORMATION = "FUTURE_INFORMATION"
    MAX_WINDOWS_EXCEEDED = "MAX_WINDOWS_EXCEEDED"
    DATA_GAP = "DATA_GAP"
    IRREGULAR_SPACING = "IRREGULAR_SPACING"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    STORE_FAILURE = "STORE_FAILURE"
    NOT_FOUND = "NOT_FOUND"


class BacktestError(Exception):
    """Stable public error for backtesting failures.

    Only ``code`` and ``safe_message`` are exposed to API consumers;
    ``internal_detail`` stays private.
    """

    def __init__(
        self,
        code: BacktestErrorCodes | str,
        safe_message: str,
        retryable: bool = False,
        internal_detail: str | None = None,
    ) -> None:
        self.code = BacktestErrorCodes(code)
        self.safe_message = safe_message
        self.retryable = retryable
        self.internal_detail = internal_detail
        super().__init__(safe_message)


# ---------------------------------------------------------------------------
# Walk-forward window
# ---------------------------------------------------------------------------


class EvaluationWindow(BaseModel):
    """A single walk-forward evaluation window.

    ``context_end`` is the timestamp of the last historical bar available
    to the forecast provider.  ``target_start`` / ``target_end`` define the
    future horizon whose actuals are compared against the forecast.

    The gap between ``context_end`` and ``target_start`` must equal exactly
    one timeframe step — this is enforced to prevent lookahead and data
    leakage.
    """

    model_config = ConfigDict(strict=True)

    window_index: int = Field(ge=0)
    context_start: UtcDatetime
    context_end: UtcDatetime
    target_start: UtcDatetime
    target_end: UtcDatetime
    timeframe: Timeframe

    @field_validator("context_start", "context_end", "target_start", "target_end", mode="before")
    @classmethod
    def normalize(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))

    @model_validator(mode="after")
    def validate_window(self) -> EvaluationWindow:
        if self.context_start >= self.context_end:
            raise ValueError("context_start must be before context_end")
        if self.target_start >= self.target_end:
            raise ValueError("target_start must be before target_end")
        if self.context_end >= self.target_start:
            raise ValueError("context_end must be before target_start (no overlap)")
        return self


# ---------------------------------------------------------------------------
# Forecast metrics
# ---------------------------------------------------------------------------


class ForecastMetrics(BaseModel):
    """Deterministic forecast-versus-actual metrics for a single window."""

    model_config = ConfigDict(strict=True)

    mae: float = Field(ge=0)
    rmse: float = Field(ge=0)
    median_absolute_error: float = Field(ge=0)
    symmetric_mape: float = Field(ge=0)
    bias: float
    max_error: float = Field(ge=0)
    correlation: float | None = Field(default=None, ge=-1, le=1)
    directional_accuracy: float = Field(ge=0, le=1)
    sign_accuracy: float = Field(ge=0, le=1)
    hit_rate: float = Field(ge=0, le=1)
    interval_coverage: float = Field(ge=0, le=1)
    interval_width: float = Field(ge=0)
    sample_count: int = Field(ge=0)

    def to_display_dict(self) -> dict[str, float | int]:
        """Human-friendly view of all metrics as a flat dict."""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Performance attribution (before / after costs)
# ---------------------------------------------------------------------------


class CostResult(BaseModel):
    """Transaction-cost and slippage result for a research signal path."""

    model_config = ConfigDict(strict=True)

    gross_pnl: float
    total_costs: float
    net_pnl: float
    turnover: float = Field(ge=0)
    commission_cost: float = Field(default=0.0, ge=0)
    slippage_cost: float = Field(default=0.0, ge=0)
    max_drawdown: float = Field(ge=0)
    short_selling_allowed: bool = False
    leverage: float = Field(ge=0)


class ResearchSignalRow(BaseModel):
    """A single research-signal observation."""

    model_config = ConfigDict(strict=True)

    timestamp: UtcDatetime
    signal: ResearchSignal
    position_size: float = Field(ge=0)
    price: float = Field(gt=0, allow_inf_nan=False)

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))


# ---------------------------------------------------------------------------
# Aggregated results
# ---------------------------------------------------------------------------


class ProviderBacktestResult(BaseModel):
    """Aggregated backtest result for a single forecast provider."""

    model_config = ConfigDict(strict=True)

    provider: str = Field(min_length=1)
    research_only: bool = True
    suitable_for_live_trading: bool = False
    mean_metrics: ForecastMetrics
    regime_breakdown: dict[str, dict[str, float | int]]
    cost_result: CostResult | None = None
    research_signal_summary: dict[str, float | int] | None = None
    warning: str | None = None


class BacktestRunRequest(BaseModel):
    """Request payload for ``POST /backtests/run``."""

    model_config = ConfigDict(strict=True)

    ticker: str = Field(min_length=1, max_length=10)
    timeframe: Timeframe
    start: UtcDatetime
    end: UtcDatetime
    lookback: int = Field(default=60, gt=0, le=10000)
    horizon: int = Field(default=5, gt=0, le=100)
    step: int = Field(default=5, gt=0, le=10000)
    max_windows: int | None = Field(default=None, gt=0, le=100000)
    seed: int = Field(default=42)
    include_local_kronos: bool = False
    research_simulation: bool = False
    short_selling: bool = False
    leverage: float = Field(default=1.0, ge=1.0, le=4.0)
    transaction_cost_bps: float = Field(default=0.0, ge=0)
    slippage_bps: float = Field(default=0.0, ge=0)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("start", "end", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))

    @model_validator(mode="after")
    def validate_range(self) -> BacktestRunRequest:
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class BacktestRunResult(BaseModel):
    """Full result of a completed backtest run."""

    model_config = ConfigDict(strict=True)

    backtest_id: str
    status: BacktestStatus
    request: BacktestRunRequest
    created_at: UtcDatetime
    completed_at: UtcDatetime | None = None
    schema_version: str = BACKTEST_SCHEMA_VERSION
    research_only: bool = True
    suitable_for_live_trading: bool = False
    providers: list[ProviderBacktestResult]
    regime_distribution: dict[str, int]
    windows_total: int = Field(ge=0)
    windows_evaluated: int = Field(ge=0)
    error: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, value: object) -> object:
        """Coerce JSON string values before strict enum validation."""
        if isinstance(value, str):
            return BacktestStatus(value)
        return value

    @field_validator("created_at", "completed_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: object) -> datetime | None:
        if value is None:
            return value
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))


class BacktestSummary(BaseModel):
    """Lightweight summary returned by ``GET /backtests/{id}/summary``."""

    model_config = ConfigDict(strict=True)

    backtest_id: str
    ticker: str
    timeframe: Timeframe
    status: BacktestStatus
    research_only: bool = True
    suitable_for_live_trading: bool = False
    providers: list[str]
    windows_total: int = Field(ge=0)
    windows_evaluated: int = Field(ge=0)
    mean_mae_by_provider: dict[str, float]
    mean_rmse_by_provider: dict[str, float]
    best_provider_by_rmse: str | None = None
    regime_distribution: dict[str, int]
    created_at: UtcDatetime

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, value: object) -> object:
        """Coerce JSON string values before strict enum validation."""
        if isinstance(value, str):
            return BacktestStatus(value)
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        return _require_aware_utc(cast(datetime, value))
