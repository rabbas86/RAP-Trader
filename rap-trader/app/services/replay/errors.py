"""Typed replay DAG errors."""

from __future__ import annotations


class ReplayError(Exception):
    """Base replay DAG error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ReplayArtifactNotFoundError(ReplayError):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(code="REPLAY_ARTIFACT_NOT_FOUND", message="Replay artifact not found.")
        self.artifact_id = artifact_id


class ReplayIncompleteProvenanceError(ReplayError):
    def __init__(self, missing_ids: tuple[str, ...]) -> None:
        super().__init__(
            code="REPLAY_INCOMPLETE_PROVENANCE",
            message="Replay provenance is incomplete.",
        )
        self.missing_ids = missing_ids


class ReplayCycleDetectedError(ReplayError):
    def __init__(self, cycle: tuple[str, ...]) -> None:
        super().__init__(code="REPLAY_CYCLE_DETECTED", message="Replay graph contains a cycle.")
        self.cycle = cycle


class ReplayDepthExceededError(ReplayError):
    def __init__(self, max_depth: int) -> None:
        super().__init__(code="REPLAY_DEPTH_EXCEEDED", message="Replay traversal depth exceeded.")
        self.max_depth = max_depth


class ReplayGraphTooLargeError(ReplayError):
    def __init__(self, max_nodes: int) -> None:
        super().__init__(code="REPLAY_GRAPH_TOO_LARGE", message="Replay graph exceeds node limit.")
        self.max_nodes = max_nodes


class ReplayTemporalViolationError(ReplayError):
    def __init__(self, upstream_id: str, downstream_id: str) -> None:
        super().__init__(
            code="REPLAY_TEMPORAL_VIOLATION",
            message="Replay temporal no-lookahead validation failed.",
        )
        self.upstream_id = upstream_id
        self.downstream_id = downstream_id


class ReplayInvalidTerminalError(ReplayError):
    def __init__(self, artifact_id: str, artifact_type: str) -> None:
        super().__init__(
            code="REPLAY_INVALID_TERMINAL",
            message="Replay terminal artifact is not a TradeDecision.",
        )
        self.artifact_id = artifact_id
        self.artifact_type = artifact_type


class ReplayTypeMismatchError(ReplayError):
    def __init__(self, artifact_id: str, expected_type: str, actual_type: str) -> None:
        super().__init__(
            code="REPLAY_TYPE_MISMATCH",
            message="Replay artifact type mismatch.",
        )
        self.artifact_id = artifact_id
        self.expected_type = expected_type
        self.actual_type = actual_type


class ReplayGraphCorruptedError(ReplayError):
    def __init__(self, artifact_id: str, reason: str) -> None:
        super().__init__(code="REPLAY_GRAPH_CORRUPTED", message="Replay graph corruption detected.")
        self.artifact_id = artifact_id
        self.reason = reason


class ReplayManifestMismatchError(ReplayError):
    def __init__(self, manifest_id: str) -> None:
        super().__init__(
            code="REPLAY_MANIFEST_MISMATCH",
            message="Replay manifest does not match reconstructed graph.",
        )
        self.manifest_id = manifest_id


__all__ = [
    "ReplayArtifactNotFoundError",
    "ReplayCycleDetectedError",
    "ReplayDepthExceededError",
    "ReplayError",
    "ReplayGraphCorruptedError",
    "ReplayGraphTooLargeError",
    "ReplayIncompleteProvenanceError",
    "ReplayInvalidTerminalError",
    "ReplayManifestMismatchError",
    "ReplayTemporalViolationError",
    "ReplayTypeMismatchError",
]
