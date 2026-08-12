"""Artifact store error model.

Stable, typed errors for the artifact persistence boundary.
"""

from __future__ import annotations


class ArtifactStoreError(Exception):
    """Base artifact store error.

    Parameters
    ----------
    code:
        Stable machine-readable error code.
    message:
        Human-readable safe message without internal filesystem detail.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ArtifactNotFoundError(ArtifactStoreError):
    """Requested artifact_id does not exist in the store."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            code="ARTIFACT_NOT_FOUND",
            message="Artifact not found.",
        )
        self.artifact_id = artifact_id


class ArtifactConflictError(ArtifactStoreError):
    """Write would replace an existing artifact with different bytes."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            code="ARTIFACT_CONFLICT",
            message="Artifact write rejected due to content conflict.",
        )
        self.artifact_id = artifact_id


class ArtifactCorruptedError(ArtifactStoreError):
    """Persisted artifact failed integrity verification."""

    def __init__(self, artifact_id: str, reason: str) -> None:
        super().__init__(
            code="ARTIFACT_CORRUPTED",
            message="Artifact integrity verification failed.",
        )
        self.artifact_id = artifact_id
        self.reason = reason


class UnsupportedSchemaVersionError(ArtifactStoreError):
    """Persisted artifact uses an unsupported schema version."""

    def __init__(self, schema_version: str) -> None:
        super().__init__(
            code="UNSUPPORTED_SCHEMA_VERSION",
            message="Unsupported artifact schema version.",
        )
        self.schema_version = schema_version


class InvalidArtifactIdError(ArtifactStoreError):
    """Artifact identifier is not a valid artifact_id."""

    def __init__(self, artifact_id: str) -> None:
        super().__init__(
            code="INVALID_ARTIFACT_ID",
            message="Invalid artifact identifier.",
        )
        self.artifact_id = artifact_id


class StoreIOError(ArtifactStoreError):
    """Durable store encountered an IO error."""

    def __init__(self, message: str) -> None:
        super().__init__(
            code="STORE_IO_ERROR",
            message="Store IO error.",
        )
        self.io_message = message


class InvalidStorePathError(ArtifactStoreError):
    """Storage path is invalid or outside the allowed store root."""

    def __init__(self, path: str) -> None:
        super().__init__(
            code="INVALID_STORE_PATH",
            message="Invalid store path.",
        )
        self.path = path


__all__ = [
    "ArtifactConflictError",
    "ArtifactCorruptedError",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "InvalidArtifactIdError",
    "InvalidStorePathError",
    "StoreIOError",
    "UnsupportedSchemaVersionError",
]
