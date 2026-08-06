"""Phase 6.5 Market Intelligence Feature Platform contract tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from app.services.features.cache import FeatureCacheKey, FeatureSnapshotCache, configuration_hash
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
    key = FeatureCacheKey("AAPL", "1d", "mock", "raw", "regular", AS_OF, configuration_hash(()))
    cache.set(key, snapshot)
    assert cache.get(key) is snapshot
    assert cache.get(FeatureCacheKey("AAPL", "1d", "mock", "raw", "all", AS_OF, configuration_hash(()))) is None


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
