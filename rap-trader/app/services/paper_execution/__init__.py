"""Phase 16D paper execution simulator contracts and services."""

from app.services.paper_execution.contracts import (
    PaperExecutionResult,
    PaperFill,
    PaperFillStatus,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
)
from app.services.paper_execution.errors import (
    CorruptedDecisionArtifactError,
    InvalidPaperInputError,
    MissingCanonicalSizingError,
    PaperExecutionError,
    ReplayLinkageError,
    UnfilledOrderError,
)
from app.services.paper_execution.models import (
    FillTimingPolicy,
    PaperExecutionMethodology,
    PaperOrderType,
    TimeInForce,
    UnfilledOrderPolicy,
)
from app.services.paper_execution.simulator import PaperExecutionSimulator

__all__ = [
    "CorruptedDecisionArtifactError",
    "FillTimingPolicy",
    "InvalidPaperInputError",
    "MissingCanonicalSizingError",
    "PaperExecutionError",
    "PaperExecutionMethodology",
    "PaperExecutionResult",
    "PaperExecutionSimulator",
    "PaperFill",
    "PaperFillStatus",
    "PaperOrder",
    "PaperOrderSide",
    "PaperOrderStatus",
    "PaperOrderType",
    "ReplayLinkageError",
    "TimeInForce",
    "UnfilledOrderError",
    "UnfilledOrderPolicy",
]
