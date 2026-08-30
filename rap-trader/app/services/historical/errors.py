"""Typed errors for the Phase 16B historical clock and point-in-time boundary."""

from __future__ import annotations


class HistoricalClockError(Exception):
    """Base deterministic historical-clock error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class HistoricalClockBackwardError(HistoricalClockError):
    """Raised when an explicit clock advance would move simulated time backwards."""

    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"clock cannot move backwards from {current} to {target}")
        self.current = current
        self.target = target


class HistoricalClockBoundsError(HistoricalClockError):
    """Raised when an explicit clock advance falls outside the replay range."""

    def __init__(self, reason: str, boundary: str) -> None:
        super().__init__(reason)
        self.boundary = boundary


class PointInTimeBoundaryError(Exception):
    """Base point-in-time boundary error."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class PointInTimeLookaheadError(PointInTimeBoundaryError):
    """Raised when a future-only record would be exposed at simulated time T."""

    def __init__(self, available_at: str, simulated_at: str) -> None:
        super().__init__(f"future record rejected: available_at {available_at} is after simulated time {simulated_at}")
        self.available_at = available_at
        self.simulated_at = simulated_at


class PointInTimeMissingAvailabilityError(PointInTimeBoundaryError):
    """Raised when availability metadata is required but missing."""

    def __init__(self, record_id: str) -> None:
        super().__init__(f"record {record_id} is missing availability metadata required by available_at-aware policy")
        self.record_id = record_id


class PointInTimeTemporalViolationError(PointInTimeBoundaryError):
    """Raised when a record has an impossible temporal relationship."""

    def __init__(self, record_id: str, detail: str) -> None:
        super().__init__(f"temporal violation for record {record_id}: {detail}")
        self.record_id = record_id
        self.detail = detail


__all__ = [
    "HistoricalClockBackwardError",
    "HistoricalClockBoundsError",
    "HistoricalClockError",
    "PointInTimeBoundaryError",
    "PointInTimeLookaheadError",
    "PointInTimeMissingAvailabilityError",
    "PointInTimeTemporalViolationError",
]
