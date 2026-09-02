"""Phase 16H walk-forward evaluation errors."""

from __future__ import annotations


class WalkForwardEvaluationError(Exception):
    """Base walk-forward evaluation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidWalkForwardMethodologyError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_WALK_FORWARD_METHODOLOGY", message=message)


class InsufficientHistoryError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INSUFFICIENT_HISTORY", message=message)


class InvalidWindowOrderingError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_WINDOW_ORDERING", message=message)


class OverlappingTestWindowsError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="OVERLAPPING_TEST_WINDOWS", message=message)


class WrongReplayLinkageError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="WRONG_REPLAY_LINKAGE", message=message)


class MissingArtifactError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="MISSING_ARTIFACT", message=message)


class CorruptedArtifactError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="CORRUPTED_ARTIFACT", message=message)


class InvalidPerformanceEvaluationLinkageError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_PERFORMANCE_EVALUATION_LINKAGE", message=message)


class BenchmarkMismatchError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="BENCHMARK_MISMATCH", message=message)


class FutureEvaluationContaminationError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="FUTURE_EVALUATION_CONTAMINATION", message=message)


class InconsistentMethodologyError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INCONSISTENT_METHODOLOGY", message=message)


class IncompleteLineageError(WalkForwardEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INCOMPLETE_LINEAGE", message=message)


__all__ = [
    "BenchmarkMismatchError",
    "CorruptedArtifactError",
    "FutureEvaluationContaminationError",
    "IncompleteLineageError",
    "InconsistentMethodologyError",
    "InsufficientHistoryError",
    "InvalidPerformanceEvaluationLinkageError",
    "InvalidWalkForwardMethodologyError",
    "InvalidWindowOrderingError",
    "MissingArtifactError",
    "OverlappingTestWindowsError",
    "WalkForwardEvaluationError",
    "WrongReplayLinkageError",
]
