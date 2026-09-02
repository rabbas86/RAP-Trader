"""Phase 16H walk-forward evaluation service package."""

from app.services.walk_forward.errors import (
    BenchmarkMismatchError,
    CorruptedArtifactError,
    FutureEvaluationContaminationError,
    IncompleteLineageError,
    InsufficientHistoryError,
    InvalidPerformanceEvaluationLinkageError,
    InvalidWalkForwardMethodologyError,
    InvalidWindowOrderingError,
    MissingArtifactError,
    OverlappingTestWindowsError,
    WalkForwardEvaluationError,
    WrongReplayLinkageError,
)
from app.services.walk_forward.evaluator import WalkForwardEvaluationService
from app.services.walk_forward.models import (
    FoldStabilityMetrics,
    HistoricalBacktestReport,
    WalkForwardEvaluation,
    WalkForwardEvaluationMethodology,
    WalkForwardFold,
)

__all__ = [
    "BenchmarkMismatchError",
    "CorruptedArtifactError",
    "FoldStabilityMetrics",
    "FutureEvaluationContaminationError",
    "HistoricalBacktestReport",
    "IncompleteLineageError",
    "InsufficientHistoryError",
    "InvalidPerformanceEvaluationLinkageError",
    "InvalidWalkForwardMethodologyError",
    "InvalidWindowOrderingError",
    "MissingArtifactError",
    "OverlappingTestWindowsError",
    "WalkForwardEvaluation",
    "WalkForwardEvaluationError",
    "WalkForwardEvaluationMethodology",
    "WalkForwardEvaluationService",
    "WalkForwardFold",
    "WrongReplayLinkageError",
]
