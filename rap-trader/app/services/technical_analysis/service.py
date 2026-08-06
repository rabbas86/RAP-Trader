"""Deterministic, causal, research-only technical analyst."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import sqrt
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
from app.domain.models.market_data import HistoricalBarsRequest, MarketDataError, OHLCVBar, Symbol, Timeframe, _require_aware_utc
from app.domain.models.technical import TechnicalAnalysisSnapshot, TechnicalIndicatorValue
from app.services.analyst.service import Analyst, ConfidenceAssessmentService, DataFreshnessService, EvidenceValidationService
from app.services.market_data import MarketDataProvider, MockMarketDataProvider, cache_key_builder
from app.services.technical_analysis.levels import clustered_levels
from app.services.technical_analysis.structure import classify_structure, confirmed_swings
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

    def __init__(self, provider: MarketDataProvider | None = None, config: TechnicalAnalystConfig | None = None) -> None:
        self.provider, self.config = provider or MockMarketDataProvider(), config or TechnicalAnalystConfig()
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

    @staticmethod
    def sma(values: list[float], period: int) -> float:
        if period <= 0 or len(values) < period:
            raise ValueError("insufficient values for SMA")
        return sum(values[-period:]) / period

    @staticmethod
    def ema_series(values: list[float], period: int) -> list[float]:
        if period <= 0 or len(values) < period:
            raise ValueError("insufficient values for EMA")
        alpha, result = 2 / (period + 1), [sum(values[:period]) / period]
        for value in values[period:]:
            result.append(value * alpha + result[-1] * (1 - alpha))
        return result

    @classmethod
    def ema(cls, values: list[float], period: int) -> float:
        return cls.ema_series(values, period)[-1]

    @staticmethod
    def moving_average_slope(series: list[float], period: int, *, exponential: bool = False) -> float:
        if len(series) < period + 1:
            raise ValueError("insufficient values for moving-average slope")
        current = TechnicalAnalyst.ema(series, period) if exponential else TechnicalAnalyst.sma(series, period)
        previous = TechnicalAnalyst.ema(series[:-1], period) if exponential else TechnicalAnalyst.sma(series[:-1], period)
        return (current - previous) / previous if previous else 0.0

    @classmethod
    def crossover(cls, values: list[float], fast: int, slow: int) -> tuple[str, int]:
        if fast >= slow or len(values) < slow:
            raise ValueError("insufficient values for crossover")
        states = []
        for end in range(slow, len(values) + 1):
            states.append(cls.sma(values[:end], fast) >= cls.sma(values[:end], slow))
        age = 0
        for state in reversed(states[:-1]):
            if state == states[-1]:
                age += 1
            else:
                break
        return ("above" if states[-1] else "below", age)

    @staticmethod
    def roc(values: list[float], period: int) -> float:
        if period <= 0 or len(values) <= period:
            raise ValueError("insufficient values for ROC")
        return (values[-1] / values[-period - 1] - 1) * 100

    @staticmethod
    def true_ranges(bars: list[OHLCVBar]) -> list[float]:
        if not bars:
            raise ValueError("bars required")
        return [
            bar.high - bar.low
            if index == 0
            else max(bar.high - bar.low, abs(bar.high - bars[index - 1].close), abs(bar.low - bars[index - 1].close))
            for index, bar in enumerate(bars)
        ]

    @classmethod
    def atr(cls, bars: list[OHLCVBar], period: int) -> float:
        ranges = cls.true_ranges(bars)
        if period <= 0 or len(ranges) < period:
            raise ValueError("insufficient values for ATR")
        value = sum(ranges[:period]) / period
        for item in ranges[period:]:
            value = (value * (period - 1) + item) / period
        return value

    @classmethod
    def bollinger_bands(cls, values: list[float], period: int = 20, deviations: float = 2) -> tuple[float, float, float]:
        middle = cls.sma(values, period)
        window = values[-period:]
        std = sqrt(sum((value - middle) ** 2 for value in window) / period)
        return middle - deviations * std, middle, middle + deviations * std

    @staticmethod
    def bollinger_bandwidth(lower: float, middle: float, upper: float) -> float:
        return (upper - lower) / middle if middle else 0.0

    @staticmethod
    def obv(bars: list[OHLCVBar]) -> float:
        if not bars:
            raise ValueError("bars required")
        total = 0
        for previous, current in pairwise(bars):
            total += current.volume if current.close > previous.close else -current.volume if current.close < previous.close else 0
        return float(total)

    @staticmethod
    def rolling_volume_average(bars: list[OHLCVBar], period: int) -> float:
        if period <= 0 or len(bars) < period:
            raise ValueError("insufficient volume values")
        return sum(bar.volume for bar in bars[-period:]) / period

    @classmethod
    def relative_volume(cls, bars: list[OHLCVBar], period: int) -> float:
        average = cls.rolling_volume_average(bars, period)
        return bars[-1].volume / average if average else 0.0

    @staticmethod
    def vwap(bars: list[OHLCVBar]) -> float:
        volume = sum(bar.volume for bar in bars)
        if not bars or not volume:
            raise ValueError("positive aggregate volume required")
        return sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars) / volume

    @staticmethod
    def rsi(values: list[float], period: int) -> float:
        if period <= 0 or len(values) < period + 1:
            raise ValueError("insufficient values for RSI")
        deltas = [b - a for a, b in pairwise(values)]
        gains = [max(x, 0.0) for x in deltas]
        losses = [max(-x, 0.0) for x in deltas]
        gain, loss = sum(gains[:period]) / period, sum(losses[:period]) / period
        for g, item in zip(gains[period:], losses[period:], strict=True):
            gain, loss = (gain * (period - 1) + g) / period, (loss * (period - 1) + item) / period
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    @classmethod
    def macd(cls, values: list[float], fast: int, slow: int, signal: int) -> tuple[float, float, float]:
        if fast >= slow or len(values) < slow:
            raise ValueError("insufficient values for MACD")
        fast_values, slow_values = cls.ema_series(values, fast), cls.ema_series(values, slow)
        line = [a - b for a, b in zip(fast_values[slow - fast :], slow_values, strict=True)]
        signal_value = cls.ema(line, signal) if len(line) >= signal else sum(line) / len(line)
        return line[-1], signal_value, line[-1] - signal_value

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

    def snapshot_from_bars(self, bars: list[OHLCVBar], timeframe: Timeframe, generated_at: datetime) -> TechnicalAnalysisSnapshot:
        _require_aware_utc(generated_at)
        causal = [bar for bar in bars if bar.timestamp <= generated_at]
        if len(causal) < self._minimum_bars():
            raise AnalystError(AnalystErrorCodes.INSUFFICIENT_DATA, "Not enough historical bars")
        indicators, _ = self._indicator_values(causal)
        swings = confirmed_swings(causal)
        structure = classify_structure(causal, swings)
        levels = clustered_levels(swings, causal[-1].close, tolerance=self.config.level_tolerance, limit=self.config.level_limit)
        return TechnicalAnalysisSnapshot(
            bars_analyzed=len(causal),
            timeframe=timeframe,
            indicator_values=indicators,
            swing_points=swings,
            structure=structure,
            levels=levels,
            generated_at=generated_at,
        )

    def snapshot(self, request: AnalystRequest) -> TechnicalAnalysisSnapshot:
        self.validate_input(request)
        bars_request = self._bars_request(request)
        result = self.provider.get_bars(bars_request)
        return self.snapshot_from_bars(result.bars, request.timeframe, request.as_of)

    def _evidence(
        self,
        request: AnalystRequest,
        observed: datetime,
        provider: str,
        snapshot: TechnicalAnalysisSnapshot,
        raw: dict[str, float | str | int],
        material: str,
    ) -> list[EvidenceItem]:
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
    ) -> EvidenceItem:
        confidence = self.config.base_confidence
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
            provenance=[ProvenanceRecord(source=source, retrieved_at=request.as_of, uri=None)],
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
        bars_request = self._bars_request(request)
        key = cache_key_builder("technical", bars_request, "raw", "regular", {"phase": 6, "config": repr(self.config)})
        with self._lock:
            if key in self._opinions:
                return self._opinions[key]
        material = f"{request.model_dump_json()}|{key}"
        try:
            result = self.provider.get_bars(bars_request)
        except MarketDataError:
            return self._insufficient(request, material)
        bars = [bar for bar in result.bars if bar.timestamp <= request.as_of]
        if len(bars) < self._minimum_bars():
            return self._insufficient(request, material)
        snapshot = self.snapshot_from_bars(bars, request.timeframe, request.as_of)
        _, raw = self._indicator_values(bars)
        evidence = self._evidence(request, bars[-1].timestamp, result.provider, snapshot, raw, material)
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
            data_freshness=self.freshness.assess(bars[-1].timestamp, bars[-1].timestamp, request.as_of, EvidenceType.TECHNICAL_INDICATOR),
        )
        trace = self._trace(opinion_id, evidence, request, result.provider)
        with self._lock:
            self._opinions[key], self._traces[opinion_id], self._snapshots[key] = opinion, trace, snapshot
        return opinion
