"""Canonical provenance and input fingerprint construction."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.domain.models.features import FeatureMetadata, FeatureProvenance
from app.domain.models.market_data import HistoricalBarsResult


def input_fingerprint(result: HistoricalBarsResult, configuration: tuple[tuple[str, str], ...]) -> str:
    payload = {
        "symbol": str(result.symbol),
        "timeframe": result.timeframe,
        "provider": result.provider,
        "adjustment": result.adjustment,
        "session": result.session,
        "bars": [bar.model_dump(mode="json") for bar in result.bars],
        "configuration": sorted(configuration),
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode()).hexdigest()


def build_provenance(
    result: HistoricalBarsResult,
    metadata: tuple[FeatureMetadata, ...],
    dependency_graph: tuple[tuple[str, tuple[str, ...]], ...],
    configuration: tuple[tuple[str, str], ...],
    generated_at: datetime,
) -> FeatureProvenance:
    versions = tuple(sorted((str(item.feature_id), item.version) for item in metadata))
    return FeatureProvenance(
        source_data=f"{result.provider}:{result.symbol}:{result.timeframe}:{result.actual_start.isoformat()}:{result.actual_end.isoformat()}",
        generator_version="mifp-6.5.0",
        feature_versions=versions,
        source_retrieved_at=result.retrieved_at,
        generated_at=generated_at,
        dependency_graph=dependency_graph,
        input_fingerprint=input_fingerprint(result, configuration),
    )
