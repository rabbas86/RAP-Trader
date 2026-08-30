"""Phase 15H champion/challenger evaluation tests."""

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
    AttributionService,
    ComponentAttribution,
    ComponentKind,
    OutcomeAlignment,
)
from app.services.champion_challenger import (
    ChampionChallengerEvaluation,
    ChampionChallengerService,
    ChampionChallengerValidationError,
    ComparisonAssumptions,
    EvaluationMetrics,
    EvaluationRecommendation,
)
from app.services.decision_journal import DecisionJournalEntry, DecisionJournalService
from app.services.outcome_journal import (
    FuturePriceMethodology,
    OutcomeEvaluation,
    OutcomeJournalService,
    OutcomeObservation,
    OutcomeStatus,
    ReferencePriceMethodology,
)
from app.services.replay.manifest import DecisionRunManifest
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
        rationale="champion/challenger unit test decision",
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
                producer="champion-challenger-tests",
                producer_version="1.0",
            ),
        ),
    )
    return store.put(envelope)


def _journal_entry_payload(decision_artifact_id, manifest_id, graph_fingerprint, symbol="AAPL", direction="BUY"):
    return {
        "journal_entry_id": JOURNAL_ENTRY_ID,
        "journal_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_run_manifest_id": manifest_id,
        "research_run_id": RESEARCH_RUN_ID,
        "symbol": symbol,
        "decision_at": DECISION_AT.isoformat(),
        "logical_as_of": AS_OF.isoformat(),
        "direction": direction,
        "confidence": 0.85,
        "producer_version": "1.0",
        "graph_fingerprint": graph_fingerprint,
    }


def _observation_payload(decision_artifact_id, journal_entry_id, symbol="AAPL", horizon=1, observed_future_price=105.0):
    return {
        "observation_id": sha256_fingerprint(
            {
                "schema_version": "1.0",
                "decision_artifact_id": decision_artifact_id,
                "journal_entry_id": journal_entry_id,
                "horizon": horizon,
                "decision_at": DECISION_AT.isoformat(),
                "observation_at": datetime(2026, 8, 2, 10, 0, tzinfo=UTC).isoformat(),
                "adjustment": "raw",
                "session": "regular",
            }
        ),
        "outcome_schema_version": "1.0",
        "decision_artifact_id": decision_artifact_id,
        "decision_journal_entry_id": journal_entry_id,
        "symbol": symbol,
        "decision_at": DECISION_AT.isoformat(),
        "observation_at": datetime(2026, 8, 2, 10, 0, tzinfo=UTC).isoformat(),
        "horizon": horizon,
        "evaluation_timeframe": "1d",
        "reference_price_methodology": ReferencePriceMethodology.DECISION_BAR_CLOSE.value,
        "observed_future_price_methodology": FuturePriceMethodology.OBSERVATION_BAR_CLOSE.value,
        "reference_price_at_decision": 100.0,
        "observed_future_price": observed_future_price,
        "market_data_provider": "deterministic_mock",
        "adjustment": "raw",
        "session": "regular",
        "outcome_status": OutcomeStatus.COMPLETED.value,
    }


def _setup_decision_chain(store, ticker="AAPL", action="BUY"):
    decision_envelope = _put_artifact(
        store, _trade_decision(ticker=ticker, action=action).model_dump(mode="json"), ArtifactType.TRADE_DECISION
    )
    manifest_payload = {
        "manifest_schema_version": "1.0",
        "research_run_id": RESEARCH_RUN_ID,
        "terminal_artifact_id": decision_envelope.artifact_id,
        "logical_as_of": AS_OF.isoformat(),
        "ordered_graph_nodes": [],
        "ordered_graph_edges": [],
        "root_artifact_ids": [],
        "producer_version": "1.0",
    }
    manifest_envelope = _put_artifact(store, manifest_payload, ArtifactType.DECISION_RUN_MANIFEST)
    graph_fingerprint = DecisionRunManifest.model_validate(manifest_payload).graph_fingerprint()
    journal_service = DecisionJournalService(store=store)
    journal_entry = DecisionJournalEntry(
        **_journal_entry_payload(
            decision_envelope.artifact_id, manifest_envelope.artifact_id, graph_fingerprint, symbol=ticker, direction=action
        )
    )
    persisted_journal = journal_service.record_entry(journal_entry)
    return decision_envelope, manifest_envelope, persisted_journal


def _record_attribution(
    store,
    symbol="AAPL",
    direction="BUY",
    alignment=OutcomeAlignment.ALIGNED,
    signed_metric=0.05,
    confidence=0.8,
    component=ComponentKind.TECHNICAL,
):
    decision, manifest, journal = _setup_decision_chain(store, ticker=symbol, action=direction)
    observation_payload = _observation_payload(decision.artifact_id, journal.artifact_id, symbol=symbol)
    observation = OutcomeObservation(**observation_payload)
    outcome_service = OutcomeJournalService(store=store)
    persisted_observation = outcome_service.record_observation(observation)
    evaluation_envelope = outcome_service.evaluate_observation(persisted_observation.artifact_id, direction)
    evaluation = OutcomeEvaluation.model_validate(evaluation_envelope.payload)
    attribution_service = AttributionService(store=store)
    component_attribution = ComponentAttribution(
        component=component,
        component_artifact_id="a" * 64,
        component_name="Technical Analyst",
        historical_signal=direction.lower(),
        historical_confidence=confidence,
        historical_weight=0.25,
        weight_available=True,
        outcome_alignment=alignment,
        signed_outcome_metric=signed_metric,
        methodology="technical-1.0",
        producer_version="1.0",
    )
    record = attribution_service.attribute_decision(
        decision_artifact_id=decision.artifact_id,
        decision_run_manifest_id=manifest.artifact_id,
        decision_journal_entry_id=journal.artifact_id,
        outcome_evaluation_id=evaluation.evaluation_id,
        symbol=symbol,
        decision_at=DECISION_AT,
        horizon=1,
        period="2026-08",
        direction=direction,
        components=[component_attribution],
        outcome_alignment=alignment,
    )[0]
    return record, evaluation, component_attribution


def _metrics(
    sample_count=10,
    alignment_rate=0.8,
    average_signed_return=0.05,
    directionally_correct_rate=0.8,
    confidence_calibration=0.05,
    alignment_count=None,
    directionally_correct_count=None,
):
    if alignment_count is None:
        alignment_count = int(sample_count * alignment_rate)
    if directionally_correct_count is None:
        directionally_correct_count = int(sample_count * directionally_correct_rate)
    return EvaluationMetrics(
        sample_count=sample_count,
        alignment_count=alignment_count,
        alignment_rate=alignment_rate,
        average_signed_return=average_signed_return,
        directionally_correct_count=directionally_correct_count,
        directionally_correct_rate=directionally_correct_rate,
        confidence_calibration=confidence_calibration,
    )


def _assumptions(minimum_sample_size=5, horizon=1, same_methodology=True):
    return ComparisonAssumptions(
        horizon=horizon,
        same_instruments=True,
        same_horizon=True,
        same_methodology=same_methodology,
        same_pricing_convention=True,
        same_transaction_cost_assumptions=True,
        same_sample_eligibility=True,
        point_in_time_semantics_preserved=True,
        minimum_sample_size=minimum_sample_size,
        eligibility_rule="completed_evaluations_only",
        methodology="phase15h-deterministic-1.0",
    )


def test_immutable_evaluation() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(),
        challenger_metrics=_metrics(),
        comparison_assumptions=_assumptions(),
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
    )
    with pytest.raises(ValidationError):
        evaluation.recommendation = EvaluationRecommendation.REJECT_CHALLENGER


def test_identity_nested_mutation_is_immutable() -> None:
    evaluation = ChampionChallengerEvaluation(
        evaluation_id="b" * 64,
        evaluation_as_of=AS_OF,
        champion_identity={"model_id": "champion-v1", "tags": {"line": "alpha", "meta": {"nested": [1]}}},
        challenger_identity={"model_id": "challenger-v1", "tags": {"line": "beta", "meta": [{"nested": 1}]}},
        evaluation_period="2026-08",
        instruments=("AAPL",),
        horizon=1,
        sample_count=10,
        champion_metrics=_metrics(),
        challenger_metrics=_metrics(),
        methodology="phase15h-deterministic-1.0",
        comparison_assumptions=_assumptions(),
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
        producer_version="1.0",
    )
    expected_fingerprint = evaluation.fingerprint()
    expected_dump = evaluation.model_dump(mode="json")

    with pytest.raises((TypeError, ValueError)):
        evaluation.champion_identity["tags"]["meta"]["nested"][0] = 9
    with pytest.raises((TypeError, ValueError)):
        evaluation.challenger_identity["tags"]["line"] = "changed"
    with pytest.raises((TypeError, ValueError)):
        evaluation.challenger_identity["tags"]["meta"][0]["nested"] = 2

    assert evaluation.fingerprint() == expected_fingerprint
    assert evaluation.model_dump(mode="json") == expected_dump


def test_deterministic_identity() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    assumptions = _assumptions()
    metrics = _metrics()
    first, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=assumptions,
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
    )
    second, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=assumptions,
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
    )
    assert first.evaluation_id == second.evaluation_id
    assert first.envelope().artifact_id == second.envelope().artifact_id


def test_champion_challenger_identity_preserved() -> None:
    champion = {"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"}
    challenger = {"model_id": "challenger-v1", "model_version": "2.0", "provider": "rap"}
    evaluation = ChampionChallengerEvaluation(
        evaluation_id="b" * 64,
        evaluation_as_of=AS_OF,
        champion_identity=champion,
        challenger_identity=challenger,
        evaluation_period="2026-08",
        instruments=("AAPL",),
        horizon=1,
        sample_count=10,
        champion_metrics=_metrics(),
        challenger_metrics=_metrics(),
        methodology="phase15h-deterministic-1.0",
        comparison_assumptions=_assumptions(),
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
        producer_version="1.0",
    )
    normalized_champion = ChampionChallengerEvaluation._freeze_identity(champion)
    normalized_challenger = ChampionChallengerEvaluation._freeze_identity(challenger)
    assert evaluation.champion_identity == normalized_champion
    assert evaluation.challenger_identity == normalized_challenger


def test_fair_same_sample_comparison() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    champion_metrics = _metrics(alignment_rate=0.8, average_signed_return=0.05)
    challenger_metrics = _metrics(alignment_rate=0.9, average_signed_return=0.06)
    evaluation, _persisted = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.sample_count == champion_metrics.sample_count
    assert evaluation.champion_metrics.sample_count == evaluation.challenger_metrics.sample_count
    assert evaluation.recommendation == EvaluationRecommendation.PROMOTE_CHALLENGER_FOR_RESEARCH


def test_mismatched_sample_rejection() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    with pytest.raises(ChampionChallengerValidationError, match="sample counts must be equal"):
        service.evaluate(
            champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
            challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
            evaluation_period="2026-08",
            instruments=["AAPL"],
            horizon=1,
            champion_metrics=_metrics(sample_count=10),
            challenger_metrics=_metrics(sample_count=8),
            comparison_assumptions=_assumptions(minimum_sample_size=5),
        )


def test_mismatched_horizon_rejection() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    with pytest.raises(ChampionChallengerValidationError, match="horizon must match"):
        service.evaluate(
            champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
            challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
            evaluation_period="2026-08",
            instruments=["AAPL"],
            horizon=5,
            champion_metrics=_metrics(),
            challenger_metrics=_metrics(),
            comparison_assumptions=_assumptions(horizon=1),
        )


def test_incompatible_methodology_rejection() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    with pytest.raises(ChampionChallengerValidationError, match="same instruments, horizon, and methodology"):
        service.evaluate(
            champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
            challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
            evaluation_period="2026-08",
            instruments=["AAPL"],
            horizon=1,
            champion_metrics=_metrics(),
            challenger_metrics=_metrics(),
            comparison_assumptions=_assumptions(same_methodology=False),
        )


def test_minimum_sample_threshold() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    with pytest.raises(ChampionChallengerValidationError, match="below minimum evidence threshold"):
        service.evaluate(
            champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
            challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
            evaluation_period="2026-08",
            instruments=["AAPL"],
            horizon=1,
            champion_metrics=_metrics(sample_count=3),
            challenger_metrics=_metrics(sample_count=3),
            comparison_assumptions=_assumptions(minimum_sample_size=5),
        )


def test_insufficient_evidence() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(alignment_rate=0.5, average_signed_return=0.0),
        challenger_metrics=_metrics(alignment_rate=0.5, average_signed_return=0.0),
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.recommendation == EvaluationRecommendation.INSUFFICIENT_EVIDENCE


def test_keep_champion() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(alignment_rate=0.9, average_signed_return=0.08),
        challenger_metrics=_metrics(alignment_rate=0.6, average_signed_return=0.02),
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.recommendation == EvaluationRecommendation.KEEP_CHAMPION


def test_promote_challenger_for_research() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(alignment_rate=0.6, average_signed_return=0.02),
        challenger_metrics=_metrics(alignment_rate=0.85, average_signed_return=0.07),
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.recommendation == EvaluationRecommendation.PROMOTE_CHALLENGER_FOR_RESEARCH


def test_reject_challenger() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(alignment_rate=0.8, average_signed_return=0.05),
        challenger_metrics=_metrics(alignment_rate=0.3, average_signed_return=-0.04),
        comparison_assumptions=_assumptions(minimum_sample_size=5),
        recommendation=EvaluationRecommendation.REJECT_CHALLENGER,
    )
    assert evaluation.recommendation == EvaluationRecommendation.REJECT_CHALLENGER


def test_mixed_evidence_prefers_insufficient() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    metrics = _metrics(alignment_rate=0.8, average_signed_return=0.05, directionally_correct_rate=0.8, confidence_calibration=0.05)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.recommendation == EvaluationRecommendation.INSUFFICIENT_EVIDENCE


def test_explicit_thresholds() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    assumptions = ComparisonAssumptions(
        horizon=1,
        same_instruments=True,
        same_horizon=True,
        same_methodology=True,
        same_pricing_convention=True,
        same_transaction_cost_assumptions=True,
        same_sample_eligibility=True,
        point_in_time_semantics_preserved=True,
        minimum_sample_size=20,
        eligibility_rule="completed_evaluations_only",
        methodology="phase15h-explicit-threshold-1.0",
    )
    with pytest.raises(ChampionChallengerValidationError, match="below minimum evidence threshold"):
        service.evaluate(
            champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
            challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
            evaluation_period="2026-08",
            instruments=["AAPL"],
            horizon=1,
            champion_metrics=_metrics(sample_count=15),
            challenger_metrics=_metrics(sample_count=15),
            comparison_assumptions=assumptions,
        )


def test_sample_counts_preserved() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    champion_metrics = _metrics(sample_count=12, alignment_count=10)
    challenger_metrics = _metrics(sample_count=12, alignment_count=9)
    evaluation, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation.sample_count == champion_metrics.sample_count
    assert evaluation.champion_metrics.sample_count == champion_metrics.sample_count
    assert evaluation.challenger_metrics.sample_count == challenger_metrics.sample_count
    assert evaluation.champion_metrics.alignment_count == 10
    assert evaluation.challenger_metrics.alignment_count == 9


def test_signed_return_comparison() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    comparison = service.compare_metrics(
        _metrics(average_signed_return=0.05),
        _metrics(average_signed_return=0.08),
    )
    assert comparison["champion_average_signed_return"] == pytest.approx(0.05)
    assert comparison["challenger_average_signed_return"] == pytest.approx(0.08)


def test_alignment_comparison() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    comparison = service.compare_metrics(
        _metrics(alignment_rate=0.8),
        _metrics(alignment_rate=0.6),
    )
    assert comparison["champion_alignment_rate"] == pytest.approx(0.8)
    assert comparison["challenger_alignment_rate"] == pytest.approx(0.6)


def test_calibration_comparison() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    comparison = service.compare_metrics(
        _metrics(alignment_rate=0.8, confidence_calibration=0.2),
        _metrics(alignment_rate=0.8, confidence_calibration=0.1),
    )
    assert comparison["champion_confidence_calibration"] == pytest.approx(0.2)
    assert comparison["challenger_confidence_calibration"] == pytest.approx(0.1)


def test_attribution_integration() -> None:
    store = InMemoryArtifactStore()
    _record, _evaluation, _component = _record_attribution(
        store, symbol="AAPL", direction="BUY", alignment=OutcomeAlignment.ALIGNED, signed_metric=0.05
    )
    attribution_summary = AttributionService(store=store).aggregate(symbol="AAPL")
    assert len(attribution_summary) >= 1
    service = ChampionChallengerService(store=store)
    evaluation_obj, _ = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=_metrics(alignment_rate=0.8, average_signed_return=0.05),
        challenger_metrics=_metrics(alignment_rate=0.7, average_signed_return=0.04),
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert evaluation_obj.recommendation in {
        EvaluationRecommendation.KEEP_CHAMPION,
        EvaluationRecommendation.PROMOTE_CHALLENGER_FOR_RESEARCH,
        EvaluationRecommendation.INSUFFICIENT_EVIDENCE,
        EvaluationRecommendation.REJECT_CHALLENGER,
    }


def test_no_causal_claims() -> None:
    source = (Path(__file__).parent.parent / "app" / "services" / "champion_challenger" / "service.py").read_text()
    assert "deploy" not in source.lower()
    assert "execute" not in source.lower()
    assert "broker" not in source.lower()
    assert "live trading" not in source.lower()


def test_idempotent_persistence() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    champion_metrics = _metrics()
    challenger_metrics = _metrics()
    first, first_envelope = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    _second, second_envelope = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=champion_metrics,
        challenger_metrics=challenger_metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    assert first_envelope.artifact_id == second_envelope.artifact_id
    assert service.get_evaluation(first.evaluation_id).evaluation_id == first.evaluation_id


def test_deterministic_queries() -> None:
    store = InMemoryArtifactStore()
    service = ChampionChallengerService(store=store)
    metrics = _metrics()
    service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
        recommendation=EvaluationRecommendation.KEEP_CHAMPION,
    )
    service.evaluate(
        champion_identity={"model_id": "champion-v2", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["MSFT"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
        recommendation=EvaluationRecommendation.PROMOTE_CHALLENGER_FOR_RESEARCH,
    )
    assert len(service.query(recommendation="keep_champion")) == 1
    assert len(service.query(recommendation="promote_challenger_for_research")) == 1
    assert len(service.query(champion_model_id={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"})) == 1
    assert len(service.query(challenger_model_id={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"})) == 2
    assert len(service.query(evaluation_period="2026-08")) == 2


def test_artifact_store_persistence() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-champion-challenger-")
    store = FileArtifactStore(temp_dir)
    service = ChampionChallengerService(store=store)
    metrics = _metrics()
    evaluation, _persisted = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    reloaded = FileArtifactStore(temp_dir)
    restored_service = ChampionChallengerService(store=reloaded)
    restored_evaluation = restored_service.get_evaluation(evaluation.evaluation_id)
    assert restored_evaluation.evaluation_id == evaluation.evaluation_id
    assert restored_service.get_evaluation_envelope(evaluation.evaluation_id).payload["evaluation_id"] == evaluation.evaluation_id


def test_file_store_restart_index_rebuild() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-champion-challenger-restart-")
    store = FileArtifactStore(temp_dir)
    service = ChampionChallengerService(store=store)
    metrics = _metrics()
    service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    reloaded = FileArtifactStore(temp_dir)
    restored = ChampionChallengerService(store=reloaded)
    assert len(restored.query(evaluation_period="2026-08")) == 1


def test_corruption_propagation() -> None:
    temp_dir = tempfile.mkdtemp(prefix="rap-champion-challenger-corruption-")
    store = FileArtifactStore(temp_dir)
    service = ChampionChallengerService(store=store)
    metrics = _metrics()
    evaluation, _persisted = service.evaluate(
        champion_identity={"model_id": "champion-v1", "model_version": "1.0", "provider": "rap"},
        challenger_identity={"model_id": "challenger-v1", "model_version": "1.0", "provider": "rap"},
        evaluation_period="2026-08",
        instruments=["AAPL"],
        horizon=1,
        champion_metrics=metrics,
        challenger_metrics=metrics,
        comparison_assumptions=_assumptions(minimum_sample_size=5),
    )
    prefix = _persisted.artifact_id[:2]
    target_dir = os.path.join(temp_dir, "artifacts", prefix)
    filepath = os.path.join(target_dir, f"{_persisted.artifact_id}.json")
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write("not-json")
    with pytest.raises(ArtifactCorruptedError):
        service.get_evaluation_envelope(evaluation.evaluation_id)


def test_no_network_or_model_reruns() -> None:
    source = (Path(__file__).parent.parent / "app" / "services" / "champion_challenger" / "service.py").read_text()
    assert "import requests" not in source
    assert "import httpx" not in source
    assert "yfinance" not in source


def test_no_deployment_or_execution_authority() -> None:
    source = (Path(__file__).parent.parent / "app" / "services" / "champion_challenger" / "service.py").read_text()
    assert "deploy" not in source.lower()
    assert "execute" not in source.lower()
    assert "broker" not in source.lower()
    assert "live trading" not in source.lower()


def test_existing_phase_stack_unchanged() -> None:
    store = InMemoryArtifactStore()
    _, _, terminal = _setup_replay_chain(store)
    replay_service = ReplayService(store=store)
    graph = replay_service.build_graph(terminal.artifact_id)
    assert graph.terminal_artifact_id == terminal.artifact_id
    assert graph.node_count == 3


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

    source = DataSourceIdentity(
        provider="deterministic_mock",
        dataset="unit_test",
        source_version="1",
        schema_version="1",
        offline_capable=True,
        authoritative=False,
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
        source=source,
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
    "test_alignment_comparison",
    "test_artifact_store_persistence",
    "test_attribution_integration",
    "test_calibration_comparison",
    "test_champion_challenger_identity_preserved",
    "test_corruption_propagation",
    "test_deterministic_identity",
    "test_deterministic_queries",
    "test_existing_phase_stack_unchanged",
    "test_explicit_thresholds",
    "test_fair_same_sample_comparison",
    "test_file_store_restart_index_rebuild",
    "test_idempotent_persistence",
    "test_immutable_evaluation",
    "test_incompatible_methodology_rejection",
    "test_insufficient_evidence",
    "test_keep_champion",
    "test_minimum_sample_threshold",
    "test_mismatched_horizon_rejection",
    "test_mismatched_sample_rejection",
    "test_mixed_evidence_prefers_insufficient",
    "test_no_causal_claims",
    "test_no_deployment_or_execution_authority",
    "test_no_network_or_model_reruns",
    "test_promote_challenger_for_research",
    "test_reject_challenger",
    "test_sample_counts_preserved",
    "test_signed_return_comparison",
]
