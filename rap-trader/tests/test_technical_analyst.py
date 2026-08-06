from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.cli.analyst import main as analyst_main
from app.domain.models.analyst import AnalysisDirection, AnalystRequest, EvidenceStrength
from app.domain.models.market_data import OHLCVBar
from app.domain.models.technical import SwingPoint
from app.main import app
from app.services.technical_analysis import TechnicalAnalyst, classify_structure, clustered_levels, confirmed_swings


def request(*, lookback: int = 60) -> AnalystRequest:
    return AnalystRequest(
        analyst_id="technical",
        ticker="AAPL",
        timeframe="1d",
        as_of=datetime(2025, 1, 1, tzinfo=UTC),
        lookback=lookback,
        horizon=5,
        asset_class="equity",
    )


def test_indicator_computations() -> None:
    values = [float(value) for value in range(1, 16)]
    assert TechnicalAnalyst.sma(values, 5) == 13.0
    assert TechnicalAnalyst.ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)
    assert TechnicalAnalyst.rsi(values, 14) == 100.0

    macd, signal, histogram = TechnicalAnalyst.macd([float(value) for value in range(1, 41)], 3, 6, 2)
    assert macd == pytest.approx(1.5)
    assert signal == pytest.approx(1.5)
    assert histogram == pytest.approx(0.0)


def bars(prices: list[float]) -> list[OHLCVBar]:
    return [
        OHLCVBar(
            timestamp=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
            open=price,
            high=price + 1,
            low=max(0.01, price - 1),
            close=price,
            volume=100 + index * 10,
        )
        for index, price in enumerate(prices)
    ]


def test_extended_price_indicators() -> None:
    values = [float(value) for value in range(1, 31)]
    assert TechnicalAnalyst.moving_average_slope(values, 5) > 0
    assert TechnicalAnalyst.crossover(values, 5, 10)[0] == "above"
    assert TechnicalAnalyst.roc(values, 10) == pytest.approx(50.0)
    lower, middle, upper = TechnicalAnalyst.bollinger_bands(values, 20)
    assert lower < middle < upper
    assert TechnicalAnalyst.bollinger_bandwidth(lower, middle, upper) > 0


def test_range_volume_and_vwap_indicators() -> None:
    sample = bars([10, 11, 10, 12, 13])
    assert TechnicalAnalyst.true_ranges(sample) == pytest.approx([2, 2, 2, 3, 2])
    assert TechnicalAnalyst.atr(sample, 3) > 0
    assert TechnicalAnalyst.obv(sample) == 260
    assert TechnicalAnalyst.rolling_volume_average(sample, 2) == 135
    assert TechnicalAnalyst.relative_volume(sample, 2) == pytest.approx(140 / 135)
    assert 9 < TechnicalAnalyst.vwap(sample) < 14


def test_confirmed_swings_have_two_bar_delay() -> None:
    sample = bars([10, 11, 15, 11, 10, 9, 5, 9, 10])
    points = confirmed_swings(sample)
    assert [(point.type, point.bar_index) for point in points] == [("high", 2), ("low", 6)]
    assert all(point.confirmed_at == sample[point.bar_index + 2].timestamp for point in points)


def test_no_lookahead_swing_is_absent_until_confirmed() -> None:
    sample = bars([10, 11, 15, 11, 10])
    assert confirmed_swings(sample[:-1]) == []
    assert confirmed_swings(sample)[0].bar_index == 2


def test_structure_counts_and_is_causal() -> None:
    sample = bars([10, 12, 15, 12, 11, 13, 17, 14, 13, 15, 19, 16, 15])
    points = confirmed_swings(sample)
    state = classify_structure(sample, points)
    assert state.higher_highs >= 1
    assert state.higher_lows >= 1
    assert state.regime == "uptrend"
    assert state.last_confirmed_timestamp <= sample[-1].timestamp


def test_level_clustering_touch_strength_and_limit() -> None:
    stamp = datetime(2024, 1, 1, tzinfo=UTC)
    points = [
        SwingPoint(timestamp=stamp, price=price, type="low", confirmed_at=stamp, strength=EvidenceStrength.MODERATE, bar_index=index)
        for index, price in enumerate([99.5, 100.0, 100.4, 110.0])
    ]
    levels = clustered_levels(points, 105, tolerance=0.01, limit=1)
    assert len(levels) == 1
    assert levels[0].touch_count == 3
    assert levels[0].strength.value == "STRONG"
    assert levels[0].level_type == "support"


def test_snapshot_contains_every_indicator_family() -> None:
    analyst = TechnicalAnalyst()
    snapshot = analyst.snapshot(request())
    names = {item.name for item in snapshot.indicator_values}
    assert {
        "sma_slope",
        "crossover_age",
        "roc",
        "true_range",
        "atr",
        "bollinger_bandwidth",
        "obv",
        "volume_average",
        "relative_volume",
        "vwap",
    } <= names
    assert snapshot.bars_analyzed == 60


def test_evidence_has_all_required_categories() -> None:
    opinion = TechnicalAnalyst().analyze(request())
    assert {item.summary.split(":", 1)[0] for item in opinion.evidence} == {
        "trend",
        "momentum",
        "volatility",
        "volume",
        "structure",
        "levels",
    }


def test_optional_external_evidence_and_calibration() -> None:
    enriched = request().model_copy(
        update={"extra_context": {"kronos_forecast": {"direction": "UP"}, "backtest_result": {"directional_accuracy": 0.8}}}
    )
    opinion = TechnicalAnalyst().analyze(enriched)
    types = {item.evidence_type.value for item in opinion.evidence}
    assert {"forecast", "backtest"} <= types
    assert opinion.confidence.has_historical_calibration is True


def test_trace_is_full_provenance_dag() -> None:
    analyst = TechnicalAnalyst()
    opinion = analyst.analyze(request())
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert {node.node_type for node in trace.nodes} >= {"analyst_request", "market_data", "evidence", "analyst_opinion"}
    assert len(trace.edges) >= len(opinion.evidence) * 2


def test_deterministic_and_research_only() -> None:
    analyst = TechnicalAnalyst()
    first = analyst.analyze(request())
    second = analyst.analyze(request())
    assert first == second
    assert first.opinion_id == second.opinion_id
    assert first.decision_ready is False
    assert first.suitable_for_live_trading is False
    assert first.research_only is True
    assert analyst.trace_for(first.opinion_id) is not None


def test_insufficient_data_is_fail_safe() -> None:
    opinion = TechnicalAnalyst().analyze(request(lookback=10))
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []
    assert opinion.confidence.value == 0.0


def test_source_has_no_forbidden_runtime_dependencies() -> None:
    source = inspect.getsource(inspect.getmodule(TechnicalAnalyst)).lower()
    for dependency in ("risk_engine", "portfolio", "broker", "execution", "committee", "chairman"):
        assert f"import {dependency}" not in source
        assert f"from app.services.{dependency}" not in source


def test_api_lists_and_runs_technical_analyst() -> None:
    client = TestClient(app)
    listed = client.get("/analysts")
    assert listed.status_code == 200
    assert "technical" in {item["analyst_id"] for item in listed.json()}

    response = client.post("/analysts/technical/analyze", json=json.loads(request().model_dump_json()))
    assert response.status_code == 200
    assert response.json()["analyst_id"] == "technical"

    snapshot = client.get(
        "/analysts/technical/snapshot", params={"ticker": "AAPL", "timeframe": "1d", "lookback": 60, "as_of": "2025-01-01T00:00:00Z"}
    )
    assert snapshot.status_code == 200
    assert snapshot.json()["bars_analyzed"] == 60


def test_cli_runs_technical_analyst(capsys: pytest.CaptureFixture[str]) -> None:
    result = analyst_main(
        [
            "--analyst-id",
            "technical",
            "--ticker",
            "AAPL",
            "--as-of",
            "2025-01-01T00:00:00+00:00",
            "--lookback",
            "60",
            "--as-json",
        ]
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out)["analyst_id"] == "technical"


def test_cli_prints_structure_and_level_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        analyst_main(["--analyst", "technical-analyst", "--ticker", "AAPL", "--as-of", "2025-01-01T00:00:00+00:00", "--lookback", "60"])
        == 0
    )
    output = capsys.readouterr().out
    assert "Structure:" in output and "Levels:" in output and "[trend]" in output
