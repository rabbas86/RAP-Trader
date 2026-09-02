"""Phase 16G performance evaluation errors."""

from __future__ import annotations


class PerformanceEvaluationError(Exception):
    """Base performance evaluation error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidMethodologyError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_METHODOLOGY", message=message)


class InsufficientSampleError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INSUFFICIENT_SAMPLE", message=message)


class ValuationCoverageError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="VALUATION_COVERAGE_ERROR", message=message)


class BenchmarkAlignmentError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="BENCHMARK_ALIGNMENT_ERROR", message=message)


class MissingBenchmarkDataError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="MISSING_BENCHMARK_DATA", message=message)


class MismatchedReplayLinkageError(PerformanceEvaluationError):
    def __init__(self, message: str) -> None:
        super().__init__(code="MISMATCHED_REPLAY_LINKAGE", message=message)


class LookaheadContaminationError(PerformanceEvaluationError):
    def __init__(self, artifact_id: str, artifact_type: str) -> None:
        super().__init__(
            code="LOOKAHEAD_CONTAMINATION",
            message=f"lookahead contamination rejected: {artifact_type} artifact {artifact_id} is not allowed downstream",
        )
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type


__all__ = [
    "BenchmarkAlignmentError",
    "InsufficientSampleError",
    "InvalidMethodologyError",
    "LookaheadContaminationError",
    "MismatchedReplayLinkageError",
    "MissingBenchmarkDataError",
    "PerformanceEvaluationError",
    "ValuationCoverageError",
]
