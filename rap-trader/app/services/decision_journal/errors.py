"""Decision journal errors."""

from __future__ import annotations


class DecisionJournalError(Exception):
    """Base decision journal error."""


class DecisionJournalValidationError(DecisionJournalError):
    """Raised when a journal entry fails linkage or integrity validation."""


class DecisionJournalEntryNotFoundError(DecisionJournalError):
    """Raised when a requested journal entry does not exist."""

    def __init__(self, decision_artifact_id: str) -> None:
        super().__init__(f"Decision journal entry not found: {decision_artifact_id}")
        self.decision_artifact_id = decision_artifact_id


class DecisionJournalQueryError(DecisionJournalError):
    """Raised when a journal query cannot be satisfied."""


__all__ = [
    "DecisionJournalEntryNotFoundError",
    "DecisionJournalError",
    "DecisionJournalQueryError",
    "DecisionJournalValidationError",
]
