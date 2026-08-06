"""Market Intelligence Feature Platform orchestration service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.features import (
    FeatureCategory,
    FeatureDependency,
    FeatureId,
    FeatureMetadata,
    FeatureSnapshot,
    FeatureSnapshotRequest,
    FeatureStoreHealth,
    FeatureValue,
    FeatureVector,
)
from app.domain.models.market_data import HistoricalBarsRequest, Symbol
from app.services.features.cache import FeatureCacheKey, configuration_hash
from app.services.features.feature_store import FeatureStore
from app.services.features.freshness import STEPS, FeatureFreshnessService
from app.services.features.generators import (
    BacktestFeatureGenerator,
    KronosFeatureGenerator,
    MomentumFeatureGenerator,
    PriceFeatureGenerator,
    StructureFeatureGenerator,
    SupportResistanceFeatureGenerator,
    TrendFeatureGenerator,
    VolatilityFeatureGenerator,
    VolumeFeatureGenerator,
)
from app.services.features.provenance import build_provenance
from app.services.features.registry import FeatureComputationContext, FeatureRegistry
from app.services.features.validation import validate_bars
from app.services.market_data import MarketDataProvider, MockMarketDataProvider

_FEATURES: dict[FeatureCategory, tuple[str, ...]] = {
    FeatureCategory.PRICE: ("price.close", "price.high", "price.low", "price.open", "price.return_1", "price.typical"),
    FeatureCategory.TREND: (
        "trend.crossover_age",
        "trend.crossover_state",
        "trend.ema_12",
        "trend.ema_26",
        "trend.ema_slope",
        "trend.sma_10",
        "trend.sma_20",
        "trend.sma_50",
        "trend.sma_slope",
    ),
    FeatureCategory.MOMENTUM: (
        "momentum.macd",
        "momentum.macd_histogram",
        "momentum.macd_signal",
        "momentum.roc_12",
        "momentum.rsi_14",
    ),
    FeatureCategory.VOLATILITY: (
        "volatility.atr_14",
        "volatility.bollinger_bandwidth",
        "volatility.bollinger_lower",
        "volatility.bollinger_middle",
        "volatility.bollinger_upper",
        "volatility.true_range",
    ),
    FeatureCategory.VOLUME: ("volume.average_20", "volume.obv", "volume.relative_20", "volume.vwap"),
    FeatureCategory.STRUCTURE: (
        "structure.bos_timestamp",
        "structure.choch_timestamp",
        "structure.higher_highs",
        "structure.higher_lows",
        "structure.lower_highs",
        "structure.lower_lows",
        "structure.regime",
        "structure.swing_count",
    ),
    FeatureCategory.SUPPORT_RESISTANCE: (
        "support_resistance.broken_count",
        "support_resistance.level_count",
        "support_resistance.nearest_resistance",
        "support_resistance.nearest_support",
        "support_resistance.touch_count",
    ),
    FeatureCategory.KRONOS: (
        "kronos.forecast_change",
        "kronos.forecast_high",
        "kronos.forecast_horizon",
        "kronos.forecast_low",
        "kronos.forecast_mean_close",
    ),
    FeatureCategory.BACKTEST: (),
}

_GENERATORS: dict[FeatureCategory, type[Any]] = {
    FeatureCategory.PRICE: PriceFeatureGenerator,
    FeatureCategory.TREND: TrendFeatureGenerator,
    FeatureCategory.MOMENTUM: MomentumFeatureGenerator,
    FeatureCategory.VOLATILITY: VolatilityFeatureGenerator,
    FeatureCategory.VOLUME: VolumeFeatureGenerator,
    FeatureCategory.STRUCTURE: StructureFeatureGenerator,
    FeatureCategory.SUPPORT_RESISTANCE: SupportResistanceFeatureGenerator,
    FeatureCategory.KRONOS: KronosFeatureGenerator,
    FeatureCategory.BACKTEST: BacktestFeatureGenerator,
}


class FeatureService:
    def __init__(
        self,
        provider: MarketDataProvider | None = None,
        registry: FeatureRegistry | None = None,
        store: FeatureStore | None = None,
        freshness: FeatureFreshnessService | None = None,
    ) -> None:
        self.provider = provider or MockMarketDataProvider()
        self.registry = registry or FeatureRegistry()
        self.store = store or FeatureStore()
        self.freshness = freshness or FeatureFreshnessService()
        if registry is None:
            self._register_defaults()

    def _register_defaults(self) -> None:
        for category, names in _FEATURES.items():
            generator = _GENERATORS[category]
            for name in names:
                dependencies = () if category is FeatureCategory.PRICE else (FeatureDependency(feature_id=FeatureId("price.close")),)
                metadata = FeatureMetadata(
                    feature_id=FeatureId(name),
                    category=category,
                    display_name=name.replace(".", " ").replace("_", " ").title(),
                    description=f"Deterministic {category.value} market feature",
                    version=generator.version,
                    generator=f"{generator.__module__}.{generator.__name__}",
                    dependencies=dependencies,
                )
                self.registry.register(metadata, self._computation(category, name))
        self.registry.validate()

    @staticmethod
    def _computation(category: FeatureCategory, name: str):  # type: ignore[no-untyped-def]
        generator = _GENERATORS[category]

        def compute(context: FeatureComputationContext, _dependencies: dict[str, Any]) -> Any:
            if category.value not in context.batches:
                if category is FeatureCategory.KRONOS:
                    values = generator.generate(context.extras.get("kronos_forecast"))
                elif category is FeatureCategory.BACKTEST:
                    values = generator.generate(context.extras.get("backtest_metrics"))
                else:
                    values = generator.generate(context.bars)
                context.batches[category.value] = values
            return context.batches[category.value].get(name)

        return compute

    def categories(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for category in FeatureCategory:
            result[category.value] = tuple(str(metadata.feature_id) for metadata in self.registry.list() if metadata.category is category)
        return result

    def health(self) -> FeatureStoreHealth:
        provider_health = self.provider.health()
        healthy = provider_health.configured and provider_health.reachable is not False
        return FeatureStoreHealth(
            status="healthy" if healthy else "degraded",
            checked_at=datetime.now(UTC),
            statistics=self.store.statistics(len(self.registry.list())),
            detail=f"feature platform using {provider_health.provider}: {provider_health.status}",
        )

    def snapshot(self, request: FeatureSnapshotRequest, *, extras: dict[str, Any] | None = None) -> FeatureSnapshot:
        provider_name = self.provider.health().provider
        selected = "*" if request.feature_ids is None else ",".join(sorted(str(item) for item in request.feature_ids))
        effective_configuration = tuple(sorted((*request.configuration, ("feature_ids", selected))))
        key = FeatureCacheKey(
            ticker=request.ticker,
            timeframe=request.timeframe,
            provider=provider_name,
            adjustment=request.adjustment,
            session=request.session,
            as_of=request.as_of,
            configuration_hash=configuration_hash(effective_configuration),
        )
        cached = self.store.get(key)
        if cached is not None:
            return cached
        bars_request = HistoricalBarsRequest(
            symbol=Symbol(request.ticker),
            timeframe=request.timeframe,
            start=request.as_of - STEPS[request.timeframe] * request.lookback,
            end=request.as_of,
            limit=request.lookback,
            adjustment=request.adjustment,
            session=request.session,
        )
        result = self.provider.get_bars(bars_request)
        bars = validate_bars(result.bars, request.as_of, minimum=52)
        context = FeatureComputationContext(bars=bars, as_of=request.as_of, extras={} if extras is None else extras)
        raw = self.registry.compute_many(context, request.feature_ids)
        metadata_by_id = {str(item.feature_id): item for item in self.registry.list()}
        metadata = tuple(metadata_by_id[name] for name in sorted(raw))
        observed_at = bars[-1].timestamp
        values = tuple(
            FeatureValue(
                feature_id=FeatureId(name),
                value=raw[name],
                observed_at=observed_at,
                category=metadata_by_id[name].category,
                version=metadata_by_id[name].version,
            )
            for name in sorted(raw)
        )
        generated_at = request.as_of
        provenance = build_provenance(result, metadata, self.registry.dependency_snapshot(), effective_configuration, generated_at)
        snapshot = FeatureSnapshot(
            snapshot_id=str(uuid5(NAMESPACE_URL, f"mifp|{key}|{provenance.input_fingerprint}|{','.join(raw)}")),
            ticker=request.ticker,
            timeframe=request.timeframe,
            provider=result.provider,
            adjustment=result.adjustment,
            session=result.session,
            as_of=request.as_of,
            generated_at=generated_at,
            vector=FeatureVector(values=values),
            provenance=provenance,
            stale=self.freshness.is_stale(observed_at, request.as_of, request.timeframe),
            age_seconds=self.freshness.age_seconds(observed_at, request.as_of),
        )
        self.store.put(key, snapshot)
        return snapshot
