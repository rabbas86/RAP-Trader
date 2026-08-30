"""Phase 15G Attribution Engine tests."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ArtifactEnvelope,
    ArtifactType,
    ProvenanceReference,
    ProvenanceReferenceKind,
)
from app.domain.models.decision import TradeDecision
from app.domain.models.market_data import Symbol
from app.services.artifacts.errors import ArtifactCorruptedError
from app.services.artifacts.file_store import FileArtifactStore
from app.services.artifacts.memory import InMemoryArtifactStore
from app.services.attribution import (
    AttributionRecord,
    AttributionService,
    AttributionValidationError,
    ComponentAttribution,
    ComponentKind,
    GovernanceAttribution,
    GovernanceInterventionKind,
    OutcomeAlignment,
)
from app.services.outcome_journal import (
    FuturePriceMethodology,
    OutcomeEvaluation,
    OutcomeJournalService,
    OutcomeStatus,
    ReferencePriceMethodology,
)
from app.services.replay.service import ReplayService

SYMBOL = Symbol("AAPL")
MSFT = Symbol("MSFT")
AS_OF = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
DECISION_AT = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
RESEARCH_RUN_ID = "c" * 64
JOURNAL_ENTRY_ID = "d" * 64


def _trade_decision(ticker="AAPL", action="BUY") -> TradeDecision:
    return TradeDecision(
        decision_id=UUID("12345678-1234-5678-1234-567812345678"),
        ticker=ticker,
        action=action,
        confidence=0.85,
        quantity=100,
        order_type="market",
        rationale="attribution unit test decision",
        evidence=[],
        created_at=DECISION_AT,
    )


def _put_artifact(store, payload, artifact_type, logical_as_of=AS_OF):
    envelope = ArtifactEnvelope.create(
        payload=payload,
        artifact_type=artifact_type,
        logical_as_of=logical_as_of,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier="0" * 64,
                description="unit test artifact",
                producer="attribution-tests",
                producer_version="1.0",
            ),
        ),
    )
    return store.put(envelope)


def _journal_entry_payload(
    decision_artifact_id,
    manifest_id,
    graph_fingerprint,
    symbol="AAPL",
    direction="BUY",
    decision_at=DECISION_AT,
    logical_as_of=AS_OF,
):
    return {
        "journal_entry_id": JOURNAL_ENTRY_ID,
        "journal_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_run_manifest_id": manifest_id,
        "research_run_id": RESEARCH_RUN_ID,
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "logical_as_of": logical_as_of.isoformat(),
        "direction": direction,
        "confidence": 0.85,
        "producer_version": "1.0",
        "graph_fingerprint": graph_fingerprint,
    }


def _setup_decision_chain(store, ticker="AAPL", action="BUY"):
    decision_envelope = _put_artifact(
        store, _trade_decision(ticker=ticker, action=action).model_dump(mode="json"), ArtifactType.TRADE_DECISION
    )
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "graph_fingerprint": "graph",
        "producer_version": "1.0",
        "node_artifact_ids": [decision_envelope.artifact_id],
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    journal_envelope = _put_artifact(
        store,
        _journal_entry_payload(decision_envelope.artifact_id, manifest_envelope.artifact_id, "graph"),
        ArtifactType.DECISION_JOURNAL_ENTRY,
    )
    return decision_envelope, manifest_envelope, journal_envelope


def _observation_payload(
    decision_artifact_id,
    journal_artifact_id,
    symbol="AAPL",
    horizon=1,
    outcome_status=OutcomeStatus.COMPLETED,
    observed_future_price=105.0,
    decision_at=DECISION_AT,
    observation_at=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
    adjustment="raw",
    session="regular",
):
    return {
        "observation_id": sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": decision_artifact_id,
                "journal_entry_id": journal_artifact_id,
                "horizon": horizon,
                "decision_at": decision_at.isoformat(),
                "observation_at": observation_at.isoformat(),
                "adjustment": adjustment,
                "session": session,
            }
        ),
        "outcome_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_journal_entry_id": journal_artifact_id,
        "symbol": symbol,
        "decision_at": decision_at.isoformat(),
        "observation_at": observation_at.isoformat(),
        "horizon": horizon,
        "evaluation_timeframe": "1d",
        "reference_price_methodology": ReferencePriceMethodology.DECISION_BAR_CLOSE.value,
        "observed_future_price_methodology": FuturePriceMethodology.OBSERVATION_BAR_CLOSE.value,
        "reference_price_at_decision": 100.0,
        "observed_future_price": observed_future_price,
        "market_data_provider": "deterministic_mock",
        "adjustment": adjustment,
        "session": session,
        "outcome_status": outcome_status.value,
    }


def test_immutable_record() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "BUY",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "aligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    with pytest.raises(ValidationError):
        record.decision_artifact_id = "different"


def test_deterministic_identity() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "BUY",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "aligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.fingerprint() == record.fingerprint()
    assert record.envelope().artifact_id == record.envelope().artifact_id


def test_bullish_aligned() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "BUY",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "aligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.components[0].outcome_alignment is OutcomeAlignment.ALIGNED


def test_bullish_misaligned() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        signed_outcome_metric=-0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "BUY",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "misaligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.components[0].outcome_alignment is OutcomeAlignment.MISALIGNED


def test_bearish_aligned() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="sell",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=-0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "SELL",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "aligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="SELL",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.components[0].outcome_alignment is OutcomeAlignment.ALIGNED


def test_bearish_misaligned() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="sell",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        signed_outcome_metric=0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "SELL",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "misaligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="SELL",
        components=(component,),
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.components[0].outcome_alignment is OutcomeAlignment.MISALIGNED


def test_neutral_semantics() -> None:
    component = ComponentAttribution(
        component=ComponentKind.MACRO,
        component_artifact_id="a" * 64,
        component_name="Macro Economist",
        historical_signal="neutral",
        historical_confidence=0.7,
        historical_weight=None,
        weight_available=False,
        outcome_alignment=OutcomeAlignment.NEUTRAL,
        signed_outcome_metric=0.0,
        methodology="macro-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "WAIT",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "neutral",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="WAIT",
        components=(component,),
        outcome_alignment=OutcomeAlignment.NEUTRAL,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    assert record.components[0].historical_weight is None
    assert record.components[0].weight_available is False
    assert record.components[0].signed_outcome_metric == pytest.approx(0.0)


def test_decision_outcome_component_linkage() -> None:
    store = InMemoryArtifactStore()
    decision, manifest, journal = _setup_decision_chain(store)
    observation_envelope = ArtifactEnvelope.create(
        payload=_observation_payload(decision.artifact_id, journal.artifact_id),
        artifact_type=ArtifactType.OUTCOME_OBSERVATION,
        logical_as_of=DECISION_AT,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=decision.artifact_id,
                description="decision for outcome observation",
                producer="attribution-tests",
                producer_version="1.0",
            ),
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=journal.artifact_id,
                description="journal entry for outcome observation",
                producer="attribution-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(observation_envelope)
    outcome_service = OutcomeJournalService(store=store)
    evaluation_envelope = outcome_service.evaluate_observation(observation_envelope.artifact_id, "BUY")
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)

    service = AttributionService(store=store)
    record = service.attribute_decision(
        decision_artifact_id=decision.artifact_id,
        decision_run_manifest_id=manifest.artifact_id,
        decision_journal_entry_id=journal.artifact_id,
        outcome_evaluation_id=evaluation.evaluation_id,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=[
            ComponentAttribution(
                component=ComponentKind.TECHNICAL,
                component_artifact_id="a" * 64,
                component_name="Technical Analyst",
                historical_signal="buy",
                historical_confidence=0.8,
                historical_weight=0.25,
                weight_available=True,
                outcome_alignment=OutcomeAlignment.ALIGNED,
                signed_outcome_metric=0.05,
                methodology="technical-1.0",
                producer_version="1.0",
            )
        ],
    )[0]
    assert record.decision_artifact_id == decision.artifact_id
    assert record.decision_journal_entry_id == journal.artifact_id
    assert record.outcome_evaluation_id == evaluation.evaluation_id


def test_historical_weight_preserved() -> None:
    component = ComponentAttribution(
        component=ComponentKind.PORTFOLIO,
        component_artifact_id="a" * 64,
        component_name="Portfolio Manager",
        historical_signal="buy",
        historical_confidence=0.9,
        historical_weight=0.4,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.03,
        methodology="portfolio-1.0",
        producer_version="1.0",
    )
    assert component.historical_weight == pytest.approx(0.4)
    assert component.weight_available is True


def test_missing_weight_not_fabricated() -> None:
    component = ComponentAttribution(
        component=ComponentKind.NEWS,
        component_artifact_id="a" * 64,
        component_name="News Analyst",
        historical_signal="negative",
        historical_confidence=0.6,
        historical_weight=None,
        weight_available=False,
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        signed_outcome_metric=-0.02,
        methodology="news-1.0",
        producer_version="1.0",
    )
    assert component.historical_weight is None
    assert component.weight_available is False


def test_multiple_components() -> None:
    components = [
        ComponentAttribution(
            component=ComponentKind.TECHNICAL,
            component_artifact_id="a" * 64,
            component_name="Technical Analyst",
            historical_signal="buy",
            historical_confidence=0.8,
            historical_weight=0.25,
            weight_available=True,
            outcome_alignment=OutcomeAlignment.ALIGNED,
            signed_outcome_metric=0.05,
            methodology="technical-1.0",
            producer_version="1.0",
        ),
        ComponentAttribution(
            component=ComponentKind.RISK,
            component_artifact_id="b" * 64,
            component_name="Risk Officer",
            historical_signal="reduce",
            historical_confidence=0.6,
            historical_weight=0.15,
            weight_available=True,
            outcome_alignment=OutcomeAlignment.MISALIGNED,
            signed_outcome_metric=-0.02,
            methodology="risk-1.0",
            producer_version="1.0",
        ),
    ]
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    record = service.attribute_decision(
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=components,
    )[0]
    assert len(record.components) == 2
    assert record.components[0].component is ComponentKind.TECHNICAL
    assert record.components[1].historical_weight == pytest.approx(0.15)


def test_missing_optional_component() -> None:
    components = [
        ComponentAttribution(
            component=ComponentKind.TECHNICAL,
            component_artifact_id="a" * 64,
            component_name="Technical Analyst",
            historical_signal="buy",
            historical_confidence=0.8,
            historical_weight=0.25,
            weight_available=True,
            outcome_alignment=OutcomeAlignment.ALIGNED,
            signed_outcome_metric=0.05,
            methodology="technical-1.0",
            producer_version="1.0",
        ),
    ]
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    record = service.attribute_decision(
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=components,
    )[0]
    assert len(record.components) == 1


def test_governance_potentially_beneficial() -> None:
    governance = GovernanceAttribution(
        pre_governance_signal="buy",
        post_governance_signal="reduce",
        intervention=GovernanceInterventionKind.REDUCED_WEIGHT,
        asset_outcome_direction="down",
        pre_governance_persisted=True,
        post_governance_persisted=True,
        assertion="Risk intervention was potentially beneficial relative to persisted pre-risk state.",
        producer_version="1.0",
    )
    assert governance.valid_comparison is True


def test_governance_potentially_costly() -> None:
    governance = GovernanceAttribution(
        pre_governance_signal="buy",
        post_governance_signal="rejected",
        intervention=GovernanceInterventionKind.REJECTED,
        asset_outcome_direction="up",
        pre_governance_persisted=True,
        post_governance_persisted=True,
        assertion="Risk intervention was potentially costly relative to persisted pre-risk state.",
        producer_version="1.0",
    )
    assert governance.valid_comparison is True


def test_invalid_governance_comparison_rejected() -> None:
    with pytest.raises(ValidationError, match="governance attribution requires valid persisted comparison"):
        AttributionRecord(
            attribution_id=sha256_fingerprint(
                {
                    "schema_version": "1.0",
                    "decision_artifact_id": "b" * 64,
                    "decision_journal_entry_id": "c" * 64,
                    "outcome_evaluation_id": "d" * 64,
                    "symbol": "AAPL",
                    "decision_at": DECISION_AT.isoformat(),
                    "horizon": 1,
                    "period": "2026-08",
                    "direction": "BUY",
                    "components": [
                        ComponentAttribution(
                            component=ComponentKind.TECHNICAL,
                            component_artifact_id="a" * 64,
                            component_name="Technical Analyst",
                            historical_signal="buy",
                            historical_confidence=0.8,
                            outcome_alignment=OutcomeAlignment.ALIGNED,
                            methodology="technical-1.0",
                            producer_version="1.0",
                        ).model_dump(mode="json")
                    ],
                    "outcome_alignment": "aligned",
                    "methodology": "phase15g-1.0",
                }
            ),
            decision_artifact_id="b" * 64,
            decision_run_manifest_id="e" * 64,
            decision_journal_entry_id="c" * 64,
            outcome_evaluation_id="d" * 64,
            symbol=Symbol("AAPL"),
            decision_at=DECISION_AT,
            horizon=1,
            period="2026-08",
            direction="BUY",
            components=(
                ComponentAttribution(
                    component=ComponentKind.TECHNICAL,
                    component_artifact_id="a" * 64,
                    component_name="Technical Analyst",
                    historical_signal="buy",
                    historical_confidence=0.8,
                    outcome_alignment=OutcomeAlignment.ALIGNED,
                    methodology="technical-1.0",
                    producer_version="1.0",
                ),
            ),
            governance=GovernanceAttribution(
                pre_governance_signal="buy",
                post_governance_signal="reduce",
                intervention=GovernanceInterventionKind.REDUCED_WEIGHT,
                asset_outcome_direction="down",
                pre_governance_persisted=False,
                post_governance_persisted=True,
                assertion="Risk intervention was potentially beneficial relative to persisted pre-risk state.",
                producer_version="1.0",
            ),
            outcome_alignment=OutcomeAlignment.ALIGNED,
            producer_version="1.0",
            methodology="phase15g-1.0",
        )


def test_unsupported_counterfactual_rejected() -> None:
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": "b" * 64,
                "decision_journal_entry_id": "c" * 64,
                "outcome_evaluation_id": "d" * 64,
                "symbol": "AAPL",
                "decision_at": DECISION_AT.isoformat(),
                "horizon": 1,
                "period": "2026-08",
                "direction": "BUY",
                "components": [component.model_dump(mode="json")],
                "outcome_alignment": "aligned",
                "methodology": "phase15g-1.0",
            }
        ),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol("AAPL"),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    with pytest.raises(AttributionValidationError, match="unsupported component: unknown"):
        service.attribute_decision(
            decision_artifact_id=record.decision_artifact_id,
            decision_run_manifest_id=record.decision_run_manifest_id,
            decision_journal_entry_id=record.decision_journal_entry_id,
            outcome_evaluation_id=record.outcome_evaluation_id,
            symbol="AAPL",
            decision_at=DECISION_AT,
            horizon=1,
            period="2026-08",
            direction="BUY",
            components=[
                ComponentAttribution(
                    component=ComponentKind.UNKNOWN,
                    component_artifact_id="a" * 64,
                    component_name="Unknown",
                    historical_signal="buy",
                    historical_confidence=0.8,
                    outcome_alignment=OutcomeAlignment.ALIGNED,
                    methodology="unknown-1.0",
                    producer_version="1.0",
                )
            ],
        )


def test_valid_persisted_comparison_required_for_governance() -> None:
    with pytest.raises(ValidationError, match="governance attribution requires valid persisted comparison"):
        AttributionRecord(
            attribution_id=sha256_fingerprint(
                {
                    "schema_version": "1.0",
                    "decision_artifact_id": "b" * 64,
                    "decision_journal_entry_id": "c" * 64,
                    "outcome_evaluation_id": "d" * 64,
                    "symbol": "AAPL",
                    "decision_at": DECISION_AT.isoformat(),
                    "horizon": 1,
                    "period": "2026-08",
                    "direction": "BUY",
                    "components": [
                        ComponentAttribution(
                            component=ComponentKind.TECHNICAL,
                            component_artifact_id="a" * 64,
                            component_name="Technical Analyst",
                            historical_signal="buy",
                            historical_confidence=0.8,
                            outcome_alignment=OutcomeAlignment.ALIGNED,
                            methodology="technical-1.0",
                            producer_version="1.0",
                        ).model_dump(mode="json")
                    ],
                    "outcome_alignment": "aligned",
                    "methodology": "phase15g-1.0",
                }
            ),
            decision_artifact_id="b" * 64,
            decision_run_manifest_id="e" * 64,
            decision_journal_entry_id="c" * 64,
            outcome_evaluation_id="d" * 64,
            symbol=Symbol("AAPL"),
            decision_at=DECISION_AT,
            horizon=1,
            period="2026-08",
            direction="BUY",
            components=(
                ComponentAttribution(
                    component=ComponentKind.TECHNICAL,
                    component_artifact_id="a" * 64,
                    component_name="Technical Analyst",
                    historical_signal="buy",
                    historical_confidence=0.8,
                    outcome_alignment=OutcomeAlignment.ALIGNED,
                    methodology="technical-1.0",
                    producer_version="1.0",
                ),
            ),
            governance=GovernanceAttribution(
                pre_governance_signal="buy",
                post_governance_signal="reduce",
                intervention=GovernanceInterventionKind.REDUCED_WEIGHT,
                asset_outcome_direction="down",
                pre_governance_persisted=False,
                post_governance_persisted=True,
                assertion="Risk intervention was potentially beneficial relative to persisted pre-risk state.",
                producer_version="1.0",
            ),
            outcome_alignment=OutcomeAlignment.ALIGNED,
            producer_version="1.0",
            methodology="phase15g-1.0",
        )


def test_deterministic_aggregation() -> None:
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    base_component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.05,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = _attribution_record(base_component, service=service)
    service.attribute_decision(
        decision_artifact_id=record.decision_artifact_id,
        decision_run_manifest_id=record.decision_run_manifest_id,
        decision_journal_entry_id=record.decision_journal_entry_id,
        outcome_evaluation_id=record.outcome_evaluation_id,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=[base_component],
        outcome_alignment=base_component.outcome_alignment,
    )[1]
    summary = service.aggregate(symbol="AAPL")
    assert summary == service.aggregate(symbol="AAPL")


def test_sample_counts() -> None:
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    aligned = ComponentAttribution(
        component=ComponentKind.KRONOS,
        component_artifact_id="a" * 64,
        component_name="Kronos",
        historical_signal="up",
        historical_confidence=0.7,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        signed_outcome_metric=0.03,
        methodology="kronos-1.0",
        producer_version="1.0",
    )
    misaligned = ComponentAttribution(
        component=ComponentKind.KRONOS,
        component_artifact_id="b" * 64,
        component_name="Kronos",
        historical_signal="up",
        historical_confidence=0.6,
        outcome_alignment=OutcomeAlignment.MISALIGNED,
        signed_outcome_metric=-0.01,
        methodology="kronos-1.0",
        producer_version="1.0",
    )
    _attribution_record(aligned, service=service)
    _attribution_record(misaligned, service=service)
    summaries = service.aggregate(component=ComponentKind.KRONOS)
    assert summaries[0].sample_count == 2
    assert summaries[0].alignment_count == 1
    assert summaries[0].alignment_rate == pytest.approx(0.5)


def test_confidence_calibration() -> None:
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    component = ComponentAttribution(
        component=ComponentKind.MACRO,
        component_artifact_id="a" * 64,
        component_name="Macro Economist",
        historical_signal="neutral",
        historical_confidence=0.7,
        outcome_alignment=OutcomeAlignment.NEUTRAL,
        signed_outcome_metric=0.0,
        methodology="macro-1.0",
        producer_version="1.0",
    )
    _attribution_record(component, service=service)
    summaries = service.aggregate(component=ComponentKind.MACRO)
    assert summaries[0].confidence_calibration == pytest.approx(0.7)


def test_deterministic_queries_order() -> None:
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    components = [
        ComponentAttribution(
            component=ComponentKind.TECHNICAL,
            component_artifact_id="a" * 64,
            component_name="Technical Analyst",
            historical_signal="buy",
            historical_confidence=0.8,
            outcome_alignment=OutcomeAlignment.ALIGNED,
            methodology="technical-1.0",
            producer_version="1.0",
        ),
        ComponentAttribution(
            component=ComponentKind.NEWS,
            component_artifact_id="b" * 64,
            component_name="News Analyst",
            historical_signal="positive",
            historical_confidence=0.6,
            outcome_alignment=OutcomeAlignment.MISALIGNED,
            methodology="news-1.0",
            producer_version="1.0",
        ),
    ]
    first_record = _attribution_record(components[0], service=service, symbol="AAPL")
    second_record = _attribution_record(components[1], service=service, symbol="MSFT")
    first_returned = service.query(symbol="AAPL")
    second_returned = service.query(symbol="MSFT")
    technical_returned = service.query(component=ComponentKind.TECHNICAL)
    assert [record.attribution_id for record in first_returned] == [first_record.attribution_id]
    assert [record.attribution_id for record in second_returned] == [second_record.attribution_id]
    assert [record.attribution_id for record in technical_returned] == [first_record.attribution_id]


def test_artifact_store_persistence() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-attribution-")
    store = FileArtifactStore(temp_dir)
    service = AttributionService(store=store)
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = _attribution_record(component, service=service)
    reloaded = FileArtifactStore(temp_dir)
    restored_service = AttributionService(store=reloaded)
    restored_record = restored_service.get_attribution(record.attribution_id)
    assert restored_record.attribution_id == record.attribution_id
    assert restored_service.get_attribution_envelope(record.attribution_id).payload["attribution_id"] == record.attribution_id


def test_idempotency() -> None:
    store = InMemoryArtifactStore()
    service = AttributionService(store=store)
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = _attribution_record(component, service=service)
    first = service.attribute_decision(
        decision_artifact_id=record.decision_artifact_id,
        decision_run_manifest_id=record.decision_run_manifest_id,
        decision_journal_entry_id=record.decision_journal_entry_id,
        outcome_evaluation_id=record.outcome_evaluation_id,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=[component],
    )[1]
    second = service.attribute_decision(
        decision_artifact_id=record.decision_artifact_id,
        decision_run_manifest_id=record.decision_run_manifest_id,
        decision_journal_entry_id=record.decision_journal_entry_id,
        outcome_evaluation_id=record.outcome_evaluation_id,
        symbol="AAPL",
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=[component],
    )[1]
    assert first.artifact_id == second.artifact_id


def test_restart_persistence() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-attribution-restart-")
    store = FileArtifactStore(temp_dir)
    service = AttributionService(store=store)
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = _attribution_record(component, service=service)
    reloaded = FileArtifactStore(temp_dir)
    restored = AttributionService(store=reloaded)
    restored_record = restored.get_attribution(record.attribution_id)
    assert restored_record.attribution_id == record.attribution_id


def test_corruption_propagation() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-attribution-corruption-")
    store = FileArtifactStore(temp_dir)
    service = AttributionService(store=store)
    component = ComponentAttribution(
        component=ComponentKind.TECHNICAL,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal="buy",
        historical_confidence=0.8,
        outcome_alignment=OutcomeAlignment.ALIGNED,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = _attribution_record(component, service=service)
    persisted = service.get_attribution_envelope(record.attribution_id)
    prefix = persisted.artifact_id[:2]
    target_dir = os.path.join(temp_dir, "artifacts", prefix)
    filepath = os.path.join(target_dir, f"{persisted.artifact_id}.json")
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("not-json")
    with pytest.raises(ArtifactCorruptedError):
        service.get_attribution_envelope(record.attribution_id)


def test_no_network_or_model_reruns() -> None:
    source = (Path(__file__).parent.parent / "app" / "services" / "attribution" / "service.py").read_text()
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "import yfinance" not in source


def test_existing_phase_stack_unchanged() -> None:
    store = InMemoryArtifactStore()
    _, _, terminal = _setup_replay_chain(store)
    replay_service = ReplayService(store=store)
    graph = replay_service.build_graph(terminal.artifact_id)
    assert graph.terminal_artifact_id == terminal.artifact_id
    assert graph.node_count == 3


def _attribution_record(
    component: ComponentAttribution, service: AttributionService | None = None, symbol: str = "AAPL"
) -> AttributionRecord:
    material = {
        "schema_version": "1.0",
        "decision_artifact_id": "b" * 64,
        "decision_journal_entry_id": "c" * 64,
        "outcome_evaluation_id": "d" * 64,
        "symbol": symbol,
        "decision_at": DECISION_AT.isoformat(),
        "horizon": 1,
        "period": "2026-08",
        "direction": "BUY",
        "components": [component.model_dump(mode="json")],
        "outcome_alignment": "aligned",
        "methodology": "phase15g-1.0",
    }
    record = AttributionRecord(
        attribution_id=sha256_fingerprint(material),
        decision_artifact_id="b" * 64,
        decision_run_manifest_id="e" * 64,
        decision_journal_entry_id="c" * 64,
        outcome_evaluation_id="d" * 64,
        symbol=Symbol(symbol),
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=(component,),
        outcome_alignment=OutcomeAlignment.ALIGNED,
        producer_version="1.0",
        methodology="phase15g-1.0",
    )
    if service is None:
        return record
    return service.attribute_decision(
        decision_artifact_id=record.decision_artifact_id,
        decision_run_manifest_id=record.decision_run_manifest_id,
        decision_journal_entry_id=record.decision_journal_entry_id,
        outcome_evaluation_id=record.outcome_evaluation_id,
        symbol=symbol,
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction="BUY",
        components=[component],
        outcome_alignment=component.outcome_alignment,
    )[0]


def _setup_replay_chain(store):
    from app.domain.models.data_platform import (
        DataAvailability,
        DataDomain,
        DataQuality,
        DataRecordId,
        DataRevision,
        DataSourceIdentity,
        NormalizedDataRecord,
        QualitySummary,
        ResearchDataSnapshot,
        SnapshotProvenance,
    )

    availability = DataAvailability(
        observed_at=AS_OF,
        available_at=AS_OF,
        ingested_at=AS_OF,
    )
    revision = DataRevision(
        revision_id="r0",
        revision_number=0,
        revised_at=AS_OF,
        available_at=AS_OF,
        source_fingerprint=sha256_fingerprint({"record": "revision"}),
    )
    quality = DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0)
    record = NormalizedDataRecord(
        record_id=DataRecordId("market.test.1"),
        domain=DataDomain.MARKET,
        symbol_or_entity="AAPL",
        value=189.55,
        units="price_close",
        availability=availability,
        revision=revision,
        source=DataSourceIdentity(
            provider="deterministic_mock",
            dataset="unit_test",
            source_version="1",
            schema_version="1",
            offline_capable=True,
            authoritative=False,
        ),
        quality=quality,
        source_fingerprint=sha256_fingerprint({"record": "market.test.1"}),
        schema_version="1",
        period_start=AS_OF,
        period_end=AS_OF,
    )
    provenance = SnapshotProvenance(
        snapshot_id="snapshot-unit-1",
        as_of=AS_OF,
        created_at=AS_OF,
        source_versions={"deterministic_mock": "1"},
        input_fingerprints=(sha256_fingerprint({"input": "market"}),),
        schema_version="1",
        platform_version="platform-1",
    )
    snapshot = ResearchDataSnapshot(
        snapshot_id="snapshot-unit-1",
        as_of=AS_OF,
        requested_domains=(DataDomain.MARKET,),
        records=(record,),
        source_versions={"deterministic_mock": "1"},
        schema_version="1",
        platform_version="platform-1",
        created_at=AS_OF,
        input_fingerprints=(sha256_fingerprint({"input": "market"}),),
        quality_summary=QualitySummary(
            total_records=1,
            average_score=1.0,
            records_with_warnings=0,
            domains_represented=(DataDomain.MARKET,),
        ),
        provenance=provenance,
    )
    root = ArtifactEnvelope.create(
        payload=snapshot.model_dump(mode="json"),
        artifact_type=ArtifactType.RESEARCH_DATA_SNAPSHOT,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.RESEARCH_RUN,
                identifier="0" * 64,
                description="unit-test research run",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(root)
    middle = ArtifactEnvelope.create(
        payload={"value": 2.0},
        artifact_type=ArtifactType.BACKTEST_SUMMARY,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=root.artifact_id,
                description="unit-test upstream",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(middle)
    terminal = ArtifactEnvelope.create(
        payload=_trade_decision().model_dump(mode="json"),
        artifact_type=ArtifactType.TRADE_DECISION,
        logical_as_of=AS_OF,
        producer_version="1.0",
        provenance_references=(
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=middle.artifact_id,
                description="unit-test upstream",
                producer="phase15d-tests",
                producer_version="1.0",
            ),
        ),
    )
    store.put(terminal)
    return root, middle, terminal


__all__ = [
    "test_artifact_store_persistence",
    "test_bearish_aligned",
    "test_bearish_misaligned",
    "test_bullish_aligned",
    "test_bullish_misaligned",
    "test_confidence_calibration",
    "test_corruption_propagation",
    "test_decision_outcome_component_linkage",
    "test_deterministic_aggregation",
    "test_deterministic_identity",
    "test_deterministic_queries_order",
    "test_existing_phase_stack_unchanged",
    "test_governance_potentially_beneficial",
    "test_governance_potentially_costly",
    "test_historical_weight_preserved",
    "test_idempotency",
    "test_immutable_record",
    "test_invalid_governance_comparison_rejected",
    "test_missing_optional_component",
    "test_missing_weight_not_fabricated",
    "test_multiple_components",
    "test_neutral_semantics",
    "test_no_network_or_model_reruns",
    "test_restart_persistence",
    "test_sample_counts",
    "test_unsupported_counterfactual_rejected",
    "test_valid_persisted_comparison_required_for_governance",
]
