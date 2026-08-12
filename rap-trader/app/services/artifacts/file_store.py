"""Durable file-based artifact store with atomic writes."""

from __future__ import annotations

import os
import tempfile
from typing import Any

from app.domain.models.artifact import ARTIFACT_ID_PATTERN
from app.services.artifacts.base import (
    ArtifactStore,
    _deserialize_artifact,
    _serialize_artifact,
    _validate_artifact_id,
    _verify_integrity,
)
from app.services.artifacts.errors import (
    ArtifactConflictError,
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ArtifactStoreError,
    InvalidStorePathError,
    StoreIOError,
    UnsupportedSchemaVersionError,
)


class FileArtifactStore(ArtifactStore):
    """Atomic durable artifact store backed by JSON files.

    Parameters
    ----------
    root_dir:
        Root directory for artifact storage. The directory is created if
        it does not exist.
    """

    _ARTIFACT_ID_DIRECTORY_COMPONENTS = 2

    def __init__(self, root_dir: str) -> None:
        self.root_dir = os.path.abspath(root_dir)
        self.artifacts_dir = os.path.join(self.root_dir, "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)

    def _filepath(self, artifact_id: str) -> str:
        validated_id = _validate_artifact_id(artifact_id)
        prefix = validated_id[: self._ARTIFACT_ID_DIRECTORY_COMPONENTS]
        target_dir = os.path.join(self.artifacts_dir, prefix)
        os.makedirs(target_dir, exist_ok=True)
        filepath = os.path.abspath(os.path.join(target_dir, f"{validated_id}.json"))
        if not filepath.startswith(self.artifacts_dir + os.sep) and filepath != self.artifacts_dir:
            raise InvalidStorePathError(path=filepath)
        return filepath

    def _load(self, filepath: str) -> Any:
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                data = fh.read()
        except OSError as exc:
            raise StoreIOError(message=str(exc)) from exc

        envelope = _deserialize_artifact(data)
        _verify_integrity(envelope)

        expected_id = os.path.splitext(os.path.basename(filepath))[0]
        if envelope.artifact_id != expected_id:
            raise ArtifactCorruptedError(
                artifact_id=expected_id,
                reason="filename artifact_id does not match persisted artifact_id",
            )
        return envelope

    def put(self, artifact: Any) -> Any:
        filepath = self._filepath(artifact.artifact_id)
        _verify_integrity(artifact)
        serialized = _serialize_artifact(artifact)

        if os.path.isfile(filepath):
            try:
                existing = self._load(filepath)
            except (ArtifactCorruptedError, UnsupportedSchemaVersionError, StoreIOError):
                existing = None
            if existing is not None:
                if _serialize_artifact(existing) != serialized:
                    raise ArtifactConflictError(artifact_id=artifact.artifact_id)
                return existing

        dir_name = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=f".{artifact.artifact_id}_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(serialized)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, filepath)
        except (OSError, TypeError, ValueError):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise StoreIOError(message=str(filepath)) from None

        return artifact

    def get(self, artifact_id: str) -> Any:
        filepath = self._filepath(artifact_id)
        if not os.path.isfile(filepath):
            raise ArtifactNotFoundError(artifact_id=artifact_id)
        return self._load(filepath)

    def exists(self, artifact_id: str) -> bool:
        return os.path.isfile(self._filepath(artifact_id))

    def list_ids(self, filters: dict[str, Any] | None = None) -> tuple[str, ...]:
        if not os.path.isdir(self.artifacts_dir):
            return ()

        ids = []
        for root, _, files in os.walk(self.artifacts_dir):
            for filename in files:
                if not filename.endswith(".json"):
                    continue
                candidate_id = filename[:-5]
                if ARTIFACT_ID_PATTERN.fullmatch(candidate_id):
                    ids.append(candidate_id)

        ids.sort()

        if not filters:
            return tuple(ids)

        filtered = []
        for candidate_id in ids:
            prefix = candidate_id[: self._ARTIFACT_ID_DIRECTORY_COMPONENTS]
            filepath = os.path.join(self.artifacts_dir, prefix, f"{candidate_id}.json")
            try:
                artifact = self._load(filepath)
            except ArtifactStoreError:
                continue
            match = True
            if "artifact_type" in filters:
                match = match and artifact.artifact_type == filters["artifact_type"]
            if "logical_as_of" in filters:
                match = match and artifact.logical_as_of == filters["logical_as_of"]
            if "producer_version" in filters:
                match = match and artifact.producer_version == filters["producer_version"]
            if match:
                filtered.append(candidate_id)
        return tuple(filtered)


__all__ = ["FileArtifactStore"]
