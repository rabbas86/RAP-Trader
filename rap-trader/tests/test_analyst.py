from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.services.analyst.service as analyst_module
from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisTrace,
    AnalystError,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    ConfidenceScore,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    TraceEdge,
    TraceNode,
)
from app.main import app
from app.services.analyst import (
    AnalystConfig,
    ConfidenceAssessmentService,
    DataFreshnessService,
    EvidenceValidationService,
    InMemoryAnalystOpinionStore,
    JSONFileAnalystOpinionStore,
    MockAnalyst,
    OpinionAggregationService,
)

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def request() -> AnalystRequest:
    return AnalystRequest(
        analyst_id="mock", ticker="aapl", timeframe="1d", as_of=NOW, lookback=5, horizon=2, asset_class="equity", extra_context={}
    )


def opinion(direction: AnalysisDirection = AnalysisDirection.BULLISH) -> AnalystOpinion:
    return MockAnalyst(AnalystConfig(mock_direction=direction)).analyze(request())


def evidence(**changes: object) -> EvidenceItem:
    data: dict[str, object] = {
        "evidence_id": "e1",
        "evidence_type": EvidenceType.NEWS,
        "observed_at": NOW - timedelta(hours=1),
        "available_at": NOW - timedelta(minutes=30),
        "evaluated_at": NOW,
        "valid_until": NOW + timedelta(hours=1),
        "strength": EvidenceStrength.MODERATE,
        "summary": "news",
        "confidence": 0.5,
    }
    data.update(changes)
    return EvidenceItem(**data)  # type: ignore[arg-type]


def test_ids_enums_and_ticker_normalization() -> None:
    assert request().ticker == "AAPL"
    assert EvidenceType.REGULATORY_FILING.value == "regulatory_filing"
    with pytest.raises(ValidationError):
        AnalystRequest(
            analyst_id="bad id", ticker="A", timeframe="1d", as_of=NOW, lookback=1, horizon=1, asset_class="equity", extra_context={}
        )


def test_naive_timestamp_rejected_and_offset_normalized() -> None:
    with pytest.raises(ValidationError):
        evidence(observed_at=NOW.replace(tzinfo=None))
    assert evidence(observed_at=datetime.fromisoformat("2026-01-01T15:00:00+03:00")).observed_at.tzinfo == UTC


def test_evidence_chronology_and_lookahead_rejected() -> None:
    with pytest.raises(ValidationError):
        evidence(available_at=NOW + timedelta(seconds=1))
    with pytest.raises(ValidationError):
        evidence(valid_until=NOW - timedelta(days=2))


def test_validation_rejects_stale_and_duplicate_evidence() -> None:
    service = EvidenceValidationService()
    with pytest.raises(AnalystError):
        service.validate([evidence(), evidence()], NOW)
    old = evidence(evidence_id="old", observed_at=NOW - timedelta(days=3), available_at=NOW - timedelta(days=3), evaluated_at=NOW)
    with pytest.raises(AnalystError):
        service.validate([old], NOW)


def test_confidence_bounds_and_uncalibrated_cap() -> None:
    with pytest.raises(ValidationError):
        ConfidenceScore(value=1.1, capped=False, has_historical_calibration=False)
    score = ConfidenceAssessmentService(0.6).assess(0.9)
    assert score.value == 0.6 and score.capped and not score.has_historical_calibration


def test_conflict_and_staleness_reduce_confidence() -> None:
    service = ConfidenceAssessmentService(1.0)
    assert service.assess(0.8, stale_fraction=0.5, conflict_fraction=0.5).value < service.assess(0.8).value


def test_freshness_thresholds() -> None:
    service = DataFreshnessService()
    assert service.threshold(EvidenceType.NEWS) == timedelta(days=1)
    assert service.threshold(EvidenceType.MACROECONOMIC) == timedelta(days=7)


@pytest.mark.parametrize("direction", list(AnalysisDirection))
def test_mock_scenarios_are_deterministic_and_safe(direction: AnalysisDirection) -> None:
    first, second = opinion(direction), opinion(direction)
    assert first == second
    assert first.direction is direction
    assert not first.decision_ready and not first.suitable_for_live_trading and first.research_only
    assert bool(first.evidence) is (direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE)


def test_opinion_duplicate_evidence_rejected() -> None:
    original = opinion()
    with pytest.raises(ValidationError):
        AnalystOpinion(**{**original.model_dump(), "evidence": original.evidence * 2})


def node(node_id: str, uri: str | None = None) -> TraceNode:
    return TraceNode(node_id=node_id, node_type="evidence", uri=uri, created_at=NOW, metadata={})


def test_trace_valid_dag_and_missing_reference() -> None:
    assert AnalysisTrace(
        trace_id="t",
        nodes=[node("a"), node("b")],
        edges=[TraceEdge(source_node_id="a", target_node_id="b", edge_type="uses")],
        created_at=NOW,
    )
    with pytest.raises(ValidationError):
        AnalysisTrace(
            trace_id="t", nodes=[node("a")], edges=[TraceEdge(source_node_id="a", target_node_id="x", edge_type="uses")], created_at=NOW
        )


def test_trace_rejects_cycle_paths_and_secrets() -> None:
    with pytest.raises(ValidationError):
        AnalysisTrace(
            trace_id="t",
            nodes=[node("a"), node("b")],
            edges=[
                TraceEdge(source_node_id="a", target_node_id="b", edge_type="x"),
                TraceEdge(source_node_id="b", target_node_id="a", edge_type="x"),
            ],
            created_at=NOW,
        )
    for uri in [r"C:\\private\\data.json", "/private/data.json", "https://x.test/?api_key=secret"]:
        with pytest.raises(ValidationError):
            node("a", uri)


def test_aggregation_is_descriptive_only() -> None:
    result = OpinionAggregationService().aggregate(
        [opinion(AnalysisDirection.BULLISH), opinion(AnalysisDirection.BEARISH)], [AnalystRole.NEWS]
    )
    assert result["disagreement"] and result["decision_ready"] is False and result["suitable_for_live_trading"] is False
    assert result["missing_analyst_roles"] == ["NEWS"]


def test_memory_and_json_stores(tmp_path: object) -> None:
    value = opinion()
    memory = InMemoryAnalystOpinionStore(max_size=1)
    memory.put(value)
    assert memory.get(value.opinion_id) == value
    file_store = JSONFileAnalystOpinionStore(tmp_path)  # type: ignore[arg-type]
    file_store.put(value)
    assert file_store.get(value.opinion_id) == value


def test_api_endpoints_and_no_decision_route() -> None:
    client = TestClient(app)
    assert client.get("/analysts").status_code == 200
    assert client.get("/analysts/mock/health").status_code == 200
    assert client.get("/analysts/mock/metadata").status_code == 200
    response = client.post("/analysts/mock/analyze", json=request().model_dump(mode="json"))
    assert response.status_code == 200
    oid = response.json()["opinion_id"]
    assert client.get(f"/analysts/opinions/{oid}").status_code == 200
    assert client.post("/analysts/opinions/aggregate", json=[response.json()]).status_code == 200
    assert client.post("/analyst/decision").status_code == 404


def test_api_does_not_expose_internal_details() -> None:
    response = TestClient(app).get("/analysts/missing/health")
    assert "internal" not in response.text.lower()


def test_no_forbidden_runtime_dependencies() -> None:
    source = inspect.getsource(analyst_module).lower()
    for forbidden in [
        "riskengine",
        "risk_engine",
        "portfoliomanager",
        "paperbroker",
        "executionservice",
        "httpx",
        "requests",
        "torch",
        "transformer",
        "committee",
        "chairman",
    ]:
        assert forbidden not in source
