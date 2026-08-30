"""Outcome journal errors."""

from __future__ import annotations


class OutcomeJournalError(Exception):
    """Base outcome journal error."""


class OutcomeJournalValidationError(OutcomeJournalError):
    """Raised when an observation or evaluation fails linkage/integrity validation."""


class OutcomeJournalEntryNotFoundError(OutcomeJournalError):
    """Raised when a requested outcome artifact does not exist."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"Outcome journal entry not found: {artifact_id}")
        self.artifact_id = artifact_id


class OutcomeJournalQueryError(OutcomeJournalError):
    """Raised when an outcome query cannot be satisfied."""


class OutcomeJournalTemporalViolationError(OutcomeJournalError):
    """Raised when an observation violates the future-information ordering."""


class OutcomeEvaluationUnsupportedError(OutcomeJournalError):
    """Raised when evaluation cannot proceed due to unsupported inputs."""


__all__ = [
    "OutcomeEvaluationUnsupportedError",
    "OutcomeJournalEntryNotFoundError",
    "OutcomeJournalError",
    "OutcomeJournalQueryError",
    "OutcomeJournalTemporalViolationError",
    "OutcomeJournalValidationError",
]
