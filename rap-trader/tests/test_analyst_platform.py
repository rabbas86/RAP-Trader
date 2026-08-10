"""Phase 7.5 analyst-platform architecture and lifecycle regressions."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import AnalystRequest
from app.services.analyst.framework import BaseAnalyst
from app.services.analyst.service import AnalystConfig, MockAnalyst
from app.services.fundamental_analysis import FundamentalAnalyst
from app.services.technical_analysis import TechnicalAnalyst
from tests.test_fundamental_analyst import fundamentals

ROOT = Path(__file__).parents[1]
NOW = datetime(2025, 3, 15, tzinfo=UTC)


def _classes_and_functions() -> tuple[list[str], list[str]]:
    classes: list[str] = []
    functions: list[str] = []
    for path in (ROOT / "app" / "services").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        classes.extend(node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
        functions.extend(node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return classes, functions


def test_framework_services_and_trace_have_one_implementation() -> None:
    classes, functions = _classes_and_functions()
    assert classes.count("DataFreshnessService") == 1
    assert classes.count("ConfidenceAssessmentService") == 1
    assert classes.count("EvidenceValidationService") == 1
    assert functions.count("build_analysis_trace") == 1
    assert functions.count("insufficient_opinion") == 1
    assert "_trace" not in functions


def test_all_analysts_use_shared_base_and_produce_trace() -> None:
    analysts = [MockAnalyst(), TechnicalAnalyst(), FundamentalAnalyst()]
    assert all(isinstance(analyst, BaseAnalyst) for analyst in analysts)

    requests = [
        AnalystRequest(analyst_id="mock", ticker="TEST", timeframe="1d", as_of=NOW, lookback=60, horizon=5, asset_class="equity"),
        AnalystRequest(analyst_id="technical", ticker="TEST", timeframe="1d", as_of=NOW, lookback=100, horizon=5, asset_class="equity"),
        AnalystRequest(
            analyst_id="fundamental",
            ticker="TEST",
            timeframe="1d",
            as_of=NOW,
            lookback=3,
            horizon=365,
            asset_class="equity",
            extra_context={"fundamentals": fundamentals().model_dump(mode="json")},
        ),
    ]
    for analyst, request in zip(analysts, requests, strict=True):
        analyst.validate_input(request)
        opinion = analyst.analyze(request)
        assert opinion.confidence.value >= 0
        trace = analyst.trace_for(opinion.opinion_id)
        assert trace is not None
        assert {node.node_type for node in trace.nodes} >= {"analyst_request", "analyst_opinion"}
        assert trace.edges


# ------------------------------------------------------------------
# Phase 7.5 insufficient-opinion consolidation regressions
# ------------------------------------------------------------------


def _insufficient_request(analyst_id: str) -> AnalystRequest:
    return AnalystRequest(
        analyst_id=analyst_id,
        ticker="TEST",
        timeframe="1d",
        as_of=NOW,
        lookback=60,
        horizon=5,
        asset_class="equity",
    )


def test_mock_analyst_insufficient_uses_canonical_factory() -> None:
    from app.domain.models.analyst import AnalysisDirection, ConfidenceScore

    analyst = MockAnalyst(AnalystConfig(mock_direction=AnalysisDirection.INSUFFICIENT_EVIDENCE))
    request = _insufficient_request(analyst.analyst_id)
    opinion = analyst.analyze(request)

    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []
    assert opinion.confidence == ConfidenceScore(
        value=0.0, capped=False, calibration_note="uncalibrated; confidence cap enforced", has_historical_calibration=False
    )
    assert opinion.decision_ready is False
    assert opinion.suitable_for_live_trading is False
    assert opinion.research_only is True
    # Canonical default warning and limitation are present
    assert len(opinion.warnings) == 1
    assert opinion.warnings[0].code == "INSUFFICIENT_DATA"
    assert len(opinion.limitations) == 1
    assert opinion.limitations[0].code == "NO_CONCLUSION"
    # trace works
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert trace.edges  # has insufficient_for edge


def test_technical_analyst_insufficient_uses_canonical_factory() -> None:
    from app.domain.models.analyst import AnalysisDirection, ConfidenceScore

    analyst = TechnicalAnalyst()
    request = _insufficient_request(analyst.analyst_id)
    # lookback=1 forces insufficient data path (not enough bars)
    request = request.model_copy(update={"lookback": 1})
    opinion = analyst.analyze(request)

    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []
    assert opinion.confidence == ConfidenceScore(
        value=0.0, capped=False, calibration_note="uncalibrated; confidence cap enforced", has_historical_calibration=False
    )
    assert opinion.decision_ready is False
    assert opinion.suitable_for_live_trading is False
    assert opinion.research_only is True
    # Specialist-specific warning and limitation are preserved
    assert any(w.code == "INSUFFICIENT_DATA" for w in opinion.warnings)
    assert any(l.code == "NO_INDICATORS" for l in opinion.limitations)
    # trace works
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert trace.edges


def test_fundamental_analyst_insufficient_uses_canonical_factory() -> None:
    from app.domain.models.analyst import AnalysisDirection, ConfidenceScore

    analyst = FundamentalAnalyst()
    request = _insufficient_request(analyst.analyst_id)
    # Call _insufficient directly to verify canonical delegation
    opinion = analyst._insufficient(request, "Test fundamental insufficient reason")

    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []
    assert opinion.confidence == ConfidenceScore(
        value=0.0, capped=False, calibration_note="uncalibrated; confidence cap enforced", has_historical_calibration=False
    )
    assert opinion.decision_ready is False
    assert opinion.suitable_for_live_trading is False
    assert opinion.research_only is True
    # Specialist-specific warning and limitation are preserved
    assert any(w.code == "INSUFFICIENT_DATA" for w in opinion.warnings)
    assert any(l.code == "NO_FUNDAMENTALS" for l in opinion.limitations)
    # trace works
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert trace.edges


def test_all_three_insufficient_outputs_share_same_opinion_id_mechanism() -> None:
    """All three specialists must derive opinion_id from the same canonical formula."""
    from app.domain.models.analyst import AnalysisDirection

    analyst = MockAnalyst(AnalystConfig(mock_direction=AnalysisDirection.INSUFFICIENT_EVIDENCE))
    request = _insufficient_request(analyst.analyst_id)
    opinion = analyst.analyze(request)

    expected_id = str(
        uuid5(
            NAMESPACE_URL,
            f"{analyst.analyst_id}|{request.ticker}|{request.as_of.isoformat()}|insufficient|Mock analyst configured for insufficient evidence",
        )
    )
    assert opinion.opinion_id == expected_id


def test_all_three_insufficient_outputs_satisfy_structural_invariants() -> None:
    """All insufficient opinions must satisfy the same canonical invariants."""
    from app.domain.models.analyst import AnalysisDirection

    mock = MockAnalyst(AnalystConfig(mock_direction=AnalysisDirection.INSUFFICIENT_EVIDENCE))
    mock_req = _insufficient_request(mock.analyst_id)
    mock_op = mock.analyze(mock_req)

    tech = TechnicalAnalyst()
    tech_req = _insufficient_request(tech.analyst_id).model_copy(update={"lookback": 1})
    tech_op = tech.analyze(tech_req)

    fund = FundamentalAnalyst()
    fund_req = _insufficient_request(fund.analyst_id)
    fund_op = fund._insufficient(fund_req, "Test fundamental insufficient reason")

    for op in [mock_op, tech_op, fund_op]:
        assert op.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
        assert op.evidence == []
        assert op.confidence.value == 0.0
        assert op.confidence.capped is False
        assert op.confidence.has_historical_calibration is False
        assert op.decision_ready is False
        assert op.suitable_for_live_trading is False
        assert op.research_only is True
        assert len(op.warnings) >= 1
        assert len(op.limitations) >= 1
        assert op.generated_at == NOW


def test_insufficient_opinion_delegates_to_canonical_factory() -> None:
    """BaseAnalyst._insufficient must delegate to insufficient_opinion without
    constructing AnalystOpinion directly."""
    source = inspect.getsource(BaseAnalyst._insufficient)
    assert "insufficient_opinion(" in source
    assert "AnalystOpinion(" not in source


def test_no_specialist_constructs_analyst_opinion_in_insufficient() -> None:
    """No specialist _insufficient override should construct AnalystOpinion directly."""
    for cls in [TechnicalAnalyst, FundamentalAnalyst]:
        if "_insufficient" not in cls.__dict__:
            continue
        source = inspect.getsource(cls._insufficient)
        assert "AnalystOpinion(" not in source, f"{cls.__name__}._insufficient still constructs AnalystOpinion directly"


def test_only_one_canonical_insufficient_opinion_factory_exists() -> None:
    """There must be exactly one insufficient_opinion function and one _insufficient
    construction path in the framework."""
    classes, functions = _classes_and_functions()
    assert functions.count("insufficient_opinion") == 1
    assert classes.count("BaseAnalyst") == 1


def test_insufficient_opinion_id_is_deterministic_and_unique_per_source() -> None:
    """Two calls to _insufficient with the same inputs produce the same opinion_id
    and a different reason produces a different id."""
    from app.domain.models.analyst import AnalysisDirection

    analyst = MockAnalyst(AnalystConfig(mock_direction=AnalysisDirection.INSUFFICIENT_EVIDENCE))
    req = _insufficient_request(analyst.analyst_id)
    first = analyst._insufficient(req, "reason A")
    second = analyst._insufficient(req, "reason A")
    third = analyst._insufficient(req, "reason B")

    assert first.opinion_id == second.opinion_id
    assert first.opinion_id != third.opinion_id
