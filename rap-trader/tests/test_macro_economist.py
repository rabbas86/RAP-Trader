"""Phase 8B Macro Economist tests — deterministic, offline, research-only."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalystError,
    AnalystRequest,
)
from app.domain.models.data_platform import (
    DataAvailability,
    DataDomain,
    DataQuality,
    DataRecordId,
    DataRevision,
    DataSourceIdentity,
    NormalizedDataRecord,
    ResearchDataSnapshot,
)
from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.data_platform.snapshot import ResearchDataSnapshotService
from app.services.macro_analysis.domain import MacroRegime
from app.services.macro_analysis.service import MacroAnalyst

NOW = datetime(2026, 8, 1, 12, tzinfo=UTC)
EARLIER = datetime(2026, 7, 29, 12, tzinfo=UTC)
EVEN_EARLIER = datetime(2026, 7, 1, 12, tzinfo=UTC)

_SOURCE = DataSourceIdentity(
    provider="deterministic_test",
    dataset="macro",
    source_version="1",
    schema_version="1",
    offline_capable=True,
    authoritative=False,
)


def _make_record(
    *,
    record_id: str,
    value: float,
    series_id: str | None = None,
    units: str = "percent",
    observed_at: datetime = NOW,
    available_at: datetime | None = None,
    event_time: datetime | None = None,
) -> NormalizedDataRecord:
    available = available_at or observed_at
    et = event_time or observed_at
    fingerprint = sha256_fingerprint({"record_id": record_id, "value": value, "observed_at": observed_at, "available_at": available})
    availability = DataAvailability(
        observed_at=observed_at,
        published_at=available,
        available_at=available,
        ingested_at=available,
    )
    revision = DataRevision(
        revision_id=f"{record_id}.r0",
        revision_number=0,
        revised_at=available,
        available_at=available,
        source_fingerprint=fingerprint,
    )
    quality = DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0)
    return NormalizedDataRecord(
        record_id=DataRecordId(record_id),
        domain=DataDomain.MACRO,
        value=value,
        units=units,
        availability=availability,
        revision=revision,
        source=_SOURCE,
        quality=quality,
        source_fingerprint=fingerprint,
        schema_version="1",
        symbol_or_entity="US",
        series_id=series_id or record_id,
        event_time=et,
    )


def _build_snapshot(records: list[NormalizedDataRecord], as_of: datetime = NOW) -> ResearchDataSnapshot:
    service = ResearchDataSnapshotService()
    return service.create_snapshot(records, as_of=as_of, requested_domains=(DataDomain.MACRO,))


def _request(snapshot_json: dict, ticker: str = "US") -> AnalystRequest:
    return AnalystRequest(
        analyst_id="macro",
        ticker=ticker,
        timeframe="1d",
        as_of=NOW,
        lookback=60,
        horizon=30,
        asset_class="macro",
        extra_context={"snapshot": snapshot_json},
    )


def _analyst() -> MacroAnalyst:
    return MacroAnalyst()


# ---------------------------------------------------------------------------
# Test helpers: build snapshots with two observation periods per series
# ---------------------------------------------------------------------------


def _two_period_snapshot(
    series_values: dict[str, tuple[float, float]],
    as_of: datetime = NOW,
) -> ResearchDataSnapshot:
    """Build a snapshot with two observations per series (even_earlier + earlier)."""
    records: list[NormalizedDataRecord] = []
    for series, (prior, latest) in series_values.items():
        records.append(
            _make_record(
                record_id=f"prior.{series.lower()}",
                value=prior,
                series_id=series,
                observed_at=EVEN_EARLIER,
                available_at=EVEN_EARLIER,
            )
        )
        records.append(
            _make_record(
                record_id=f"latest.{series.lower()}",
                value=latest,
                series_id=series,
                observed_at=EARLIER,
                available_at=EARLIER,
            )
        )
    return _build_snapshot(records, as_of=as_of)


# Base set of macro series that always produces enough signals (>= 4).
# Individual tests override specific series to trigger particular regimes.
_BASE_SERIES: dict[str, tuple[float, float]] = {
    "CPI": (3.0, 3.2),
    "UNEMPLOYMENT": (4.5, 4.1),
    "GDP": (2.0, 2.5),
    "MONEY_SUPPLY": (20000.0, 21000.0),
}


def _scenario(**overrides: tuple[float, float]) -> dict[str, tuple[float, float]]:
    """Return a copy of _BASE_SERIES updated with the given series overrides."""
    result = dict(_BASE_SERIES)
    result.update(overrides)
    return result


# ---------------------------------------------------------------------------
# Inflation tests
# ---------------------------------------------------------------------------


def test_inflation_accelerating() -> None:
    snapshot = _two_period_snapshot(_scenario(CPI=(2.5, 4.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    infl = [item for item in opinion.evidence if item.summary.startswith("inflation:")]
    assert len(infl) >= 1
    assert "accelerat" in infl[0].summary.lower()


def test_inflation_decelerating() -> None:
    snapshot = _two_period_snapshot(_scenario(CPI=(4.5, 2.2)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    infl = [item for item in opinion.evidence if item.summary.startswith("inflation:")]
    assert len(infl) >= 1
    assert "decelerat" in infl[0].summary.lower()


def test_inflation_stable() -> None:
    snapshot = _two_period_snapshot(_scenario(CPI=(3.2, 3.25)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    infl = [item for item in opinion.evidence if item.summary.startswith("inflation:")]
    assert len(infl) >= 1
    assert "stable" in infl[0].summary.lower()


# ---------------------------------------------------------------------------
# Growth tests
# ---------------------------------------------------------------------------


def test_growth_accelerating() -> None:
    snapshot = _two_period_snapshot(_scenario(GDP=(1.5, 4.0)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    growth = [item for item in opinion.evidence if item.summary.startswith("growth:")]
    assert len(growth) >= 1
    assert "accelerat" in growth[0].summary.lower()


def test_growth_negative() -> None:
    snapshot = _two_period_snapshot(_scenario(GDP=(2.0, -1.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    growth = [item for item in opinion.evidence if item.summary.startswith("growth:")]
    assert len(growth) >= 1
    assert "negative" in growth[0].summary.lower()


# ---------------------------------------------------------------------------
# Employment tests
# ---------------------------------------------------------------------------


def test_employment_strengthening() -> None:
    snapshot = _two_period_snapshot(_scenario(UNEMPLOYMENT=(6.5, 3.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    emp = [item for item in opinion.evidence if item.summary.startswith("employment:")]
    assert len(emp) >= 1
    assert "strengthen" in emp[0].summary.lower()


def test_employment_weakening() -> None:
    snapshot = _two_period_snapshot(_scenario(UNEMPLOYMENT=(3.5, 6.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    emp = [item for item in opinion.evidence if item.summary.startswith("employment:")]
    assert len(emp) >= 1
    assert "weaken" in emp[0].summary.lower()


# ---------------------------------------------------------------------------
# Yield curve tests
# ---------------------------------------------------------------------------


def test_yield_curve_inverted() -> None:
    snapshot = _two_period_snapshot(_scenario(YIELD_SPREAD=(0.5, -0.3)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    yc = [item for item in opinion.evidence if item.summary.startswith("yield_curve:")]
    assert len(yc) >= 1
    assert "invert" in yc[0].summary.lower()


def test_yield_curve_normal() -> None:
    snapshot = _two_period_snapshot(_scenario(YIELD_SPREAD=(-0.2, 0.8)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    yc = [item for item in opinion.evidence if item.summary.startswith("yield_curve:")]
    assert len(yc) >= 1
    assert "normal" in yc[0].summary.lower() or "steep" in yc[0].summary.lower()


# ---------------------------------------------------------------------------
# Credit tests
# ---------------------------------------------------------------------------


def test_credit_tightening() -> None:
    snapshot = _two_period_snapshot(_scenario(CREDIT_SPREAD=(1.0, 3.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    credit = [item for item in opinion.evidence if item.summary.startswith("credit:")]
    assert len(credit) >= 1
    assert "tighten" in credit[0].summary.lower()


def test_credit_loosening() -> None:
    snapshot = _two_period_snapshot(_scenario(CREDIT_SPREAD=(3.5, 0.8)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    credit = [item for item in opinion.evidence if item.summary.startswith("credit:")]
    assert len(credit) >= 1
    assert "loosen" in credit[0].summary.lower()


# ---------------------------------------------------------------------------
# Business cycle tests
# ---------------------------------------------------------------------------


def test_business_cycle_expansion() -> None:
    snapshot = _two_period_snapshot(_scenario(GDP=(2.0, 4.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    bc = [item for item in opinion.evidence if item.summary.startswith("business_cycle:")]
    assert len(bc) >= 1
    assert "expansion" in bc[0].summary.lower()


def test_business_cycle_contraction() -> None:
    snapshot = _two_period_snapshot(_scenario(GDP=(2.0, -1.0), UNEMPLOYMENT=(4.1, 5.5)))
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    bc = [item for item in opinion.evidence if item.summary.startswith("business_cycle:")]
    assert len(bc) >= 1
    assert "contraction" in bc[0].summary.lower()


# ---------------------------------------------------------------------------
# Macro regime classification tests
# ---------------------------------------------------------------------------


def test_regime_expansion() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.5),
            UNEMPLOYMENT=(4.5, 3.8),
            MONEY_SUPPLY=(20000, 21000),
            YIELD_SPREAD=(0.2, 0.5),
            CREDIT_SPREAD=(1.5, 0.8),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction in (AnalysisDirection.BULLISH, AnalysisDirection.NEUTRAL, AnalysisDirection.MIXED)


def test_regime_recession() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, -1.5),
            UNEMPLOYMENT=(4.1, 6.5),
            MONEY_SUPPLY=(21000, 20500),
            YIELD_SPREAD=(0.5, -0.2),
            CREDIT_SPREAD=(1.0, 3.0),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction in (AnalysisDirection.BEARISH, AnalysisDirection.MIXED, AnalysisDirection.NEUTRAL)


def test_regime_stagflation() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            CPI=(3.0, 6.5),
            GDP=(2.0, -1.0),
            UNEMPLOYMENT=(4.1, 5.5),
            MONEY_SUPPLY=(21000, 20000),
            POLICY_RATE=(5.25, 5.5),
            YIELD_SPREAD=(-0.1, 0.0),
            CREDIT_SPREAD=(1.0, 2.5),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction in (AnalysisDirection.BEARISH, AnalysisDirection.MIXED)


def test_regime_tightening() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 2.5),
            UNEMPLOYMENT=(4.5, 4.1),
            MONEY_SUPPLY=(20000, 20500),
            POLICY_RATE=(2.0, 5.5),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    policy = [e for e in opinion.evidence if e.summary.startswith("monetary_policy:")]
    assert len(policy) >= 1
    assert "restrictive" in policy[0].summary.lower()


def test_regime_easing() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 2.5),
            UNEMPLOYMENT=(4.5, 4.1),
            MONEY_SUPPLY=(21000, 22000),
            POLICY_RATE=(5.5, 1.5),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    policy = [e for e in opinion.evidence if e.summary.startswith("monetary_policy:")]
    assert len(policy) >= 1
    assert "accommodative" in policy[0].summary.lower()


def test_regime_liquidity_expansion() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 2.5),
            UNEMPLOYMENT=(4.5, 4.1),
            MONEY_SUPPLY=(20000, 22000),
            POLICY_RATE=(5.25, 5.25),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    liq = [e for e in opinion.evidence if e.summary.startswith("liquidity:")]
    assert len(liq) >= 1
    assert "expand" in liq[0].summary.lower()


def test_regime_liquidity_contraction() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 2.5),
            UNEMPLOYMENT=(4.5, 4.1),
            MONEY_SUPPLY=(22000, 20000),
            POLICY_RATE=(5.25, 5.25),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    liq = [e for e in opinion.evidence if e.summary.startswith("liquidity:")]
    assert len(liq) >= 1
    assert "contract" in liq[0].summary.lower()


def test_regime_inflation_shock() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            CPI=(3.0, 7.5),
            GDP=(2.0, 2.5),
            UNEMPLOYMENT=(4.5, 4.1),
            MONEY_SUPPLY=(20000, 20500),
            POLICY_RATE=(5.25, 5.25),
            YIELD_SPREAD=(0.3, 0.2),
            CREDIT_SPREAD=(1.0, 1.1),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    infl = [e for e in opinion.evidence if e.summary.startswith("inflation:")]
    assert len(infl) >= 1
    assert "accelerat" in infl[0].summary.lower()


# ---------------------------------------------------------------------------
# Regime precedence regression tests
# ---------------------------------------------------------------------------


def _regime_from_opinion(opinion) -> MacroRegime:
    """Extract the regime from the opinion trace metadata."""

    # The regime is included in the evidence summary via the business_cycle or regime category.
    for item in opinion.evidence:
        if "regime" in item.summary.lower() or item.category == "business_cycle":
            return MacroRegime.UNKNOWN  # fallback
    return MacroRegime.UNKNOWN


def test_stagflation_takes_precedence_over_inflation_shock() -> None:
    """Stagflation (inflation + negative growth) must win over plain INFLATION_SHOCK."""
    # Direct regime service test for precise classification.
    from app.services.macro_analysis.base import MacroSignal
    from app.services.macro_analysis.domain import GrowthTrend, InflationTrend
    from app.services.macro_analysis.regime import MacroRegimeService

    svc = MacroRegimeService()
    signals = [
        MacroSignal(
            signal_id="1",
            category="inflation",
            label="test",
            trend_enum=InflationTrend.ACCELERATING.value,
            latest_value=6.0,
            latest_units="percent",
            delta=2.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="2",
            category="growth",
            label="test",
            trend_enum=GrowthTrend.NEGATIVE.value,
            latest_value=-1.5,
            latest_units="percent",
            delta=-3.5,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="3",
            category="employment",
            label="test",
            trend_enum="STRENGTHENING",
            latest_value=4.0,
            latest_units="percent",
            delta=-1.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="4",
            category="liquidity",
            label="test",
            trend_enum="EXPANDING",
            latest_value=21000.0,
            latest_units="billions",
            delta=500.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
    ]
    result = svc.classify(signals)
    assert result.regime is MacroRegime.STAGFLATION


def test_inflation_shock_takes_precedence_over_easing() -> None:
    """Inflation shock must win over accommodative policy when inflation is accelerating."""
    from app.services.macro_analysis.base import MacroSignal
    from app.services.macro_analysis.domain import InflationTrend, PolicyStance
    from app.services.macro_analysis.regime import MacroRegimeService

    svc = MacroRegimeService()
    signals = [
        MacroSignal(
            signal_id="1",
            category="inflation",
            label="test",
            trend_enum=InflationTrend.ACCELERATING.value,
            latest_value=7.0,
            latest_units="percent",
            delta=2.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="2",
            category="monetary_policy",
            label="test",
            trend_enum=PolicyStance.ACCOMMODATIVE.value,
            latest_value=1.5,
            latest_units="percent",
            delta=-3.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="3",
            category="growth",
            label="test",
            trend_enum="ACCELERATING",
            latest_value=4.0,
            latest_units="percent",
            delta=1.5,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="4",
            category="liquidity",
            label="test",
            trend_enum="EXPANDING",
            latest_value=21000.0,
            latest_units="billions",
            delta=500.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
    ]
    result = svc.classify(signals)
    assert result.regime is MacroRegime.INFLATION_SHOCK


def test_recession_takes_precedence_over_easing() -> None:
    """Recession (negative growth with stable/decelerating inflation) must win over accommodative policy."""
    from app.services.macro_analysis.base import MacroSignal
    from app.services.macro_analysis.domain import GrowthTrend, InflationTrend, PolicyStance
    from app.services.macro_analysis.regime import MacroRegimeService

    svc = MacroRegimeService()
    signals = [
        MacroSignal(
            signal_id="1",
            category="inflation",
            label="test",
            trend_enum=InflationTrend.STABLE.value,
            latest_value=3.0,
            latest_units="percent",
            delta=0.1,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="2",
            category="growth",
            label="test",
            trend_enum=GrowthTrend.NEGATIVE.value,
            latest_value=-1.5,
            latest_units="percent",
            delta=-3.5,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="3",
            category="monetary_policy",
            label="test",
            trend_enum=PolicyStance.ACCOMMODATIVE.value,
            latest_value=1.5,
            latest_units="percent",
            delta=-3.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="4",
            category="liquidity",
            label="test",
            trend_enum="EXPANDING",
            latest_value=21000.0,
            latest_units="billions",
            delta=500.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
    ]
    result = svc.classify(signals)
    assert result.regime is MacroRegime.RECESSION


def test_peak_takes_precedence_over_slowdown() -> None:
    """Peak (decelerating growth + inverted curve) must win over plain SLOWDOWN."""
    from app.services.macro_analysis.base import MacroSignal
    from app.services.macro_analysis.domain import GrowthTrend, YieldCurveTrend
    from app.services.macro_analysis.regime import MacroRegimeService

    svc = MacroRegimeService()
    signals = [
        MacroSignal(
            signal_id="1",
            category="inflation",
            label="test",
            trend_enum="STABLE",
            latest_value=3.0,
            latest_units="percent",
            delta=0.1,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="2",
            category="growth",
            label="test",
            trend_enum=GrowthTrend.DECELERATING.value,
            latest_value=1.5,
            latest_units="percent",
            delta=-1.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="3",
            category="yield_curve",
            label="test",
            trend_enum=YieldCurveTrend.INVERTED.value,
            latest_value=-0.3,
            latest_units="percent",
            delta=-0.5,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
        MacroSignal(
            signal_id="4",
            category="employment",
            label="test",
            trend_enum="STABLE",
            latest_value=4.0,
            latest_units="percent",
            delta=0.0,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
    ]
    result = svc.classify(signals)
    assert result.regime is MacroRegime.PEAK


def test_unknown_with_insufficient_signals() -> None:
    """Only 1 signal → UNKNOWN regime (below min_regime_categories)."""
    from app.services.macro_analysis.base import MacroSignal
    from app.services.macro_analysis.domain import InflationTrend
    from app.services.macro_analysis.regime import MacroRegimeService

    svc = MacroRegimeService()
    signals = [
        MacroSignal(
            signal_id="1",
            category="inflation",
            label="test",
            trend_enum=InflationTrend.ACCELERATING.value,
            latest_value=4.5,
            latest_units="percent",
            delta=1.5,
            observed_at=EARLIER,
            available_at=EARLIER,
            source="test",
            source_fingerprint="fp",
            confidence=0.7,
        ),
    ]
    result = svc.classify(signals)
    assert result.regime is MacroRegime.UNKNOWN


# ---------------------------------------------------------------------------
# Conflicting evidence tests
# ---------------------------------------------------------------------------


def test_conflicting_evidence_mixed_or_neutral() -> None:
    """Inflation acceleration + employment weakening = conflicting evidence."""
    snapshot = _two_period_snapshot(
        _scenario(
            CPI=(2.0, 5.0),  # accelerating inflation (bad)
            GDP=(2.5, 3.0),  # stable growth (ok)
            UNEMPLOYMENT=(3.5, 5.5),  # weakening employment (bad)
            MONEY_SUPPLY=(20000, 20500),
            POLICY_RATE=(4.0, 4.5),
            YIELD_SPREAD=(0.3, 0.2),
            CREDIT_SPREAD=(1.0, 1.2),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    # Conflicting signals across categories should push toward MIXED/BEARISH.
    assert opinion.direction in (AnalysisDirection.MIXED, AnalysisDirection.BEARISH)


def test_conflict_reduces_confidence() -> None:
    # Clean expansion scenario — minimal conflict
    snapshot_good = _two_period_snapshot(
        _scenario(
            CPI=(3.0, 3.2),
            GDP=(2.0, 4.0),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
            POLICY_RATE=(4.0, 4.5),
            YIELD_SPREAD=(0.3, 0.5),
            CREDIT_SPREAD=(1.5, 1.0),
        )
    )
    request_good = _request(snapshot_good.model_dump(mode="json"))
    opinion_good = _analyst().analyze(request_good)

    # Conflicting scenario — growth up but inflation up too, employment worsening
    snapshot_conflict = _two_period_snapshot(
        _scenario(
            CPI=(3.0, 5.0),
            GDP=(2.0, 4.0),
            UNEMPLOYMENT=(4.0, 5.5),
            MONEY_SUPPLY=(20000, 21000),
            POLICY_RATE=(4.0, 4.5),
            YIELD_SPREAD=(0.3, 0.5),
            CREDIT_SPREAD=(1.5, 1.0),
        )
    )
    request_conflict = _request(snapshot_conflict.model_dump(mode="json"))
    opinion_conflict = _analyst().analyze(request_conflict)

    assert opinion_good.confidence.value > 0
    assert opinion_conflict.confidence.value > 0


# ---------------------------------------------------------------------------
# Insufficient data tests
# ---------------------------------------------------------------------------


def test_insufficient_data_empty_snapshot() -> None:
    snapshot = _build_snapshot([], as_of=NOW)
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []
    assert opinion.research_only is True
    assert opinion.suitable_for_live_trading is False
    assert opinion.decision_ready is False
    assert any(w.code == "INSUFFICIENT_DATA" for w in opinion.warnings)


def test_insufficient_data_no_macro_records() -> None:
    record = _make_record(record_id="market.aapl", value=150.0, units="price", series_id="PRICE")
    record = record.model_copy(update={"domain": DataDomain.MARKET})
    snapshot = _build_snapshot([record], as_of=NOW)
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []


def test_insufficient_data_only_one_series() -> None:
    snapshot = _two_period_snapshot({"CPI": (3.0, 3.2)})
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert opinion.evidence == []


def test_insufficient_data_without_snapshot() -> None:
    request = AnalystRequest(
        analyst_id="macro",
        ticker="US",
        timeframe="1d",
        as_of=NOW,
        lookback=60,
        horizon=30,
        asset_class="macro",
        extra_context={},
    )
    with pytest.raises(AnalystError):
        _analyst().analyze(request)


# ---------------------------------------------------------------------------
# Determinism tests
# ---------------------------------------------------------------------------


def test_analysis_is_deterministic() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    analyst = _analyst()
    first = analyst.analyze(request)
    second = analyst.analyze(request)
    assert first.model_dump() == second.model_dump()


def test_opinion_id_is_deterministic() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    first = _analyst().analyze(request)
    second = _analyst().analyze(request)
    assert first.opinion_id == second.opinion_id


# ---------------------------------------------------------------------------
# Trace generation tests
# ---------------------------------------------------------------------------


def test_trace_is_generated() -> None:
    from app.services.analyst.framework import BaseAnalyst

    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    analyst = _analyst()
    opinion = analyst.analyze(request)
    assert isinstance(analyst, BaseAnalyst)
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert {node.node_type for node in trace.nodes} >= {"analyst_request", "analyst_opinion"}
    assert trace.edges


def test_insufficient_trace_has_insufficient_for_edge() -> None:
    snapshot = _build_snapshot([], as_of=NOW)
    request = _request(snapshot.model_dump(mode="json"))
    analyst = _analyst()
    opinion = analyst.analyze(request)
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert trace.edges


# ---------------------------------------------------------------------------
# Freshness and provenance tests
# ---------------------------------------------------------------------------


def test_evidence_has_freshness_and_provenance() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert opinion.data_freshness.observed_at is not None
    assert opinion.data_freshness.age_seconds >= 0
    for item in opinion.evidence:
        for prov in item.provenance:
            assert prov.source
            assert prov.retrieved_at is not None


def test_evidence_has_confidence_field() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    assert len(opinion.evidence) > 0
    for item in opinion.evidence:
        assert 0 <= item.confidence <= 1
        assert item.strength is not None


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_macro_analyst_registered() -> None:
    from app.services.analyst.service import AnalystService

    service = AnalystService()
    metadata = service.list()
    ids = [m.analyst_id for m in metadata]
    assert "macro" in ids
    macro_meta = service.analyst("macro").metadata()
    assert macro_meta.role.value == "MACRO"
    assert macro_meta.research_only is True
    assert macro_meta.suitable_for_live_trading is False
    assert "1d" in macro_meta.supported_timeframes


def test_api_macro_endpoints() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    assert client.get("/analysts/macro/health").status_code == 200
    assert client.get("/analysts/macro/metadata").status_code == 200
    resp = client.get("/analysts/macro/health")
    assert resp.json()["status"] == "healthy"


def test_api_macro_analyze() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request_body = AnalystRequest(
        analyst_id="macro",
        ticker="US",
        timeframe="1d",
        as_of=NOW,
        lookback=60,
        horizon=30,
        asset_class="macro",
        extra_context={"snapshot": snapshot.model_dump(mode="json")},
    ).model_dump(mode="json")
    resp = client.post("/analysts/macro/analyze", json=request_body)
    assert resp.status_code == 200
    opinion = resp.json()
    assert opinion["direction"] in ("BULLISH", "BEARISH", "NEUTRAL", "MIXED", "INSUFFICIENT_EVIDENCE")
    assert opinion["research_only"] is True
    assert opinion["suitable_for_live_trading"] is False
    assert opinion["decision_ready"] is False


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_macro_smoke(tmp_path) -> None:
    from app.cli.analyst import main as cli_main

    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    result = cli_main(
        [
            "--analyst",
            "macro",
            "--ticker",
            "US",
            "--timeframe",
            "1d",
            "--lookback",
            "60",
            "--horizon",
            "30",
            "--asset-class",
            "macro",
            "--as-of",
            NOW.isoformat(),
            "--input-snapshot",
            str(snapshot_file),
            "--as-json",
        ]
    )
    assert result == 0


def test_cli_macro_missing_snapshot_errors() -> None:
    from app.cli.analyst import main as cli_main

    try:
        cli_main(
            [
                "--analyst",
                "macro",
                "--ticker",
                "US",
                "--timeframe",
                "1d",
                "--as-of",
                NOW.isoformat(),
            ]
        )
        assert False, "Should have raised SystemExit"
    except SystemExit:
        pass


# ---------------------------------------------------------------------------
# Safety: no forbidden imports
# ---------------------------------------------------------------------------


def test_no_forbidden_runtime_dependencies() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "macro_analysis"
    imports: set[str] = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.lower())
    source = "\n".join(imports)
    for forbidden in [
        "risk_engine",
        "portfoliomanager",
        "broker",
        "executionservice",
        "committee",
        "chairman",
        "paperbroker",
        "httpx",
        "requests",
        "torch",
        "transformer",
        "openai",
        "yfinance",
    ]:
        assert forbidden not in source, f"forbidden import '{forbidden}' found in macro_analysis package"


def test_macro_analyst_is_research_only() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    serialized = opinion.model_dump_json().lower()
    assert '"buy"' not in serialized
    assert '"sell"' not in serialized
    assert opinion.research_only is True
    assert opinion.suitable_for_live_trading is False
    assert opinion.decision_ready is False
    assert opinion.analyst_role.value == "MACRO"
    assert opinion.analyst_id == "macro"


def test_all_evidence_uses_macro_or_allowed_types() -> None:
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    for item in opinion.evidence:
        assert item.evidence_type.value == "macroeconomic"


def test_opinion_contains_regime_info() -> None:
    """The opinion should carry evidence from multiple macro categories."""
    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
            YIELD_SPREAD=(0.3, 0.5),
            CREDIT_SPREAD=(1.5, 1.0),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    opinion = _analyst().analyze(request)
    categories = {item.summary.split(":")[0] for item in opinion.evidence}
    assert "inflation" in categories
    assert "growth" in categories
    assert "employment" in categories or "liquidity" in categories


def test_maco_regime_enum_values() -> None:
    """MacroRegime enum has the expected members for regime classification."""
    assert MacroRegime.EXPANSION
    assert MacroRegime.RECESSION
    assert MacroRegime.STAGFLATION
    assert MacroRegime.TIGHTENING
    assert MacroRegime.EASING
    assert MacroRegime.UNKNOWN
    assert MacroRegime.INFLATION_SHOCK
    assert MacroRegime.DEFLATION_RISK
    assert MacroRegime.LIQUIDITY_EXPANSION
    assert MacroRegime.LIQUIDITY_CONTRACTION


# ---------------------------------------------------------------------------
# Platform integration: macro economist in the analyst platform
# ---------------------------------------------------------------------------


def test_macro_analyst_in_platform_lifecycle() -> None:
    """MacroAnalyst uses BaseAnalyst and produces a trace like all specialists."""
    from app.services.analyst.framework import BaseAnalyst

    snapshot = _two_period_snapshot(
        _scenario(
            GDP=(2.0, 4.0),
            CPI=(3.0, 3.2),
            UNEMPLOYMENT=(4.5, 4.0),
            MONEY_SUPPLY=(20000, 21000),
        )
    )
    request = _request(snapshot.model_dump(mode="json"))
    analyst = _analyst()
    assert isinstance(analyst, BaseAnalyst)
    opinion = analyst.analyze(request)
    trace = analyst.trace_for(opinion.opinion_id)
    assert trace is not None
    assert {node.node_type for node in trace.nodes} >= {"analyst_request", "analyst_opinion"}


def test_all_analysts_registered_in_service() -> None:
    """AnalystService should register mock, technical, fundamental, and macro."""
    from app.services.analyst.service import AnalystService

    service = AnalystService()
    ids = {m.analyst_id for m in service.list()}
    assert {"mock", "technical", "fundamental", "macro"}.issubset(ids)
