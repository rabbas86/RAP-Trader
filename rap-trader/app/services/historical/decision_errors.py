"""Typed errors for Phase 16C historical decision orchestration."""

from __future__ import annotations


class HistoricalDecisionError(Exception):
    """Base historical decision-pipeline error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class HistoricalDecisionSnapshotError(HistoricalDecisionError):
    """Raised when the selected snapshot is invalid for historical orchestration."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class FutureSnapshotError(HistoricalDecisionSnapshotError):
    """Raised when the selected snapshot is from a future simulated time."""

    def __init__(self, snapshot_time: str, decision_time: str) -> None:
        super().__init__(f"future snapshot rejected: snapshot_time {snapshot_time} is after decision time {decision_time}")
        self.snapshot_time = snapshot_time
        self.decision_time = decision_time


class SnapshotReplaySpecificationMismatchError(HistoricalDecisionSnapshotError):
    """Raised when the snapshot belongs to a different replay specification."""

    def __init__(self, snapshot_specification_id: str, expected_specification_id: str) -> None:
        super().__init__(
            f"snapshot replay_specification_id {snapshot_specification_id} does not match expected {expected_specification_id}"
        )
        self.snapshot_specification_id = snapshot_specification_id
        self.expected_specification_id = expected_specification_id


class UnsupportedHistoricalModeError(HistoricalDecisionError):
    """Raised when an unsupported historical execution mode is requested."""

    def __init__(self, mode: str) -> None:
        super().__init__(f"unsupported historical execution mode: {mode}")
        self.mode = mode


class MissingHistoricalConfigurationError(HistoricalDecisionError):
    """Raised when required immutable historical configuration identity is missing."""

    def __init__(self, missing: str) -> None:
        super().__init__(f"missing required historical configuration identity: {missing}")
        self.missing = missing


class InvalidDecisionCadenceError(HistoricalDecisionError):
    """Raised when the requested decision cadence cannot be scheduled deterministically."""

    def __init__(self, cadence: str, reason: str) -> None:
        super().__init__(f"invalid decision cadence {cadence}: {reason}")
        self.cadence = cadence
        self.reason = reason


class CorruptedSourceArtifactError(HistoricalDecisionError):
    """Raised when a required upstream source artifact is corrupted or wrong-typed."""

    def __init__(self, artifact_id: str, artifact_type: str, expected_type: str) -> None:
        super().__init__(f"source artifact {artifact_id} has wrong or corrupted type {artifact_type}; expected {expected_type}")
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type
        self.expected_type = expected_type


class InconsistentDecisionLineageError(HistoricalDecisionError):
    """Raised when ResearchRun/DecisionRunManifest linkage is inconsistent."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class LookaheadContaminationError(HistoricalDecisionError):
    """Raised when a future-only evaluation artifact would contaminate historical T0."""

    def __init__(self, artifact_id: str, artifact_type: str) -> None:
        super().__init__(f"lookahead contamination rejected: {artifact_type} artifact {artifact_id} is not allowed at historical T0")
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type


class NonDeterministicPipelineDependencyError(HistoricalDecisionError):
    """Raised when a required pipeline dependency cannot be run offline deterministically."""

    def __init__(self, dependency: str, reason: str) -> None:
        super().__init__(f"non-deterministic pipeline dependency {dependency}: {reason}")
        self.dependency = dependency
        self.reason = reason


class HistoricalDecisionStepNotFoundError(HistoricalDecisionError):
    """Raised when a requested historical decision step cannot be found."""

    def __init__(self, step_id: str) -> None:
        super().__init__(f"historical decision step not found: {step_id}")
        self.step_id = step_id


__all__ = [
    "CorruptedSourceArtifactError",
    "FutureSnapshotError",
    "HistoricalDecisionError",
    "HistoricalDecisionSnapshotError",
    "HistoricalDecisionStepNotFoundError",
    "InconsistentDecisionLineageError",
    "InvalidDecisionCadenceError",
    "LookaheadContaminationError",
    "MissingHistoricalConfigurationError",
    "NonDeterministicPipelineDependencyError",
    "SnapshotReplaySpecificationMismatchError",
    "UnsupportedHistoricalModeError",
]
