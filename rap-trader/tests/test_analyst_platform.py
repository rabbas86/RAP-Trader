"""Phase 7.5 analyst-platform architecture and lifecycle regressions."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models.analyst import AnalystRequest
from app.services.analyst.framework import BaseAnalyst
from app.services.analyst.service import MockAnalyst
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
