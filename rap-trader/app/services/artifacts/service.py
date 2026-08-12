"""Service facade for artifact persistence."""

from __future__ import annotations

from typing import Any

from app.services.artifacts.base import ArtifactStore, _validate_artifact_id
from app.services.artifacts.errors import ArtifactStoreError


class ArtifactStoreService:
    """Facade over an ArtifactStore implementation.

    Parameters
    ----------
    store:
        Backing artifact store.
    """

    def __init__(self, store: ArtifactStore) -> None:
        self.store = store

    def save(self, artifact: Any) -> Any:
        """Persist an artifact and return it."""
        return self.store.put(artifact)

    def load(self, artifact_id: str) -> Any:
        """Load an artifact by ID or raise if missing/corrupted."""
        _validate_artifact_id(artifact_id)
        return self.store.get(artifact_id)

    def exists(self, artifact_id: str) -> bool:
        """Return whether an artifact exists."""
        try:
            _validate_artifact_id(artifact_id)
        except ArtifactStoreError:
            return False
        return self.store.exists(artifact_id)

    def list_ids(self, filters: dict[str, Any] | None = None) -> tuple[str, ...]:
        """Return deterministic artifact IDs."""
        return self.store.list_ids(filters=filters)

    def get_direct_dependencies(self, artifact_id: str) -> tuple[str, ...]:
        """Resolve direct provenance artifact identifiers."""
        return self.store.get_direct_dependencies(artifact_id)


__all__ = ["ArtifactStoreService"]
