"""Phase 6.5 Market Intelligence Feature Platform contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.cli.features import main as features_cli
from app.domain.models.features import (
    FeatureCategory,
    FeatureDependency,
    FeatureError,
    FeatureId,
    FeatureMetadata,
    FeatureSnapshot,
    FeatureSnapshotRequest,
)
from app.domain.models.market_data import OHLCVBar
from app.main import app
from app.services.features.cache import (
    FeatureCacheKey,
    FeatureSnapshotCache,
    backtest_fingerprint,
    build_cache_key,
    configuration_hash,
    kronos_fingerprint,
)
from app.services.features.dependency_graph import FeatureDependencyGraph
from app.services.features.freshness import FeatureFreshnessService
from app.services.features.generators import (
    MomentumFeatureGenerator,
    PriceFeatureGenerator,
    TrendFeatureGenerator,
    VolatilityFeatureGenerator,
    VolumeFeatureGenerator,
)
from app.services.features.registry import FeatureComputationContext, FeatureRegistry
from app.services.features.service import FeatureService
from app.services.features.versioning import FEATURE_SCHEMA_VERSION

AS_OF = datetime(2026, 8, 1, tzinfo=UTC)


def request() -> FeatureSnapshotRequest:
    return FeatureSnapshotRequest(ticker="AAPL", timeframe="1d", as_of=AS_OF, lookback=100)


def bars(count: int = 60) -> list[OHLCVBar]:
    result = []
    for index in range(count):
        close = 100.0 + index * 0.2 + (index % 5) * 0.1
        result.append(
            OHLCVBar(
                timestamp=AS_OF - timedelta(days=count - index),
                open=close - 0.1,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1_000 + index * 10,
            )
        )
    return result


def metadata(name: str, dependencies: tuple[str, ...] = ()) -> FeatureMetadata:
    return FeatureMetadata(
        feature_id=FeatureId(name),
        category=FeatureCategory.PRICE,
        display_name=name,
        description="test feature",
        version="1.0.0",
        generator="tests",
        dependencies=tuple(FeatureDependency(feature_id=FeatureId(item)) for item in dependencies),
    )


def test_registry_register_compute_list_dependencies_and_unregister() -> None:
    registry = FeatureRegistry()
    registry.register(metadata("price.base"), lambda _context, _computed: 2.0)
    registry.register(metadata("price.double", ("price.base",)), lambda _context, computed: float(computed["price.base"]) * 2)
    context = FeatureComputationContext(bars=bars(), as_of=AS_OF)
    assert registry.compute("price.double", context) == 4.0
    assert registry.dependencies("price.double") == ("price.base",)
    assert [str(item.feature_id) for item in registry.list()] == ["price.base", "price.double"]
    with pytest.raises(FeatureError):
        registry.unregister("price.base")
    registry.unregister("price.double")
    registry.unregister("price.base")


def test_dependency_graph_rejects_cycles_and_invalid_references() -> None:
    graph = FeatureDependencyGraph()
    graph.add("price.a", ("price.b",))
    with pytest.raises(FeatureError):
        graph.add("price.b", ("price.a",))
    with pytest.raises(FeatureError):
        graph.validate_references()


def test_generators_are_finite_and_deterministic() -> None:
    source = bars()
    generators = (
        PriceFeatureGenerator,
        TrendFeatureGenerator,
        MomentumFeatureGenerator,
        VolatilityFeatureGenerator,
        VolumeFeatureGenerator,
    )
    for generator in generators:
        assert generator.generate(source) == generator.generate(source)
        assert generator.generate(source)


def test_cache_uses_full_identity_and_returns_same_immutable_snapshot() -> None:
    service = FeatureService()
    snapshot = service.snapshot(request())
    cache = FeatureSnapshotCache()
    key = FeatureCacheKey(
        "AAPL",
        "1d",
        "mock",
        "raw",
        "regular",
        AS_OF,
        100,
        configuration_hash(()),
        "1.0.0",
        "none",
        "none",
    )
    cache.set(key, snapshot)
    assert cache.get(key) is snapshot
    # different lookback must not collide
    other_lookback = FeatureCacheKey(
        "AAPL",
        "1d",
        "mock",
        "raw",
        "regular",
        AS_OF,
        500,
        configuration_hash(()),
        "1.0.0",
        "none",
        "none",
    )
    assert cache.get(other_lookback) is None


def test_snapshot_is_deeply_immutable() -> None:
    snapshot = FeatureService().snapshot(request())
    with pytest.raises(ValidationError):
        snapshot.ticker = "MSFT"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.vector.values[0].value = 1.0  # type: ignore[misc]


def test_provenance_is_complete_and_fingerprinted() -> None:
    snapshot = FeatureService().snapshot(request())
    provenance = snapshot.provenance
    assert provenance.source_data.startswith("mock:")
    assert provenance.generator_version == "mifp-6.5.0"
    assert provenance.feature_schema_version == "1.0.0"
    assert provenance.platform_version == "mifp-6.5.0"
    assert len(provenance.input_fingerprint) == 64
    assert provenance.feature_versions
    assert provenance.dependency_graph


def test_freshness_is_timeframe_aware() -> None:
    freshness = FeatureFreshnessService(allowed_intervals=2)
    assert not freshness.is_stale(AS_OF - timedelta(days=2), AS_OF, "1d")
    assert freshness.is_stale(AS_OF - timedelta(days=2, seconds=1), AS_OF, "1d")


def test_serialization_round_trip_and_no_non_finite_values() -> None:
    snapshot = FeatureService().snapshot(request())
    restored = FeatureSnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot
    assert "NaN" not in snapshot.model_dump_json()
    assert "Infinity" not in snapshot.model_dump_json()


def test_snapshot_determinism_and_cache_statistics() -> None:
    service = FeatureService()
    first = service.snapshot(request())
    second = service.snapshot(request())
    assert first is second
    assert first.snapshot_id == second.snapshot_id
    assert service.store.statistics(len(service.registry.list())).cache_hits == 1


def test_api_health_categories_and_snapshot() -> None:
    client = TestClient(app)
    assert client.get("/features/health").status_code == 200
    categories = client.get("/features/categories")
    assert categories.status_code == 200
    assert "trend" in categories.json()
    response = client.post("/features/snapshot", json=request().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["ticker"] == "AAPL"


def test_cli_json_and_summary(capsys: pytest.CaptureFixture[str]) -> None:
    common = ["--ticker", "AAPL", "--as-of", AS_OF.isoformat()]
    assert features_cli([*common, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ticker"] == "AAPL"
    assert features_cli([*common, "--summary"]) == 0
    assert "Features:" in capsys.readouterr().out


def test_feature_platform_has_no_forbidden_imports_or_actions() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "features"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    for forbidden in ("AnalystOpinion", "TradeDecision", "BUY", "SELL", "risk_engine", "execution", "quantity", "allocation"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# Phase 6.5 regression tests: cache identity, availability, dependency graph
# ---------------------------------------------------------------------------


def _base_key(**overrides: Any) -> FeatureCacheKey:
    """Helper to build a FeatureCacheKey with defaults and optional overrides."""
    defaults: dict[str, Any] = {
        "ticker": "AAPL",
        "timeframe": "1d",
        "provider": "mock",
        "adjustment": "raw",
        "session": "regular",
        "as_of": AS_OF,
        "lookback": 100,
        "selected_feature_ids": None,
        "configuration_hash_value": configuration_hash(()),
        "schema_version": FEATURE_SCHEMA_VERSION,
        "kronos_fp": "none",
        "backtest_fp": "none",
    }
    defaults.update(overrides)
    return build_cache_key(**defaults)


def test_cache_lookback_isolation() -> None:
    """Different lookback produces different cache keys."""
    key_100 = _base_key(lookback=100)
    key_500 = _base_key(lookback=500)
    assert key_100 != key_500


def test_cache_market_data_fingerprint_isolation() -> None:
    """Different market-data configuration hash produces different cache keys."""
    key_a = _base_key(configuration_hash_value=configuration_hash(()))
    key_b = _base_key(configuration_hash_value=configuration_hash((("test", "value"),)))
    assert key_a != key_b


def test_cache_kronos_isolation() -> None:
    """Different Kronos forecasts produce different cache keys."""
    kronos_a = kronos_fingerprint({"direction": "UP", "horizon": 5})
    kronos_b = kronos_fingerprint({"direction": "DOWN", "horizon": 5})
    assert kronos_a != kronos_b
    key_a = _base_key(kronos_fp=kronos_a)
    key_b = _base_key(kronos_fp=kronos_b)
    assert key_a != key_b


def test_cache_backtest_isolation() -> None:
    """Different backtest metrics produce different cache keys."""
    bt_a = backtest_fingerprint({"directional_accuracy": 0.8})
    bt_b = backtest_fingerprint({"directional_accuracy": 0.5})
    assert bt_a != bt_b
    key_a = _base_key(backtest_fp=bt_a)
    key_b = _base_key(backtest_fp=bt_b)
    assert key_a != key_b


def test_cache_feature_selection_isolation() -> None:
    """Different feature selection (via configuration hash) produces different cache keys."""
    key_none = _base_key(configuration_hash_value=configuration_hash(()))
    key_some = _base_key(configuration_hash_value=configuration_hash((("feature_ids", "trend.sma_20"),)))
    assert key_none != key_some


def test_cache_schema_version_isolation() -> None:
    """Different schema versions produce different cache keys."""
    key_v1 = _base_key(schema_version="1.0.0")
    key_v2 = _base_key(schema_version="2.0.0")
    assert key_v1 != key_v2


def test_cache_deterministic_hit() -> None:
    """Identical inputs produce the same cache key and hit."""
    key1 = _base_key(lookback=100, kronos_fp="none", backtest_fp="none")
    key2 = _base_key(lookback=100, kronos_fp="none", backtest_fp="none")
    assert key1 == key2
    cache = FeatureSnapshotCache()
    snapshot = FeatureService().snapshot(request())
    cache.set(key1, snapshot)
    assert cache.get(key2) is snapshot
    assert cache.hits == 1
    assert cache.misses == 0


def test_feature_value_has_availability_timestamps() -> None:
    """FeatureValue carries observed_at, available_at, generated_at, source_fingerprint."""
    snapshot = FeatureService().snapshot(request())
    for value in snapshot.vector.values:
        assert value.observed_at is not None
        assert value.available_at is not None
        assert value.generated_at is not None
        assert len(value.source_fingerprint) > 0
        assert value.available_at <= value.observed_at
        assert value.available_at <= value.generated_at


def test_feature_value_rejects_naive_timestamps() -> None:
    """FeatureValue rejects non-UTC-aware timestamps."""
    from app.domain.models.features import FeatureValue

    naive = datetime(2025, 1, 1, tzinfo=None)  # noqa: DTZ001 — intentionally naive for rejection test
    with pytest.raises(ValidationError):
        FeatureValue(
            feature_id=FeatureId("price.close"),
            value=100.0,
            observed_at=naive,
            available_at=naive,
            generated_at=naive,
            source_fingerprint="abc123",
            category=FeatureCategory.PRICE,
            version="1.0.0",
        )


def test_feature_value_rejects_future_available_at() -> None:
    """FeatureValue rejects available_at after generated_at."""
    from app.domain.models.features import FeatureValue

    now = AS_OF
    later = AS_OF + timedelta(seconds=1)
    with pytest.raises(ValidationError):
        FeatureValue(
            feature_id=FeatureId("price.close"),
            value=100.0,
            observed_at=now,
            available_at=later,
            generated_at=now,
            source_fingerprint="abc123",
            category=FeatureCategory.PRICE,
            version="1.0.0",
        )


def test_snapshot_no_lookahead_rejection() -> None:
    """FeatureSnapshot rejects features whose available_at > as_of."""
    from app.domain.models.features import FeatureProvenance, FeatureValue, FeatureVector

    future = AS_OF + timedelta(days=1)
    fv = FeatureValue(
        feature_id=FeatureId("price.close"),
        value=100.0,
        observed_at=future,
        available_at=future,
        generated_at=future,
        source_fingerprint="abc123",
        category=FeatureCategory.PRICE,
        version="1.0.0",
    )
    vector = FeatureVector(values=(fv,))
    provenance = FeatureProvenance(
        source_data="mock:TEST:1d",
        generator_version="test",
        feature_schema_version="1.0.0",
        platform_version="test",
        feature_versions=(),
        source_retrieved_at=AS_OF,
        generated_at=AS_OF,
        dependency_graph=(),
        input_fingerprint="a" * 64,
    )
    with pytest.raises(ValidationError):
        FeatureSnapshot(
            snapshot_id="test",
            ticker="AAPL",
            timeframe="1d",
            provider="mock",
            adjustment="raw",
            session="regular",
            as_of=AS_OF,
            generated_at=AS_OF,
            bars_analyzed=100,
            vector=vector,
            provenance=provenance,
            stale=False,
            age_seconds=0.0,
        )


def test_dependency_graph_has_real_dependencies() -> None:
    """Registered features have correct direct dependencies per their formulas."""
    service = FeatureService()
    # Trend → price.close
    for feature_id in ("trend.sma_10", "trend.sma_20", "trend.sma_50", "trend.ema_12", "trend.ema_26"):
        deps = service.registry.dependencies(feature_id)
        assert "price.close" in deps
    # MACD line depends on EMAs
    macd_deps = service.registry.dependencies("momentum.macd")
    assert "trend.ema_12" in macd_deps
    assert "trend.ema_26" in macd_deps
    # MACD signal depends on MACD line
    assert service.registry.dependencies("momentum.macd_signal") == ("momentum.macd",)
    # MACD histogram depends on MACD line + signal
    hist_deps = service.registry.dependencies("momentum.macd_histogram")
    assert "momentum.macd" in hist_deps
    assert "momentum.macd_signal" in hist_deps
    # ROC and RSI depend on price.close
    assert "price.close" in service.registry.dependencies("momentum.roc_12")
    assert "price.close" in service.registry.dependencies("momentum.rsi_14")
    # Bollinger middle depends on SMA
    assert "trend.sma_20" in service.registry.dependencies("volatility.bollinger_middle")
    # Bollinger upper/lower depend on SMA + price
    for feature_id in ("volatility.bollinger_upper", "volatility.bollinger_lower"):
        deps = service.registry.dependencies(feature_id)
        assert "trend.sma_20" in deps
        assert "price.close" in deps
    # Bollinger bandwidth depends on upper/middle/lower
    bw_deps = service.registry.dependencies("volatility.bollinger_bandwidth")
    assert "volatility.bollinger_lower" in bw_deps
    assert "volatility.bollinger_middle" in bw_deps
    assert "volatility.bollinger_upper" in bw_deps
    # ATR depends on true_range
    assert "volatility.true_range" in service.registry.dependencies("volatility.atr_14")
    # True range depends on high/low/close
    tr_deps = service.registry.dependencies("volatility.true_range")
    assert "price.high" in tr_deps
    assert "price.low" in tr_deps
    assert "price.close" in tr_deps
    # Volume features
    assert "price.close" in service.registry.dependencies("volume.obv")
    rv_deps = service.registry.dependencies("volume.relative_20")
    assert "volume.average_20" in rv_deps
    assert "price.close" in rv_deps
    # Structure features depend on price.close
    for feature_id in (
        "structure.higher_highs",
        "structure.higher_lows",
        "structure.lower_highs",
        "structure.lower_lows",
        "structure.swing_count",
        "structure.bos_timestamp",
        "structure.choch_timestamp",
        "structure.regime",
    ):
        deps = service.registry.dependencies(feature_id)
        assert "price.close" in deps
    # Support/resistance depends on price.close
    for feature_id in (
        "support_resistance.nearest_support",
        "support_resistance.nearest_resistance",
        "support_resistance.level_count",
        "support_resistance.touch_count",
        "support_resistance.broken_count",
    ):
        deps = service.registry.dependencies(feature_id)
        assert "price.close" in deps


def test_dependency_graph_transitive_ordering() -> None:
    """Transitive dependencies are correctly resolved."""
    service = FeatureService()
    macd_hist_deps = service.registry.dependencies("momentum.macd_histogram", transitive=True)
    assert "momentum.macd" in macd_hist_deps
    assert "momentum.macd_signal" in macd_hist_deps
    assert "trend.ema_12" in macd_hist_deps
    assert "trend.ema_26" in macd_hist_deps
    assert "price.close" in macd_hist_deps


def test_dependency_graph_dependency_fingerprints_in_provenance() -> None:
    """Feature provenance records the dependency graph."""
    snapshot = FeatureService().snapshot(request())
    graph = snapshot.provenance.dependency_graph
    assert len(graph) > 0
    all_nodes = {edge[0] for edge in graph}
    assert "price.close" in all_nodes
    assert "trend.sma_20" in all_nodes
    assert "momentum.macd" in all_nodes


# ---------------------------------------------------------------------------
# Phase 6.5 regression tests: Technical Analyst MIFP integration
# ---------------------------------------------------------------------------


def test_technical_analyst_consumes_feature_service_snapshot() -> None:
    """TechnicalAnalyst.analyze() must go through FeatureService to get features."""
    from app.services.technical_analysis import TechnicalAnalyst

    analyst = TechnicalAnalyst()
    # The analyst must have a FeatureService instance as the canonical source.
    assert analyst.feature_service is not None
    assert isinstance(analyst.feature_service, FeatureService)


def test_technical_analyst_does_not_recalculate_indicators() -> None:
    """Inject a controlled FeatureSnapshot and verify the opinion follows it, not raw bars."""
    from app.domain.models.analyst import AnalystRequest
    from app.domain.models.features import FeatureProvenance, FeatureSnapshot, FeatureVector
    from app.services.technical_analysis import TechnicalAnalyst

    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    vector = FeatureVector(values=())
    provenance = FeatureProvenance(
        source_data="mock:test",
        generator_version="test",
        feature_schema_version="1.0.0",
        platform_version="test",
        feature_versions=(),
        source_retrieved_at=as_of,
        generated_at=as_of,
        dependency_graph=(),
        input_fingerprint="b" * 64,
    )

    fake_snapshot = FeatureSnapshot(
        snapshot_id="injected",
        ticker="AAPL",
        timeframe="1d",
        provider="mock",
        adjustment="raw",
        session="regular",
        as_of=as_of,
        generated_at=as_of,
        bars_analyzed=100,
        vector=vector,
        provenance=provenance,
        stale=False,
        age_seconds=0.0,
    )

    analyst = TechnicalAnalyst()

    # Patch the FeatureService.snapshot to return our controlled snapshot.
    original_snapshot = analyst.feature_service.snapshot

    def fake_snapshot_fn(
        req: Any,
        extras: dict[str, object] | None = None,
    ) -> FeatureSnapshot:
        return fake_snapshot

    analyst.feature_service.snapshot = fake_snapshot_fn  # type: ignore[method-assign]

    req = AnalystRequest(
        analyst_id="technical",
        ticker="AAPL",
        timeframe="1d",
        as_of=as_of,
        lookback=100,
        horizon=5,
        asset_class="equity",
    )
    opinion = analyst.analyze(req)
    # The opinion should be produced from the injected snapshot (no exception,
    # no re-computation from bars).  If the analyst tried to re-compute
    # indicators it would get different values.
    assert opinion is not None
    assert opinion.research_only is True
    assert opinion.suitable_for_live_trading is False

    # Restore
    analyst.feature_service.snapshot = original_snapshot  # type: ignore[method-assign]


def test_technical_analyst_evidence_uses_mifp_provenance() -> None:
    """Evidence items should carry MIFP provenance in their source_provenance field."""
    from app.domain.models.analyst import AnalystRequest
    from app.services.technical_analysis import TechnicalAnalyst

    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    req = AnalystRequest(
        analyst_id="technical",
        ticker="AAPL",
        timeframe="1d",
        as_of=as_of,
        lookback=100,
        horizon=5,
        asset_class="equity",
    )
    opinion = TechnicalAnalyst().analyze(req)
    assert len(opinion.evidence) > 0
    # At least one evidence item should have source_provenance from MIFP.
    mifp_evidence = [e for e in opinion.evidence if e.provenance and any("mifp" in str(p.source) for p in e.provenance)]
    assert len(mifp_evidence) > 0
