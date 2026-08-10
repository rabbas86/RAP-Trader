"""Offline analyst interfaces and opinion infrastructure (no decision routing)."""

from __future__ import annotations

import json
import os
import threading
from abc import ABC, abstractmethod
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisWarning,
    AnalystError,
    AnalystErrorCodes,
    AnalystMetadata,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    Assumption,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    OpinionSummary,
    ProvenanceRecord,
)
from app.services.analyst import framework as _framework
from app.services.analyst.framework import BaseAnalyst

ConfidenceAssessmentService = _framework.ConfidenceAssessmentService
DataFreshnessService = _framework.DataFreshnessService
EvidenceValidationService = _framework.EvidenceValidationService

SCHEMA_VERSION = "1.0"


def _weights() -> dict[EvidenceStrength, float]:
    return {EvidenceStrength.STRONG: 0.25, EvidenceStrength.MODERATE: 0.25, EvidenceStrength.WEAK: 0.25, EvidenceStrength.SPECULATIVE: 0.25}


@dataclass(frozen=True)
class AnalystConfig:
    mock_direction: AnalysisDirection = AnalysisDirection.NEUTRAL
    analyst_id: str = "mock"
    role: AnalystRole = AnalystRole.OTHER
    flat_threshold: float = 0.05
    evidence_weights: dict[EvidenceStrength, float] = field(default_factory=_weights)
    stale_input_allowed: bool = False
    uncalibrated_confidence_cap: float = 0.65
    base_confidence: float = 0.6
    include_technical_bars: bool = True


class Analyst(BaseAnalyst, ABC):
    @abstractmethod
    def analyze(self, request: AnalystRequest) -> AnalystOpinion: ...
    @abstractmethod
    def supported_timeframes(self) -> list[str]: ...
    @abstractmethod
    def supported_asset_classes(self) -> list[str]: ...


class MockAnalyst(Analyst):
    display_name = "Mock Analyst"
    description = "Deterministic research-only framework analyst"
    health_detail = "deterministic offline mock"

    def __init__(self, config: AnalystConfig | None = None) -> None:
        self.config = config or AnalystConfig()
        self._initialize_framework(self.config.uncalibrated_confidence_cap)

    def supported_timeframes(self) -> list[str]:
        return ["1m", "5m", "15m", "1h", "1d", "1w"]

    def supported_asset_classes(self) -> list[str]:
        return ["equity"]

    def _evidence(self, request: AnalystRequest) -> list[EvidenceItem]:
        if self.config.mock_direction is AnalysisDirection.INSUFFICIENT_EVIDENCE:
            return []
        observed = request.as_of - min(self.freshness.threshold(EvidenceType.TECHNICAL_INDICATOR) / 2, timedelta(hours=request.lookback))
        identity = (
            f"{request.analyst_id}|{request.ticker}|{request.timeframe}|{request.as_of.isoformat()}|{request.lookback}|{request.horizon}"
        )
        bars = []
        if self.config.include_technical_bars:
            orientation = {AnalysisDirection.BULLISH: 1, AnalysisDirection.BEARISH: -1}.get(self.config.mock_direction, 0)
            bars = [
                {
                    "timestamp": (observed + timedelta(minutes=i)).isoformat(),
                    "open": 100 + orientation * i,
                    "high": 101 + orientation * i,
                    "low": 99 + orientation * i,
                    "close": 100.5 + orientation * i,
                    "volume": 1000 + i,
                }
                for i in range(min(request.lookback, 5))
            ]
        summary = f"Deterministic mock {self.config.mock_direction.value.lower()} technical evidence"
        if bars:
            summary += "; OHLCV=" + json.dumps(bars, separators=(",", ":"))
        evidence_id = str(uuid5(NAMESPACE_URL, "evidence|" + identity))
        return [
            EvidenceItem(
                evidence_id=evidence_id,
                evidence_type=EvidenceType.TECHNICAL_INDICATOR,
                observed_at=observed,
                available_at=observed,
                evaluated_at=request.as_of,
                valid_until=request.as_of + timedelta(hours=12),
                strength=EvidenceStrength.MODERATE,
                summary=summary,
                confidence=self.config.base_confidence,
                capped=False,
                calibration_status="uncalibrated mock",
                has_historical_calibration=False,
                source_analyst=self.config.analyst_id,
                assumptions=[Assumption(description="Synthetic offline evidence")],
                warnings=[AnalysisWarning(code="MOCK_DATA", message="Evidence is synthetic")],
                limitations=[AnalysisLimitation(code="NO_SPECIALIST_INTELLIGENCE", message="Phase 5 defines contracts only")],
                provenance=[ProvenanceRecord(source="mock", retrieved_at=request.as_of, uri=None)],
            )
        ]

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        evidence = self._evidence(request)
        self.validator.validate(evidence, request.as_of, allow_stale=self.config.stale_input_allowed)
        confidence = self.confidence.assess(self.config.base_confidence if evidence else 0.0)
        freshness = (
            self.freshness.assess(evidence[0].observed_at, evidence[0].available_at, request.as_of, evidence[0].evidence_type)
            if evidence
            else self.freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER)
        )
        material = f"{request.model_dump_json()}|{self.config.mock_direction.value}"
        opinion = AnalystOpinion(
            opinion_id=str(uuid5(NAMESPACE_URL, material)),
            analyst_id=request.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=self.config.mock_direction,
            confidence=confidence,
            evidence=evidence,
            assumptions=[Assumption(description="Deterministic mock scenario")],
            warnings=[AnalysisWarning(code="RESEARCH_ONLY", message="Not a trading decision")],
            limitations=[AnalysisLimitation(code="MOCK", message="No specialist intelligence")],
            generated_at=request.as_of,
            model_identity=None,
            data_freshness=freshness,
        )
        return self._record_trace(opinion, request, "mock")


class OpinionAggregationService:
    def aggregate(self, opinions: list[AnalystOpinion], required_roles: list[AnalystRole] | None = None) -> dict[str, Any]:
        counts = Counter(item.direction.value for item in opinions)
        orientation = {
            AnalysisDirection.BULLISH: 1.0,
            AnalysisDirection.BEARISH: -1.0,
            AnalysisDirection.NEUTRAL: 0.0,
            AnalysisDirection.MIXED: 0.0,
            AnalysisDirection.INSUFFICIENT_EVIDENCE: 0.0,
        }
        total = sum(item.confidence.value for item in opinions)
        weighted = sum(orientation[item.direction] * item.confidence.value for item in opinions) / total if total else 0.0
        evidence_sets = [{item.evidence_id for item in opinion.evidence} for opinion in opinions]
        overlap = len(set.intersection(*evidence_sets)) if evidence_sets and all(evidence_sets) else 0
        present = {item.analyst_role for item in opinions}
        missing = sorted(role.value for role in (required_roles or []) if role not in present)
        majority = counts.most_common(1)[0][0] if counts else None
        return {
            "opinions": [
                OpinionSummary(
                    opinion_id=o.opinion_id,
                    analyst_id=o.analyst_id,
                    direction=o.direction,
                    confidence=o.confidence.value,
                    evidence_count=len(o.evidence),
                    data_freshness=o.data_freshness,
                ).model_dump(mode="json")
                for o in opinions
            ],
            "direction_counts": dict(counts),
            "agreement": max(counts.values(), default=0) / len(opinions) if opinions else 0.0,
            "disagreement": len(counts) > 1,
            "confidence_weighted_orientation": round(weighted, 6),
            "evidence_overlap_count": overlap,
            "freshness_summary": {"stale_count": sum(o.data_freshness.is_stale for o in opinions), "total": len(opinions)},
            "missing_analyst_roles": missing,
            "minority_views": [o.opinion_id for o in opinions if o.direction.value != majority],
            "decision_ready": False,
            "suitable_for_live_trading": False,
            "research_only": True,
        }


class OpinionStore(ABC):
    @abstractmethod
    def put(self, opinion: AnalystOpinion) -> None: ...
    @abstractmethod
    def get(self, opinion_id: str) -> AnalystOpinion | None: ...


class InMemoryAnalystOpinionStore(OpinionStore):
    def __init__(self, max_size: int = 256, ttl: timedelta = timedelta(hours=1)) -> None:
        self.max_size, self.ttl = max_size, ttl
        self._items: OrderedDict[str, tuple[datetime, AnalystOpinion]] = OrderedDict()
        self._lock = threading.RLock()

    def put(self, opinion: AnalystOpinion) -> None:
        with self._lock:
            self._items[opinion.opinion_id] = (datetime.now(UTC), opinion)
            self._items.move_to_end(opinion.opinion_id)
            while len(self._items) > self.max_size:
                self._items.popitem(last=False)

    def get(self, opinion_id: str) -> AnalystOpinion | None:
        with self._lock:
            item = self._items.get(opinion_id)
            if item is None:
                return None
            if datetime.now(UTC) - item[0] > self.ttl:
                del self._items[opinion_id]
                return None
            self._items.move_to_end(opinion_id)
            return item[1]


class JSONFileAnalystOpinionStore(OpinionStore):
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, opinion_id: str) -> Path:
        safe = str(uuid5(NAMESPACE_URL, opinion_id))
        return self.directory / f"{safe}.json"

    def put(self, opinion: AnalystOpinion) -> None:
        payload = {"schema_version": SCHEMA_VERSION, "opinion": opinion.model_dump(mode="json")}
        with NamedTemporaryFile("w", encoding="utf-8", dir=self.directory, delete=False, suffix=".tmp") as handle:
            json.dump(payload, handle, sort_keys=True)
            temporary = Path(handle.name)
        os.replace(temporary, self._path(opinion.opinion_id))

    def get(self, opinion_id: str) -> AnalystOpinion | None:
        path = self._path(opinion_id)
        if not path.exists():
            return None
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Unsupported opinion schema version")
        return AnalystOpinion.model_validate_json(json.dumps(payload["opinion"]))


class AnalystService:
    """Registry facade for analysts; it stores opinions but never makes decisions."""

    def __init__(self, analysts: list[Analyst] | None = None, store: OpinionStore | None = None) -> None:
        if analysts is None:
            from app.services.fundamental_analysis.service import FundamentalAnalyst
            from app.services.technical_analysis.service import TechnicalAnalyst

            values = [MockAnalyst(), TechnicalAnalyst(), FundamentalAnalyst()]
        else:
            values = analysts
        self.analysts = {item.metadata().analyst_id: item for item in values}
        self.store = store or InMemoryAnalystOpinionStore()

    def list(self) -> list[AnalystMetadata]:
        return [item.metadata() for item in self.analysts.values()]

    def analyst(self, analyst_id: str) -> Analyst:
        try:
            return self.analysts[analyst_id]
        except KeyError as exc:
            raise AnalystError(AnalystErrorCodes.UNSUPPORTED_ANALYST, "Analyst is not available") from exc

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        opinion = self.analyst(request.analyst_id).analyze(request)
        self.store.put(opinion)
        return opinion


EvidenceCollector = EvidenceValidationService
