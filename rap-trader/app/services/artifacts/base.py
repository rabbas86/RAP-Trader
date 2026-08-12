"""Artifact store abstraction and shared helpers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import OrderedDict
from threading import RLock
from typing import Any

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import (
    ARTIFACT_ID_PATTERN,
    ARTIFACT_SCHEMA_VERSION,
    ArtifactEnvelope,
)
from app.services.artifacts.errors import (
    ArtifactConflictError,
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    InvalidArtifactIdError,
    UnsupportedSchemaVersionError,
)


def _validate_artifact_id(artifact_id: str) -> str:
    if not isinstance(artifact_id, str) or not ARTIFACT_ID_PATTERN.fullmatch(artifact_id):
        raise InvalidArtifactIdError(artifact_id=artifact_id)
    return artifact_id


def _normalize_schema_version(schema_version: str) -> str:
    if schema_version != ARTIFACT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(schema_version=schema_version)
    return schema_version


def _verify_integrity(envelope: ArtifactEnvelope) -> None:
    if not isinstance(envelope, ArtifactEnvelope):
        raise ArtifactCorruptedError(artifact_id="unknown", reason="object is not an ArtifactEnvelope")

    try:
        validated_id = _validate_artifact_id(envelope.artifact_id)
    except InvalidArtifactIdError as exc:
        raise ArtifactCorruptedError(artifact_id=exc.artifact_id, reason="invalid artifact_id") from exc

    try:
        _normalize_schema_version(envelope.schema_version)
    except UnsupportedSchemaVersionError as exc:
        raise ArtifactCorruptedError(artifact_id=validated_id, reason="unsupported schema version") from exc

    expected_artifact_id = sha256_fingerprint(
        {
            "artifact_type": envelope.artifact_type.value,
            "schema_version": envelope.schema_version,
            "logical_as_of": envelope.logical_as_of.isoformat(),
            "producer_version": envelope.producer_version,
            "payload_hash": envelope.payload_hash,
            "provenance_references": [reference.model_dump(mode="json", exclude_none=True) for reference in envelope.provenance_references],
        }
    )
    if expected_artifact_id != validated_id:
        raise ArtifactCorruptedError(
            artifact_id=validated_id,
            reason="artifact identity does not match persisted artifact_id",
        )

    try:
        expected_payload_hash = sha256_fingerprint(envelope.payload)
    except Exception as exc:  # pragma: no cover - defensive
        raise ArtifactCorruptedError(
            artifact_id=validated_id,
            reason="payload hashing failed",
        ) from exc

    if expected_payload_hash != envelope.payload_hash:
        raise ArtifactCorruptedError(
            artifact_id=validated_id,
            reason="payload_hash does not match payload",
        )

    if not envelope.provenance_references:
        raise ArtifactCorruptedError(artifact_id=validated_id, reason="provenance_references is empty")

    for reference in envelope.provenance_references:
        if not all(
            [
                reference.kind,
                isinstance(reference.identifier, str) and reference.identifier,
                isinstance(reference.description, str) and reference.description,
                isinstance(reference.producer, str) and reference.producer,
                isinstance(reference.producer_version, str) and reference.producer_version,
            ]
        ):
            raise ArtifactCorruptedError(
                artifact_id=validated_id,
                reason="malformed provenance reference",
            )


def _serialize_artifact(envelope: ArtifactEnvelope) -> str:
    return json.dumps(
        envelope.model_dump(mode="json", exclude_none=False),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _deserialize_artifact(data: str) -> ArtifactEnvelope:
    try:
        return ArtifactEnvelope.model_validate_json(data)
    except Exception as exc:
        raise ArtifactCorruptedError(artifact_id="unknown", reason="invalid ArtifactEnvelope") from exc


class ArtifactStore(ABC):
    """Storage-backend independent artifact persistence contract."""

    @abstractmethod
    def put(self, artifact: ArtifactEnvelope) -> ArtifactEnvelope:
        """Persist an immutable artifact.

        Returns the persisted envelope on success.
        """

    @abstractmethod
    def get(self, artifact_id: str) -> ArtifactEnvelope:
        """Retrieve a verified artifact by ID."""

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """Return whether an artifact exists."""

    @abstractmethod
    def list_ids(self, filters: dict[str, Any] | None = None) -> tuple[str, ...]:
        """Return deterministic artifact IDs, optionally filtered."""

    def get_direct_dependencies(self, artifact_id: str) -> tuple[str, ...]:
        """Return direct provenance artifact identifiers for an artifact."""
        artifact = self.get(artifact_id)
        dependencies = []
        for reference in artifact.provenance_references:
            if reference.kind.value == "artifact":
                try:
                    _validate_artifact_id(reference.identifier)
                except InvalidArtifactIdError:
                    continue
                dependencies.append(reference.identifier)
        return tuple(dict.fromkeys(dependencies))


class InMemoryArtifactStore(ArtifactStore):
    """Thread-safe in-memory artifact store."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, str] = OrderedDict()
        self._lock = RLock()

    def put(self, artifact: ArtifactEnvelope) -> ArtifactEnvelope:
        with self._lock:
            artifact_id = artifact.artifact_id
            existing_serialized = self._entries.get(artifact_id)
            incoming_serialized = _serialize_artifact(artifact)
            if existing_serialized is not None:
                if existing_serialized != incoming_serialized:
                    raise ArtifactConflictError(artifact_id=artifact_id)
                return _deserialize_artifact(existing_serialized)

            _verify_integrity(artifact)
            self._entries[artifact_id] = incoming_serialized
            self._entries.move_to_end(artifact_id)
            return artifact

    def get(self, artifact_id: str) -> ArtifactEnvelope:
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


__all__ = [
    "ArtifactStore",
    "InMemoryArtifactStore",
]
