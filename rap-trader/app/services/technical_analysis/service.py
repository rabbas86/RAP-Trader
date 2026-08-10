"""Deterministic, causal, research-only technical analyst.

Phase 6.5 hardening: the normal analysis path now consumes a
``FeatureSnapshot`` produced by the Market Intelligence Feature Platform
(FeatureService) rather than re-invoking feature generators directly.
Indicator formulas are never duplicated — they live exclusively in the
feature generators, and the analyst reads their deterministic outputs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, ClassVar, Literal
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisTrace,
    AnalysisWarning,
    AnalystError,
    AnalystErrorCodes,
    AnalystHealth,
    AnalystMetadata,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    Assumption,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    ProvenanceRecord,
    TraceEdge,
    TraceNode,
    validate_trace,
)
from app.domain.models.features import FeatureError, FeatureSnapshot, FeatureSnapshotRequest
from app.domain.models.market_data import HistoricalBarsRequest, MarketDataError, OHLCVBar, Symbol, Timeframe, _require_aware_utc
from app.domain.models.technical import (
    MarketStructureState,
    SwingPoint,
    TechnicalAnalysisSnapshot,
    TechnicalIndicatorValue,
    TechnicalLevel,
)
from app.services.analyst.service import Analyst, ConfidenceAssessmentService, DataFreshnessService, EvidenceValidationService
from app.services.features import FeatureService
from app.services.features.generators.momentum import MomentumFeatureGenerator
from app.services.features.generators.trend import TrendFeatureGenerator
from app.services.features.generators.volatility import VolatilityFeatureGenerator
from app.services.features.generators.volume import VolumeFeatureGenerator
from app.services.market_data import MarketDataProvider, MockMarketDataProvider, cache_key_builder
from app.services.technical_analysis.synthesis import TechnicalEvidenceSynthesizer


@dataclass(frozen=True)
class TechnicalAnalystConfig:
    analyst_id: str = "technical"
    role: AnalystRole = AnalystRole.TECHNICAL
    sma_periods: list[int] = field(default_factory=lambda: [10, 20, 50])
    ema_periods: list[int] = field(default_factory=lambda: [12, 26])
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    roc_period: int = 12
    atr_period: int = 14
    bollinger_period: int = 20
    volume_period: int = 20
    level_tolerance: float = 0.01
    level_limit: int = 6
    uncalibrated_confidence_cap: float = 0.65
    stale_input_allowed: bool = False
    base_confidence: float = 0.6

    def __post_init__(self) -> None:
        periods = [
            *self.sma_periods,
            *self.ema_periods,
            self.rsi_period,
            self.macd_fast,
            self.macd_slow,
            self.macd_signal,
            self.roc_period,
            self.atr_period,
            self.bollinger_period,
            self.volume_period,
        ]
        if not self.analyst_id or not self.sma_periods or not self.ema_periods or any(period <= 0 for period in periods):
            raise ValueError("analyst_id and indicator periods must be positive")
        if self.macd_fast >= self.macd_slow or self.level_tolerance <= 0 or self.level_limit <= 0:
            raise ValueError("invalid technical analyst configuration")
        if not 0 <= self.uncalibrated_confidence_cap <= 1 or not 0 <= self.base_confidence <= 1:
            raise ValueError("confidence values must be between zero and one")


class TechnicalAnalyst(Analyst):
    _STEPS: ClassVar[dict[str, timedelta]] = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
    }
    research_only: ClassVar[bool] = True
    suitable_for_live_trading: ClassVar[bool] = False

    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        config: TechnicalAnalystConfig | None = None,
        feature_service: FeatureService | None = None,
    ) -> None:
        self.provider = provider or MockMarketDataProvider()
        self.config = config or TechnicalAnalystConfig()
        self.feature_service = feature_service or FeatureService(provider=self.provider)
        self.freshness, self.confidence = DataFreshnessService(), ConfidenceAssessmentService(self.config.uncalibrated_confidence_cap)
        self.validator, self.synthesizer = EvidenceValidationService(self.freshness), TechnicalEvidenceSynthesizer()
        self._opinions: dict[str, AnalystOpinion] = {}
        self._traces: dict[str, AnalysisTrace] = {}
        self._snapshots: dict[str, TechnicalAnalysisSnapshot] = {}
        self._lock = RLock()

    def supported_timeframes(self) -> list[str]:
        return list(self._STEPS)

    def supported_asset_classes(self) -> list[str]:
        return ["equity"]

    def validate_input(self, request: AnalystRequest) -> None:
        if request.analyst_id != self.config.analyst_id:
            raise AnalystError(AnalystErrorCodes.UNSUPPORTED_ANALYST, "Analyst is not available")
        if request.timeframe not in self._STEPS or request.asset_class != "equity":
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Unsupported timeframe or asset class")

    def health(self) -> AnalystHealth:
        health = self.provider.health()
        return AnalystHealth(
            analyst_id=self.config.analyst_id,
            configured=health.configured,
            reachable=health.reachable,
            checked_at=datetime.now(UTC),
            status=health.status,
            detail=f"technical analyst using {health.provider}",
        )

    def metadata(self) -> AnalystMetadata:
        return AnalystMetadata(
            analyst_id=self.config.analyst_id,
            display_name="Technical Analyst",
            role=self.config.role,
            supported_timeframes=self.supported_timeframes(),
            supported_asset_classes=["equity"],
            description="Deterministic indicators, volume, structure, support, resistance, and evidence synthesis",
        )

    # ------------------------------------------------------------------
    # Thin delegation to canonical feature generators (no formula duplication)
    # ------------------------------------------------------------------

    @staticmethod
    def sma(values: list[float], period: int) -> float:
        return TrendFeatureGenerator.sma(values, period)

    @staticmethod
    def ema_series(values: list[float], period: int) -> list[float]:
        return TrendFeatureGenerator.ema_series(values, period)

    @classmethod
    def ema(cls, values: list[float], period: int) -> float:
        return TrendFeatureGenerator.ema(values, period)

    @staticmethod
    def moving_average_slope(values: list[float], period: int, *, exponential: bool = False) -> float:
        return TrendFeatureGenerator.moving_average_slope(values, period, exponential=exponential)

    @classmethod
    def crossover(cls, values: list[float], fast: int, slow: int) -> tuple[str, int]:
        return TrendFeatureGenerator.crossover(values, fast, slow)

    @staticmethod
    def roc(values: list[float], period: int) -> float:
        return MomentumFeatureGenerator.roc(values, period)

    @staticmethod
    def true_ranges(bars: list[OHLCVBar]) -> list[float]:
        return VolatilityFeatureGenerator.true_ranges(bars)

    @classmethod
    def atr(cls, bars: list[OHLCVBar], period: int) -> float:
        return VolatilityFeatureGenerator.atr(bars, period)

    @classmethod
    def bollinger_bands(cls, values: list[float], period: int = 20, deviations: float = 2) -> tuple[float, float, float]:
        return VolatilityFeatureGenerator.bollinger_bands(values, period, deviations)

    @staticmethod
    def bollinger_bandwidth(lower: float, middle: float, upper: float) -> float:
        return VolatilityFeatureGenerator.bollinger_bandwidth(lower, middle, upper)

    @staticmethod
    def obv(bars: list[OHLCVBar]) -> float:
        return VolumeFeatureGenerator.obv(bars)

    @staticmethod
    def rolling_volume_average(bars: list[OHLCVBar], period: int) -> float:
        return VolumeFeatureGenerator.rolling_volume_average(bars, period)

    @classmethod
    def relative_volume(cls, bars: list[OHLCVBar], period: int) -> float:
        return VolumeFeatureGenerator.relative_volume(bars, period)

    @staticmethod
    def vwap(bars: list[OHLCVBar]) -> float:
        return VolumeFeatureGenerator.vwap(bars)

    @staticmethod
    def rsi(values: list[float], period: int) -> float:
        return MomentumFeatureGenerator.rsi(values, period)

    @classmethod
    def macd(cls, values: list[float], fast: int, slow: int, signal: int) -> tuple[float, float, float]:
        return MomentumFeatureGenerator.macd(values, fast, slow, signal)

    def _minimum_bars(self) -> int:
        return max(
            max(self.config.sma_periods) + 1,
            max(self.config.ema_periods) + 1,
            self.config.rsi_period + 1,
            self.config.macd_slow,
            self.config.roc_period + 1,
            self.config.atr_period,
            self.config.bollinger_period,
            self.config.volume_period,
        )

    def _bars_request(self, request: AnalystRequest) -> HistoricalBarsRequest:
        return HistoricalBarsRequest(
            symbol=Symbol(request.ticker),
            timeframe=request.timeframe,
            start=request.as_of - self._STEPS[request.timeframe] * max(request.lookback, self._minimum_bars()),
            end=request.as_of,
            limit=request.lookback,
            adjustment="raw",
            session="regular",
        )

    @staticmethod
    def _feature_snapshot_request(request: AnalystRequest) -> FeatureSnapshotRequest:
        """Build a FeatureSnapshotRequest from an AnalystRequest.

        Maps the analyst's lookback and as_of to the feature platform's
        request, requesting the feature IDs relevant to the technical analyst.
        """
        return FeatureSnapshotRequest(
            ticker=request.ticker,
            timeframe=request.timeframe,
            as_of=request.as_of,
            lookback=request.lookback,
            adjustment="raw",
            session="regular",
        )

    def _extras_from_context(self, request: AnalystRequest) -> dict[str, Any] | None:
        """Extract Kronos / backtest extras from the analyst request's extra_context.

        Only proper model objects are forwarded to the FeatureService so that
        the generator layer receives typed inputs.  Dict-style values (e.g.
        ``{"direction": "UP"}``) are handled by the analyst's evidence layer
        directly rather than being passed through to feature generation.
        """
        extras: dict[str, Any] = {}
        kronos = request.extra_context.get("kronos_forecast")
        if kronos is not None and hasattr(kronos, "model_dump"):
            extras["kronos_forecast"] = kronos
        backtest = request.extra_context.get("backtest_result")
        if backtest is not None and (hasattr(backtest, "model_dump") or isinstance(backtest, dict)):
            extras["backtest_metrics"] = backtest
        return extras or None

    # ------------------------------------------------------------------
    # FeatureSnapshot -> TechnicalAnalysisSnapshot conversion (no recalculation)
    # ------------------------------------------------------------------

    def _snapshot_from_features(self, feature_snapshot: FeatureSnapshot) -> TechnicalAnalysisSnapshot:
        """Build a ``TechnicalAnalysisSnapshot`` from a ``FeatureSnapshot``.

        No indicator formulas are recalculated — every value is read directly
        from the MIFP FeatureSnapshot produced by FeatureService.
        """
        vector = feature_snapshot.vector
        values = {str(item.feature_id): item for item in vector.values}
        timestamp = feature_snapshot.as_of
        indicators: list[TechnicalIndicatorValue] = []

        trend_features = {
            "trend.sma_10": 10,
            "trend.sma_20": 20,
            "trend.sma_50": 50,
            "trend.ema_12": 12,
            "trend.ema_26": 26,
        }
        for feature_id, period in trend_features.items():
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, float):
                    direction: Literal["up", "down", "flat"] = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(name=feature_id, value=value, period=period, direction=direction, timestamp=timestamp)
                    )

        slope_features = {
            "trend.sma_slope": self.config.sma_periods[0],
            "trend.ema_slope": self.config.ema_periods[0],
        }
        for feature_id, period in slope_features.items():
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, float):
                    direction = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(name=feature_id, value=value, period=period, direction=direction, timestamp=timestamp)
                    )

        crossover_features = {"trend.crossover_age": None, "trend.crossover_state": None}
        for feature_id in crossover_features:
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, (int, float)):
                    direction = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(name=feature_id, value=value, period=None, direction=direction, timestamp=timestamp)
                    )

        simple_features: dict[str, int | None] = {
            "momentum.roc_12": self.config.roc_period,
            "momentum.rsi_14": self.config.rsi_period,
            "volatility.atr_14": self.config.atr_period,
            "volatility.bollinger_bandwidth": self.config.bollinger_period,
            "volume.obv": None,
            "volume.average_20": self.config.volume_period,
            "volume.relative_20": self.config.volume_period,
            "volume.vwap": None,
        }
        for feature_id, simple_period in simple_features.items():
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, (int, float)) and simple_period is not None:
                    direction = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(
                            name=feature_id, value=value, period=simple_period, direction=direction, timestamp=timestamp
                        )
                    )

        bollinger_features = {
            "volatility.bollinger_lower": self.config.bollinger_period,
            "volatility.bollinger_middle": self.config.bollinger_period,
            "volatility.bollinger_upper": self.config.bollinger_period,
        }
        for feature_id, bollinger_period in bollinger_features.items():
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, (int, float)) and bollinger_period is not None:
                    direction = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(
                            name=feature_id, value=value, period=bollinger_period, direction=direction, timestamp=timestamp
                        )
                    )

        momentum_macd = {
            "momentum.macd": None,
            "momentum.macd_signal": None,
            "momentum.macd_histogram": None,
        }
        for feature_id in momentum_macd:
            if feature_id in values:
                value = values[feature_id].value
                if isinstance(value, (int, float)):
                    direction = "up" if value > 0 else "down" if value < 0 else "flat"
                    indicators.append(
                        TechnicalIndicatorValue(name=feature_id, value=value, period=None, direction=direction, timestamp=timestamp)
                    )

        volatility_tr = "volatility.true_range"
        if volatility_tr in values:
            value = values[volatility_tr].value
            if isinstance(value, (int, float)):
                direction = "up" if value > 0 else "down" if value < 0 else "flat"
                indicators.append(
                    TechnicalIndicatorValue(
                        name=volatility_tr, value=value, period=self.config.atr_period, direction=direction, timestamp=timestamp
                    )
                )

        structure_state = MarketStructureState(
            regime=_as_regime(values.get("structure.regime")),
            higher_highs=_as_int(values.get("structure.higher_highs")),
            higher_lows=_as_int(values.get("structure.higher_lows")),
            lower_highs=_as_int(values.get("structure.lower_highs")),
            lower_lows=_as_int(values.get("structure.lower_lows")),
            bos_timestamp=_parse_optional_timestamp(values.get("structure.bos_timestamp")),
            choch_timestamp=_parse_optional_timestamp(values.get("structure.choch_timestamp")),
            last_confirmed_timestamp=timestamp,
        )

        levels: list[TechnicalLevel] = []
        level_count = _as_int(values.get("support_resistance.level_count"))
        touch_count = _as_int(values.get("support_resistance.touch_count"))
        broken_count = _as_int(values.get("support_resistance.broken_count"))
        nearest_support_raw = values.get("support_resistance.nearest_support")
        nearest_resistance_raw = values.get("support_resistance.nearest_resistance")
        if nearest_support_raw is not None and isinstance(nearest_support_raw.value, (int, float)):
            levels.append(
                TechnicalLevel(
                    price=nearest_support_raw.value,
                    level_type="support",
                    strength=EvidenceStrength.STRONG if level_count > 0 else EvidenceStrength.WEAK,
                    confirmed_at=timestamp,
                    touch_count=max(touch_count, 1),
                    broken=bool(broken_count > 0),
                )
            )
        if nearest_resistance_raw is not None and isinstance(nearest_resistance_raw.value, (int, float)):
            levels.append(
                TechnicalLevel(
                    price=nearest_resistance_raw.value,
                    level_type="resistance",
                    strength=EvidenceStrength.STRONG if level_count > 0 else EvidenceStrength.WEAK,
                    confirmed_at=timestamp,
                    touch_count=max(touch_count, 1),
                    broken=bool(broken_count > 0),
                )
            )

        return TechnicalAnalysisSnapshot(
            bars_analyzed=feature_snapshot.bars_analyzed,
            timeframe=feature_snapshot.timeframe,
            indicator_values=indicators,
            swing_points=[],
            structure=structure_state,
            levels=levels,
            generated_at=feature_snapshot.as_of,
        )

    # ------------------------------------------------------------------
    # Backward-compatible snapshot_from_bars (used by API route)
    # ------------------------------------------------------------------

    def snapshot_from_bars(self, bars: list[OHLCVBar], timeframe: Timeframe, generated_at: datetime) -> TechnicalAnalysisSnapshot:
        _require_aware_utc(generated_at)
        causal = [bar for bar in bars if bar.timestamp <= generated_at]
        if len(causal) < self._minimum_bars():
            raise AnalystError(AnalystErrorCodes.INSUFFICIENT_DATA, "Not enough historical bars")
        indicators, _ = self._indicator_values(causal)
        swings = self._confirmed_swings(causal)
        structure = self._classify_structure(causal, swings)
        levels = self._clustered_levels(swings, causal[-1].close)
        return TechnicalAnalysisSnapshot(
            bars_analyzed=len(causal),
            timeframe=timeframe,
            indicator_values=indicators,
            swing_points=swings,
            structure=structure,
            levels=levels,
            generated_at=generated_at,
        )

    @staticmethod
    def _confirmed_swings(bars: list[OHLCVBar]) -> list[SwingPoint]:
        from app.services.features.generators.structure import confirmed_swings

        return confirmed_swings(bars)

    @staticmethod
    def _classify_structure(bars: list[OHLCVBar], swings: list[SwingPoint]) -> MarketStructureState:
        from app.services.features.generators.structure import classify_structure

        return classify_structure(bars, swings)

    @staticmethod
    def _clustered_levels(swings: list[SwingPoint], current_price: float) -> list[TechnicalLevel]:
        from app.services.features.generators.support_resistance import clustered_levels

        return clustered_levels(
            swings,
            current_price,
            tolerance=0.01,  # default
            limit=6,  # default
        )

    def snapshot(self, request: AnalystRequest) -> TechnicalAnalysisSnapshot:
        self.validate_input(request)
        bars_request = self._bars_request(request)
        result = self.provider.get_bars(bars_request)
        return self.snapshot_from_bars(result.bars, request.timeframe, request.as_of)

    # ------------------------------------------------------------------
    # Indicator value computation (kept for backward compatibility)
    # ------------------------------------------------------------------

    def _indicator_values(self, bars: list[OHLCVBar]) -> tuple[list[TechnicalIndicatorValue], dict[str, float | str | int]]:
        closes = [bar.close for bar in bars]
        timestamp = bars[-1].timestamp
        raw: dict[str, float | str | int] = {}

        def add(name: str, value: float, period: int | None = None) -> None:
            raw[name] = value
            direction: Literal["up", "down", "flat"] = "up" if value > 0 else "down" if value < 0 else "flat"
            indicators.append(TechnicalIndicatorValue(name=name, value=value, period=period, direction=direction, timestamp=timestamp))

        indicators: list[TechnicalIndicatorValue] = []
        for period in self.config.sma_periods:
            add(f"sma_{period}", self.sma(closes, period), period)
        for period in self.config.ema_periods:
            add(f"ema_{period}", self.ema(closes, period), period)
        add("sma_slope", self.moving_average_slope(closes, self.config.sma_periods[0]), self.config.sma_periods[0])
        add("ema_slope", self.moving_average_slope(closes, self.config.ema_periods[0], exponential=True), self.config.ema_periods[0])
        state, age = self.crossover(closes, self.config.sma_periods[0], self.config.sma_periods[-1])
        raw.update(crossover_state=state, crossover_age=age)
        add("crossover_age", float(age))
        add("roc", self.roc(closes, self.config.roc_period), self.config.roc_period)
        add("rsi", self.rsi(closes, self.config.rsi_period), self.config.rsi_period)
        macd, signal, histogram = self.macd(closes, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
        add("macd", macd)
        add("macd_signal", signal)
        add("macd_histogram", histogram)
        ranges = self.true_ranges(bars)
        add("true_range", ranges[-1])
        add("atr", self.atr(bars, self.config.atr_period), self.config.atr_period)
        lower, middle, upper = self.bollinger_bands(closes, self.config.bollinger_period)
        add("bollinger_lower", lower)
        add("bollinger_middle", middle)
        add("bollinger_upper", upper)
        add("bollinger_bandwidth", self.bollinger_bandwidth(lower, middle, upper))
        add("obv", self.obv(bars))
        add("volume_average", self.rolling_volume_average(bars, self.config.volume_period), self.config.volume_period)
        add("relative_volume", self.relative_volume(bars, self.config.volume_period))
        add("vwap", self.vwap(bars))
        return indicators, raw

    # ------------------------------------------------------------------
    # Evidence from FeatureSnapshot (normal analysis path)
    # ------------------------------------------------------------------

    @staticmethod
    def _value(values: dict[str, Any], key: str) -> float | str | int | None:
        item = values.get(key)
        if item is None:
            return None
        return item.value if hasattr(item, "value") else None

    def _evidence_from_features(
        self,
        request: AnalystRequest,
        observed: datetime,
        provider: str,
        snapshot: TechnicalAnalysisSnapshot,
        feature_snapshot: FeatureSnapshot,
        material: str,
    ) -> list[EvidenceItem]:
        """Convert FeatureSnapshot values into Phase 5 EvidenceItem objects."""
        vector = feature_snapshot.vector
        values = {str(item.feature_id): item for item in vector.values}
        raw: dict[str, Any] = {}

        for item in vector.values:
            raw[str(item.feature_id)] = item.value

        def val(key: str) -> float:
            item = values.get(key)
            if item is None or not isinstance(item.value, (int, float)):
                return 0.0
            return float(item.value)

        sma_slope = val("trend.sma_slope")
        ema_slope = val("trend.ema_slope")
        crossover_state_raw = values.get("trend.crossover_state")
        cross = str(crossover_state_raw.value) if crossover_state_raw else "unknown"
        roc = val("momentum.roc_12")
        rsi = val("momentum.rsi_14")
        macd_histogram = val("momentum.macd_histogram")
        obv = val("volume.obv")
        relative_volume = val("volume.relative_20")
        vwap = val("volume.vwap")
        volume_average = val("volume.average_20")
        atr = val("volatility.atr_14")
        bollinger_bandwidth = val("volatility.bollinger_bandwidth")
        regime_item = values.get("structure.regime")
        regime = str(regime_item.value) if regime_item and isinstance(regime_item.value, str) else "range_bound"
        swing_count = int(val("structure.swing_count")) if values.get("structure.swing_count") else 0
        bos_item = values.get("structure.bos_timestamp")
        choch_item = values.get("structure.choch_timestamp")
        bos_str = _feature_timestamp_str(bos_item)
        choch_str = _feature_timestamp_str(choch_item)
        level_count = int(val("support_resistance.level_count")) if values.get("support_resistance.level_count") else 0
        touch_count = int(val("support_resistance.touch_count")) if values.get("support_resistance.touch_count") else 0
        broken_count = int(val("support_resistance.broken_count")) if values.get("support_resistance.broken_count") else 0
        has_support = values.get("support_resistance.nearest_support") is not None
        has_resistance = values.get("support_resistance.nearest_resistance") is not None

        directions = {
            "trend": "bullish" if sma_slope > 0 and cross == "above" else "bearish" if sma_slope < 0 and cross == "below" else "mixed",
            "momentum": "bullish"
            if roc > 0 and macd_histogram >= 0 and rsi < 70
            else "bearish"
            if roc < 0 and macd_histogram <= 0 and rsi > 30
            else "mixed",
            "volatility": "neutral",
            "volume": "bullish" if obv > 0 else "bearish" if obv < 0 else "neutral",
            "structure": "bullish" if regime == "uptrend" else "bearish" if regime == "downtrend" else "neutral",
            "levels": "support holding" if has_support else "resistance holding" if has_resistance else "neutral",
        }
        details = {
            "trend": f"sma_slope={sma_slope:.6f}, ema_slope={ema_slope:.6f}, crossover={cross}, bos={bos_str}, choch={choch_str}",
            "momentum": f"roc={roc:.4f}, rsi={rsi:.4f}, macd_histogram={macd_histogram:.6f}",
            "volatility": f"atr={atr:.6f}, bollinger_bandwidth={bollinger_bandwidth:.6f}",
            "volume": f"obv={obv:.0f}, relative_volume={relative_volume:.4f}, volume_average={volume_average:.2f}, vwap={vwap:.4f}",
            "structure": f"regime={regime}, swings={swing_count}, bos={bos_str}, choch={choch_str}",
            "levels": f"count={level_count}, touches={touch_count}, broken={broken_count}",
        }
        evidence = [
            self._item(
                request,
                observed,
                provider,
                category,
                f"{category}: {directions[category]}; {details[category]}",
                material,
                EvidenceType.TECHNICAL_INDICATOR,
                EvidenceStrength.MODERATE,
                source_provenance=feature_snapshot.provenance,
            )
            for category in details
        ]
        for key, evidence_type, category in (
            ("kronos_forecast", EvidenceType.FORECAST, "forecast"),
            ("backtest_result", EvidenceType.BACKTEST, "backtest"),
        ):
            if key in request.extra_context:
                value = request.extra_context[key]
                summary = self._external_summary(category, value)
                evidence.append(
                    self._item(
                        request,
                        observed,
                        category,
                        category,
                        summary,
                        material,
                        evidence_type,
                        EvidenceStrength.MODERATE if category == "forecast" else EvidenceStrength.STRONG,
                        calibrated=category == "backtest",
                        source_provenance=None,
                    )
                )
        return evidence

    def _evidence(
        self,
        request: AnalystRequest,
        observed: datetime,
        provider: str,
        snapshot: TechnicalAnalysisSnapshot,
        raw: dict[str, float | str | int],
        material: str,
    ) -> list[EvidenceItem]:
        """Legacy evidence builder (used by snapshot_from_bars path)."""
        values = {item.name: item.value for item in snapshot.indicator_values}
        cross = str(raw["crossover_state"])
        directions = {
            "trend": "bullish"
            if values["sma_slope"] > 0 and cross == "above"
            else "bearish"
            if values["sma_slope"] < 0 and cross == "below"
            else "mixed",
            "momentum": "bullish"
            if values["roc"] > 0 and values["macd_histogram"] >= 0 and values["rsi"] < 70
            else "bearish"
            if values["roc"] < 0 and values["macd_histogram"] <= 0 and values["rsi"] > 30
            else "mixed",
            "volatility": "neutral",
            "volume": "bullish" if values["obv"] > 0 else "bearish" if values["obv"] < 0 else "neutral",
            "structure": "bullish"
            if snapshot.structure.regime == "uptrend"
            else "bearish"
            if snapshot.structure.regime == "downtrend"
            else "neutral",
            "levels": "support holding"
            if any(x.level_type == "support" and not x.broken for x in snapshot.levels)
            else "resistance holding"
            if any(x.level_type == "resistance" and not x.broken for x in snapshot.levels)
            else "neutral",
        }
        details = {
            "trend": f"sma_slope={values['sma_slope']:.6f}, ema_slope={values['ema_slope']:.6f}, crossover={cross}, age={raw['crossover_age']}",
            "momentum": f"roc={values['roc']:.4f}, rsi={values['rsi']:.4f}, macd_histogram={values['macd_histogram']:.6f}",
            "volatility": f"atr={values['atr']:.6f}, bollinger_bandwidth={values['bollinger_bandwidth']:.6f}",
            "volume": f"obv={values['obv']:.0f}, relative_volume={values['relative_volume']:.4f}, volume_average={values['volume_average']:.2f}, vwap={values['vwap']:.4f}",
            "structure": f"regime={snapshot.structure.regime}, swings={len(snapshot.swing_points)}, bos={snapshot.structure.bos_timestamp}, choch={snapshot.structure.choch_timestamp}",
            "levels": f"count={len(snapshot.levels)}, touches={sum(x.touch_count for x in snapshot.levels)}, broken={sum(x.broken for x in snapshot.levels)}",
        }
        evidence = [
            self._item(
                request,
                observed,
                provider,
                category,
                f"{category}: {directions[category]}; {details[category]}",
                material,
                EvidenceType.TECHNICAL_INDICATOR,
                EvidenceStrength.MODERATE,
            )
            for category in details
        ]
        for key, evidence_type, category in (
            ("kronos_forecast", EvidenceType.FORECAST, "forecast"),
            ("backtest_result", EvidenceType.BACKTEST, "backtest"),
        ):
            if key in request.extra_context:
                value = request.extra_context[key]
                summary = self._external_summary(category, value)
                evidence.append(
                    self._item(
                        request,
                        observed,
                        category,
                        category,
                        summary,
                        material,
                        evidence_type,
                        EvidenceStrength.MODERATE if category == "forecast" else EvidenceStrength.STRONG,
                        calibrated=category == "backtest",
                    )
                )
        return evidence

    @staticmethod
    def _external_summary(category: str, value: Any) -> str:
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        text = str(data).lower()
        orientation = "bullish" if "up" in text or "positive" in text else "bearish" if "down" in text or "negative" in text else "neutral"
        return f"{category}: {orientation}; deterministic external research evidence"

    def _item(
        self,
        request: AnalystRequest,
        observed: datetime,
        source: str,
        category: str,
        summary: str,
        material: str,
        evidence_type: EvidenceType,
        strength: EvidenceStrength,
        *,
        calibrated: bool = False,
        source_provenance: Any = None,
    ) -> EvidenceItem:
        confidence = self.config.base_confidence
        provenance_records: list[ProvenanceRecord] = [ProvenanceRecord(source=source, retrieved_at=request.as_of, uri=None)]
        if source_provenance is not None:
            provenance_records.append(
                ProvenanceRecord(
                    source=f"mifp:{source_provenance.input_fingerprint}",
                    retrieved_at=source_provenance.source_retrieved_at,
                    uri=None,
                )
            )
        return EvidenceItem(
            evidence_id=str(uuid5(NAMESPACE_URL, f"technical-evidence|{material}|{category}")),
            evidence_type=evidence_type,
            observed_at=observed,
            available_at=observed,
            evaluated_at=request.as_of,
            valid_until=request.as_of + self.freshness.threshold(evidence_type),
            strength=strength,
            summary=summary,
            confidence=confidence,
            capped=not calibrated and confidence > self.config.uncalibrated_confidence_cap,
            calibration_status="historically calibrated" if calibrated else "uncalibrated",
            has_historical_calibration=calibrated,
            source_analyst=self.config.analyst_id,
            assumptions=[Assumption(description="Only information available at the evaluation time is used")],
            warnings=[AnalysisWarning(code="RESEARCH_ONLY", message="Evidence is not a trading decision")],
            limitations=[AnalysisLimitation(code="HISTORICAL_PATTERN", message="Historical patterns may not persist")],
            provenance=provenance_records,
        )

    def _trace(self, opinion_id: str, evidence: list[EvidenceItem], request: AnalystRequest, provider: str) -> AnalysisTrace:
        request_node, market_node, opinion_node = f"request:{opinion_id}", f"market-data:{opinion_id}", f"opinion:{opinion_id}"
        nodes = [
            TraceNode(node_id=request_node, node_type="analyst_request", created_at=request.as_of, metadata={}),
            TraceNode(node_id=market_node, node_type="market_data", created_at=request.as_of, metadata={"provider": provider}),
        ]
        edges = [TraceEdge(source_node_id=request_node, target_node_id=market_node, edge_type="requests")]
        for item in evidence:
            kind = (
                "forecast"
                if item.evidence_type is EvidenceType.FORECAST
                else "backtest"
                if item.evidence_type is EvidenceType.BACKTEST
                else "evidence"
            )
            source_node = market_node
            if kind != "evidence":
                source_node = f"{kind}:{opinion_id}"
                nodes.append(TraceNode(node_id=source_node, node_type=kind, created_at=request.as_of, metadata={}))
                edges.append(TraceEdge(source_node_id=request_node, target_node_id=source_node, edge_type="requests"))
            nodes.append(
                TraceNode(
                    node_id=item.evidence_id,
                    node_type="evidence",
                    created_at=request.as_of,
                    metadata={"category": item.summary.split(":", 1)[0]},
                )
            )
            edges.append(TraceEdge(source_node_id=source_node, target_node_id=item.evidence_id, edge_type="produces"))
        nodes.append(TraceNode(node_id=opinion_node, node_type="analyst_opinion", created_at=request.as_of, metadata={}))
        edges.extend(TraceEdge(source_node_id=item.evidence_id, target_node_id=opinion_node, edge_type="supports") for item in evidence)
        return validate_trace(
            AnalysisTrace(
                trace_id=str(uuid5(NAMESPACE_URL, f"technical-trace|{opinion_id}")), nodes=nodes, edges=edges, created_at=request.as_of
            )
        )

    def trace_for(self, opinion_id: str) -> AnalysisTrace | None:
        with self._lock:
            return self._traces.get(opinion_id)

    def _insufficient(self, request: AnalystRequest, material: str) -> AnalystOpinion:
        return AnalystOpinion(
            opinion_id=str(uuid5(NAMESPACE_URL, material + "|insufficient")),
            analyst_id=self.config.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
            confidence=self.confidence.assess(0),
            evidence=[],
            warnings=[AnalysisWarning(code="INSUFFICIENT_DATA", message="Not enough valid historical bars")],
            limitations=[AnalysisLimitation(code="NO_INDICATORS", message="No technical conclusion was produced")],
            generated_at=request.as_of,
            data_freshness=self.freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER),
        )

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        _require_aware_utc(request.as_of)
        extras = self._extras_from_context(request)

        # Normal analysis path: consume a FeatureSnapshot from FeatureService (MIFP).
        try:
            feature_request = self._feature_snapshot_request(request)
            feature_snapshot = self.feature_service.snapshot(feature_request, extras=extras)
        except (FeatureError, MarketDataError, ValueError):
            return self._insufficient(request, f"{request.model_dump_json()}|insufficient")

        key = cache_key_builder(
            "technical",
            self._bars_request(request),
            "raw",
            "regular",
            {"phase": 6, "config": repr(self.config)},
        )
        with self._lock:
            if key in self._opinions:
                return self._opinions[key]

        material = f"{request.model_dump_json()}|{key}"
        # Use the latest bar timestamp from the feature snapshot as the observation time.
        feature_values = {str(item.feature_id): item for item in feature_snapshot.vector.values}
        observed = (
            feature_values["price.close"].observed_at
            if "price.close" in feature_values
            else feature_snapshot.provenance.source_retrieved_at
        )
        provider = feature_snapshot.provider
        snapshot = self._snapshot_from_features(feature_snapshot)
        evidence = self._evidence_from_features(request, observed, provider, snapshot, feature_snapshot, material)

        try:
            self.validator.validate(evidence, request.as_of, allow_stale=self.config.stale_input_allowed)
        except AnalystError:
            return self._insufficient(request, material)

        synthesis = self.synthesizer.synthesize(evidence, request.as_of)
        opinion_id = str(uuid5(NAMESPACE_URL, f"technical-opinion|{material}|{synthesis.direction.value}"))
        opinion = AnalystOpinion(
            opinion_id=opinion_id,
            analyst_id=self.config.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=synthesis.direction,
            confidence=self.confidence.assess(
                synthesis.confidence,
                calibrated=synthesis.calibrated,
                stale_fraction=synthesis.stale_fraction,
                conflict_fraction=synthesis.conflict_fraction,
            ),
            evidence=evidence,
            assumptions=[Assumption(description="Historical price and volume patterns may not persist")],
            warnings=[AnalysisWarning(code="RESEARCH_ONLY", message="This opinion is not a trading decision")],
            limitations=[AnalysisLimitation(code="LAGGING_INDICATORS", message="Indicators derive from historical observations")],
            generated_at=request.as_of,
            data_freshness=self.freshness.assess(
                observed,
                observed,
                request.as_of,
                EvidenceType.TECHNICAL_INDICATOR,
            ),
        )
        trace = self._trace(opinion_id, evidence, request, provider)
        with self._lock:
            self._opinions[key], self._traces[opinion_id], self._snapshots[key] = opinion, trace, snapshot
        return opinion

    def _has_sufficient_bars(self, request: AnalystRequest) -> bool:
        try:
            bars = self._fetch_bars(request)
            return len(bars) >= self._minimum_bars()
        except MarketDataError:
            return False

    def _fetch_bars(self, request: AnalystRequest) -> list[OHLCVBar]:
        """Fetch raw bars for structured snapshot construction (swings, levels)."""
        bars_request = self._bars_request(request)
        result = self.provider.get_bars(bars_request)
        return [bar for bar in result.bars if bar.timestamp <= request.as_of]


def _parse_optional_timestamp(value: Any) -> datetime | None:
    """Parse a scalar feature value that may be an ISO timestamp string or None."""
    if value is None:
        return None
    raw = value.value if hasattr(value, "value") else value
    if raw is None:
        return None
    if isinstance(raw, str):
        return datetime.fromisoformat(raw)
    return None


def _feature_timestamp_str(value: Any) -> str:
    """Return a string representation of a timestamp feature value (or 'None')."""
    if value is None:
        return "None"
    raw = value.value if hasattr(value, "value") else value
    return str(raw) if raw is not None else "None"


def _as_int(value: Any) -> int:
    """Extract an int from a FeatureValue-like object or return 0."""
    if value is None:
        return 0
    raw = value.value if hasattr(value, "value") else value
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, (int, float)):
        return int(raw)
    return 0


def _as_regime(value: Any) -> Literal["uptrend", "downtrend", "range_bound"]:
    """Extract a typed market-regime from a FeatureValue, defaulting to range_bound."""
    if value is None:
        return "range_bound"
    raw = value.value if hasattr(value, "value") else value
    if not isinstance(raw, str):
        return "range_bound"
    if raw == "uptrend":
        regime: Literal["uptrend", "downtrend", "range_bound"] = "uptrend"
    elif raw == "downtrend":
        regime = "downtrend"
    else:
        regime = "range_bound"
    return regime
