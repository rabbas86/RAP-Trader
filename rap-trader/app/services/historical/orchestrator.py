"""Deterministic historical decision-pipeline orchestrator for Phase 16C.

The orchestrator consumes an immutable ``PointInTimeDataSnapshot`` and the
existing Phase 16A/B contracts. It never reaches around the snapshot to query
future or live market data directly, and it never places orders or connects
to brokers.

Supported historical mode
-------------------------
* ``DETERMINISTIC_RECOMPUTE``: bound to a snapshot and deterministic pipeline
  inputs only. Future outcome or attribution artifacts are explicitly rejected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from app.domain.models.artifact import ArtifactEnvelope, ArtifactType
from app.domain.models.decision import TradeDecision
from app.domain.models.historical_decision import HistoricalDecisionStep
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError
from app.services.decision_journal.entry import DecisionJournalEntry
from app.services.decision_journal.service import DecisionJournalService
from app.services.historical.decision_errors import (
    CorruptedSourceArtifactError,
    FutureSnapshotError,
    HistoricalDecisionError,
    HistoricalDecisionStepNotFoundError,
    InconsistentDecisionLineageError,
    InvalidDecisionCadenceError,
    LookaheadContaminationError,
    SnapshotReplaySpecificationMismatchError,
    UnsupportedHistoricalModeError,
)
from app.services.historical.snapshot import PointInTimeDataSnapshot
from app.services.replay.manifest import DecisionRunManifest
from app.services.replay.service import DecisionRunRecorder, ReplayService

_VALID_DECISION_CADENCES = frozenset({"window_close"})
_DEFAULT_WINDOW_CLOSE_CADENCE = {"1d": timedelta(days=1), "1w": timedelta(weeks=1)}


class _DecisionEngine(Protocol):
    def __call__(self) -> TradeDecision: ...


class HistoricalDecisionOrchestrator:
    """Bound deterministic historical decision orchestration to an immutable snapshot."""

    def __init__(
        self,
        *,
        clock: Any,
        specification: HistoricalReplaySpecification,
        snapshot: PointInTimeDataSnapshot | None,
        store: ArtifactStore,
        mode: str = "DETERMINISTIC_RECOMPUTE",
        decision_engine: _DecisionEngine | None = None,
        producer_version: str = "phase16c-1.0",
    ) -> None:
        if snapshot is None:
            raise HistoricalDecisionError("snapshot is required for historical decision orchestration")
        if mode != "DETERMINISTIC_RECOMPUTE":
            raise UnsupportedHistoricalModeError(mode)
        if snapshot.replay_specification_id != specification.specification_id:
            raise SnapshotReplaySpecificationMismatchError(snapshot.replay_specification_id, specification.specification_id)
        if snapshot.simulated_at > clock.now:
            raise FutureSnapshotError(snapshot.simulated_at.isoformat(), clock.now.isoformat())
        self.clock = clock
        self.specification = specification
        self.snapshot = snapshot
        self.store = store
        self.mode = mode
        self.decision_engine = decision_engine
        self.producer_version = producer_version
        self.recorder = DecisionRunRecorder(store=store, producer_version=producer_version)
        self.replay_service = ReplayService(store=store)

    def _snapshot_envelope_artifact_id(self) -> str:
        for artifact_id in self.store.list_ids():
            try:
                envelope = self.store.get(artifact_id)
            except ArtifactCorruptedError as exc:
                raise CorruptedSourceArtifactError(
                    artifact_id=artifact_id,
                    artifact_type="corrupted",
                    expected_type=ArtifactType.POINT_IN_TIME_DATA_SNAPSHOT.value,
                ) from exc
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            if payload.get("snapshot_id") == self.snapshot.snapshot_id:
                return artifact_id
        raise HistoricalDecisionStepNotFoundError(self.snapshot.snapshot_id)

    def load_snapshot_envelope(self) -> Any:
        artifact_id = self._snapshot_envelope_artifact_id()
        envelope = self.store.get(artifact_id)
        if envelope.artifact_type is not ArtifactType.POINT_IN_TIME_DATA_SNAPSHOT:
            raise CorruptedSourceArtifactError(artifact_id, envelope.artifact_type.value, ArtifactType.POINT_IN_TIME_DATA_SNAPSHOT.value)
        return envelope

    def deterministic_decision_schedule(self) -> tuple[datetime, ...]:
        """Return sorted deterministic decision points within the replay window."""
        cadence = self.specification.decision_cadence
        if cadence not in _VALID_DECISION_CADENCES:
            raise InvalidDecisionCadenceError(
                cadence,
                "only supported cadences can be scheduled deterministically",
            )
        return tuple(sorted(self._window_close_points()))

    def _window_close_points(self) -> list[datetime]:
        points: list[datetime] = []
        current = self.clock.start
        delta = _DEFAULT_WINDOW_CLOSE_CADENCE.get(self.specification.timeframes[0], timedelta(days=1))
        while current <= self.clock.end:
            candidate = _align_window_close(current, delta)
            candidate = max(candidate, self.clock.start)
            if self.clock.start <= candidate <= self.clock.end:
                points.append(candidate)
            current = candidate + delta
        return points

    def _reject_future_lineage_artifacts(self) -> None:
        disallowed_types = {
            ArtifactType.OUTCOME_EVALUATION,
            ArtifactType.ATTRIBUTION_RECORD,
            ArtifactType.CHAMPION_CHALLENGER_EVALUATION,
            ArtifactType.OUTCOME_OBSERVATION,
        }
        for artifact_id in self.store.list_ids():
            try:
                envelope = self.store.get(artifact_id)
            except ArtifactCorruptedError as exc:
                raise LookaheadContaminationError(artifact_id, "corrupted") from exc
            if envelope.artifact_type in disallowed_types and envelope.logical_as_of > self.snapshot.simulated_at:
                raise LookaheadContaminationError(envelope.artifact_id, envelope.artifact_type.value)

    def execute_decision_point(self, simulated_at: datetime) -> tuple[HistoricalDecisionStep, ArtifactEnvelope]:
        """Execute one deterministic historical decision point."""
        if simulated_at != self.snapshot.simulated_at:
            raise InconsistentDecisionLineageError(
                f"decision point {simulated_at.isoformat()} does not match snapshot time {self.snapshot.simulated_at.isoformat()}"
            )
        snapshot_envelope = self.load_snapshot_envelope()
        if snapshot_envelope.payload.get("simulated_at") != simulated_at.isoformat():
            raise InconsistentDecisionLineageError("persisted snapshot time does not match simulated_at")
        self._reject_future_lineage_artifacts()
        snapshot_artifact_id = snapshot_envelope.artifact_id
        decision = self._decide(simulated_at)
        decision_envelope = self._persist_decision(decision, simulated_at, snapshot_artifact_id=snapshot_artifact_id)
        manifest = DecisionRunManifest(
            research_run_id="0" * 64,
            terminal_artifact_id=decision_envelope.artifact_id,
            logical_as_of=simulated_at.isoformat(),
            ordered_graph_nodes=(snapshot_artifact_id, decision_envelope.artifact_id),
            ordered_graph_edges=((snapshot_artifact_id, decision_envelope.artifact_id),),
            root_artifact_ids=(snapshot_artifact_id,),
            producer_version=self.producer_version,
        )
        manifest_envelope = self.store.put(manifest.envelope())
        graph_fingerprint = manifest.graph_fingerprint()
        journal_entry = DecisionJournalEntry(
            journal_entry_id="0" * 64,
            decision_artifact_id=decision_envelope.artifact_id,
            decision_run_manifest_id=manifest_envelope.artifact_id,
            research_run_id="0" * 64,
            symbol=self.specification.instruments[0],
            decision_at=simulated_at,
            logical_as_of=simulated_at,
            direction=decision.action,
            confidence=decision.confidence,
            producer_version=self.producer_version,
            graph_fingerprint=graph_fingerprint,
        )
        journal_envelope = DecisionJournalService(store=self.store).record_entry(journal_entry)
        step = HistoricalDecisionStep.create_completed(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=self.specification.run_id,
            step_sequence=1,
            simulated_at=simulated_at,
            point_in_time_snapshot_id=self.snapshot.snapshot_id,
            snapshot_simulated_at=self.snapshot.simulated_at,
            methodology_version=self.specification.methodology_version,
            execution_mode=self.mode,
            producer_version=self.producer_version,
            input_fingerprints=self.snapshot.input_fingerprints,
            lineage_artifact_ids=(snapshot_artifact_id,),
            terminal_artifact_id=manifest_envelope.artifact_id,
            trade_decision_artifact_id=decision_envelope.artifact_id,
            decision_run_manifest_id=manifest_envelope.artifact_id,
            decision_journal_entry_id=journal_envelope.artifact_id,
        )
        step_envelope = step.envelope(provenance_references=self._step_provenance(step, snapshot_artifact_id=snapshot_artifact_id))
        persisted_envelope = self.store.put(step_envelope)
        return step, persisted_envelope

    def _decide(self, simulated_at: datetime) -> TradeDecision:
        if self.decision_engine is not None:
            return self.decision_engine()
        return TradeDecision(
            decision_id=uuid4(),
            ticker=str(self.specification.instruments[0]),
            action="WAIT",
            confidence=0.0,
            quantity=0,
            order_type="market",
            rationale="Phase 16C deterministic historical decision-pipeline safety default: WAIT",
            evidence=[],
            created_at=simulated_at,
        )

    def _persist_decision(self, decision: TradeDecision, simulated_at: datetime, *, snapshot_artifact_id: str) -> Any:
        payload = decision.model_dump(mode="json", exclude_none=True)
        payload["logical_as_of"] = simulated_at.isoformat()
        payload["recorded_at"] = datetime.now(UTC).isoformat()
        payload["research_only"] = True
        payload["paper_trading_only"] = True
        payload["suitable_for_live_trading"] = False
        return self.recorder.record_stage(
            artifact_type=ArtifactType.TRADE_DECISION,
            payload=payload,
            logical_as_of=simulated_at,
            research_run_id=None,
            upstream_artifact_ids=(snapshot_artifact_id,),
        )

    def _step_provenance(self, step: HistoricalDecisionStep, *, snapshot_artifact_id: str) -> tuple[Any, ...]:
        from app.domain.models.artifact import ProvenanceReference, ProvenanceReferenceKind

        references: list[ProvenanceReference] = [
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=snapshot_artifact_id,
                description="point-in-time snapshot bound to this historical decision",
                producer="rap-trader-phase16b",
                producer_version="1.0",
            ),
        ]
        if step.trade_decision_artifact_id:
            references.append(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=step.trade_decision_artifact_id,
                    description="historical trade decision artifact",
                    producer="rap-trader-phase16c",
                    producer_version=self.producer_version,
                )
            )
        if step.decision_run_manifest_id:
            references.append(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=step.decision_run_manifest_id,
                    description="historical decision run manifest",
                    producer="rap-trader-phase16c",
                    producer_version=self.producer_version,
                )
            )
        return tuple(references)

    def record_failed_step(self, simulated_at: datetime, failure_reference: str) -> HistoricalDecisionStep:
        if simulated_at != self.snapshot.simulated_at:
            raise InconsistentDecisionLineageError(
                f"failed step time {simulated_at.isoformat()} does not match snapshot time {self.snapshot.simulated_at.isoformat()}"
            )
        step = HistoricalDecisionStep.create_failed(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=self.specification.run_id,
            step_sequence=1,
            simulated_at=simulated_at,
            point_in_time_snapshot_id=self.snapshot.snapshot_id,
            snapshot_simulated_at=self.snapshot.simulated_at,
            methodology_version=self.specification.methodology_version,
            execution_mode=self.mode,
            failure_reference=failure_reference,
            producer_version=self.producer_version,
            input_fingerprints=self.snapshot.input_fingerprints,
            lineage_artifact_ids=(self.snapshot.snapshot_id,),
        )
        self.store.put(
            step.envelope(provenance_references=self._step_provenance(step, snapshot_artifact_id=self._snapshot_envelope_artifact_id()))
        )
        return step

    def get_step(self, artifact_id: str) -> HistoricalDecisionStep:
        try:
            envelope = self.store.get(artifact_id)
        except ArtifactNotFoundError:
            envelope = None
        if envelope is None or envelope.artifact_type is not ArtifactType.HISTORICAL_DECISION_STEP:
            for candidate_id in self.store.list_ids(filters={"artifact_type": ArtifactType.HISTORICAL_DECISION_STEP}):
                candidate = self.store.get(candidate_id)
                payload = candidate.payload if isinstance(candidate.payload, dict) else {}
                if (
                    payload.get("point_in_time_snapshot_id") == self.snapshot.snapshot_id
                    and payload.get("simulated_at") == self.snapshot.simulated_at.isoformat()
                ):
                    envelope = candidate
                    break
        if envelope is None:
            raise ArtifactNotFoundError(artifact_id=artifact_id)
        if envelope.artifact_type is not ArtifactType.HISTORICAL_DECISION_STEP:
            raise CorruptedSourceArtifactError(artifact_id, envelope.artifact_type.value, ArtifactType.HISTORICAL_DECISION_STEP.value)
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        return HistoricalDecisionStep.model_validate(payload)

    def idempotent_decision_point(self, simulated_at: datetime) -> tuple[HistoricalDecisionStep, ArtifactEnvelope]:
        if simulated_at != self.snapshot.simulated_at:
            raise InconsistentDecisionLineageError(
                f"decision point {simulated_at.isoformat()} does not match snapshot time {self.snapshot.simulated_at.isoformat()}"
            )
        candidates = [
            artifact_id
            for artifact_id in self.store.list_ids(
                filters={"artifact_type": ArtifactType.HISTORICAL_DECISION_STEP, "logical_as_of": simulated_at}
            )
            if self._step_matches_snapshot(artifact_id)
        ]
        if candidates:
            step = self.get_step(candidates[0])
            envelope = self.store.get(candidates[0])
            return step, envelope
        return self.execute_decision_point(simulated_at)

    def _step_matches_snapshot(self, artifact_id: str) -> bool:
        envelope = self.store.get(artifact_id)
        payload = envelope.payload if isinstance(envelope.payload, dict) else {}
        return payload.get("point_in_time_snapshot_id") == self.snapshot.snapshot_id


def _align_window_close(moment: datetime, delta: timedelta) -> datetime:
    if delta == timedelta(0):
        return moment
    aligned = moment - (moment - datetime(1970, 1, 1, tzinfo=UTC)) % delta
    if aligned < moment:
        aligned += delta
    return aligned


__all__ = ["HistoricalDecisionOrchestrator"]
