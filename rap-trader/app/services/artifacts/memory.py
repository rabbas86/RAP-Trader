"""In-memory artifact store for tests and development."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any

from app.services.artifacts.base import (
    ArtifactStore,
    _deserialize_artifact,
    _serialize_artifact,
    _verify_integrity,
)
from app.services.artifacts.errors import (
    ArtifactConflictError,
    ArtifactNotFoundError,
)


class InMemoryArtifactStore(ArtifactStore):
    """Thread-safe in-memory artifact store.

    Required semantics:

    * first write succeeds and stores the artifact.
    * identical write is idempotent.
    * conflicting write with same artifact_id and different bytes raises.
    """

    def __init__(self) -> None:
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._lock = RLock()

    def put(self, artifact: Any) -> Any:
        with self._lock:
            artifact_id = artifact.artifact_id
            serialized = _serialize_artifact(artifact)
            existing_serialized = self._entries.get(artifact_id)
            if existing_serialized is not None:
                if existing_serialized != serialized:
                    raise ArtifactConflictError(artifact_id=artifact_id)
                return _deserialize_artifact(existing_serialized)

            _verify_integrity(artifact)
            self._entries[artifact_id] = serialized
            self._entries.move_to_end(artifact_id)
            return artifact

    def get(self, artifact_id: str) -> Any:
        with self._lock:
            serialized = self._entries.get(artifact_id)
            if serialized is None:
                raise ArtifactNotFoundError(artifact_id=artifact_id)
            return _deserialize_artifact(serialized)

    def exists(self, artifact_id: str) -> bool:
        with self._lock:
            return artifact_id in self._entries

    def list_ids(self, filters: dict[str, Any] | None = None) -> tuple[str, ...]:
        with self._lock:
            ids = list(self._entries.keys())

        if not filters:
            return tuple(ids)

        filtered = []
        for artifact_id in ids:
            artifact = _deserialize_artifact(self._entries[artifact_id])
            match = True
            if "artifact_type" in filters:
                match = match and artifact.artifact_type == filters["artifact_type"]
            if "logical_as_of" in filters:
                match = match and artifact.logical_as_of == filters["logical_as_of"]
            if "producer_version" in filters:
                match = match and artifact.producer_version == filters["producer_version"]
            if match:
                filtered.append(artifact_id)
        return tuple(filtered)


__all__ = ["InMemoryArtifactStore"]
