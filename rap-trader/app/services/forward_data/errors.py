"""Typed errors for Phase 17A forward data ingestion."""

from __future__ import annotations


class ForwardDataServiceError(Exception):
    """Base forward data service error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class InvalidSourceError(ForwardDataServiceError):
    def __init__(self, message: str = "Invalid forward data source.") -> None:
        super().__init__(code="INVALID_SOURCE", message=message)


class NaiveTimestampError(ForwardDataServiceError):
    def __init__(self, message: str = "Naive timestamp rejected; UTC-aware timestamps are required.") -> None:
        super().__init__(code="NAIVE_TIMESTAMP", message=message)


class InvalidEventIntervalError(ForwardDataServiceError):
    def __init__(self, message: str = "Invalid observation event interval.") -> None:
        super().__init__(code="INVALID_EVENT_INTERVAL", message=message)


class InvalidOHLCError(ForwardDataServiceError):
    def __init__(self, message: str = "Invalid OHLC structure.") -> None:
        super().__init__(code="INVALID_OHLC", message=message)


class NegativeVolumeError(ForwardDataServiceError):
    def __init__(self, message: str = "Volume must be non-negative.") -> None:
        super().__init__(code="NEGATIVE_VOLUME", message=message)


class UnsupportedTimeframeError(ForwardDataServiceError):
    def __init__(self, timeframe: str) -> None:
        super().__init__(code="UNSUPPORTED_TIMEFRAME", message=f"Unsupported timeframe: {timeframe}")


class WrongSymbolError(ForwardDataServiceError):
    def __init__(self, message: str = "Symbol mismatch.") -> None:
        super().__init__(code="WRONG_SYMBOL", message=message)


class DuplicateConflictError(ForwardDataServiceError):
    def __init__(self, observation_id: str) -> None:
        super().__init__(code="DUPLICATE_CONFLICT", message="Conflicting duplicate observation detected.")
        self.observation_id = observation_id


class RevisionMismatchError(ForwardDataServiceError):
    def __init__(self, message: str = "Revision lineage mismatch.") -> None:
        super().__init__(code="REVISION_MISMATCH", message=message)


class WrongArtifactTypeError(ForwardDataServiceError):
    def __init__(self, message: str = "Wrong artifact type for forward data operation.") -> None:
        super().__init__(code="WRONG_ARTIFACT_TYPE", message=message)


class UnsupportedObservationTypeError(ForwardDataServiceError):
    def __init__(self, observation_type: str) -> None:
        super().__init__(code="UNSUPPORTED_OBSERVATION_TYPE", message=f"Unsupported observation type: {observation_type}")


class CorruptArtifactError(ForwardDataServiceError):
    def __init__(self, artifact_id: str, reason: str) -> None:
        super().__init__(code="CORRUPT_ARTIFACT", message="Artifact integrity verification failed.")
        self.artifact_id = artifact_id
        self.reason = reason


__all__ = [
    "CorruptArtifactError",
    "DuplicateConflictError",
    "ForwardDataServiceError",
    "InvalidEventIntervalError",
    "InvalidOHLCError",
    "InvalidSourceError",
    "NaiveTimestampError",
    "NegativeVolumeError",
    "RevisionMismatchError",
    "UnsupportedObservationTypeError",
    "UnsupportedTimeframeError",
    "WrongArtifactTypeError",
    "WrongSymbolError",
]
