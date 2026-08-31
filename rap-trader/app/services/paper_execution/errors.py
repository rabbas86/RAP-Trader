"""Typed errors for Phase 16D paper execution simulation."""

from __future__ import annotations


class PaperExecutionError(Exception):
    """Base paper execution simulation error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class InvalidPaperInputError(PaperExecutionError):
    """Raised when paper execution inputs are invalid."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class MissingCanonicalSizingError(PaperExecutionError):
    """Raised when a TradeDecision lacks required canonical sizing."""

    def __init__(self, decision_artifact_id: str) -> None:
        super().__init__(f"trade decision artifact {decision_artifact_id} lacks required canonical sizing for paper execution")
        self.decision_artifact_id = decision_artifact_id


class CorruptedDecisionArtifactError(PaperExecutionError):
    """Raised when a required upstream decision artifact is corrupted."""

    def __init__(self, artifact_id: str, artifact_type: str, expected_type: str) -> None:
        super().__init__(f"source artifact {artifact_id} has wrong or corrupted type {artifact_type}; expected {expected_type}")
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type
        self.expected_type = expected_type


class ReplayLinkageError(PaperExecutionError):
    """Raised when replay linkage is inconsistent."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class UnfilledOrderError(PaperExecutionError):
    """Raised when an order cannot be filled deterministically."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = [
    "CorruptedDecisionArtifactError",
    "InvalidPaperInputError",
    "MissingCanonicalSizingError",
    "PaperExecutionError",
    "ReplayLinkageError",
    "UnfilledOrderError",
]
