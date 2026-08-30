"""Decision journal service."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from threading import RLock
from typing import Any

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType
from app.domain.models.market_data import Symbol
from app.services.artifacts.base import ArtifactStore
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError
from app.services.decision_journal.entry import DecisionJournalEntry
from app.services.decision_journal.errors import (
    DecisionJournalEntryNotFoundError,
    DecisionJournalQueryError,
    DecisionJournalValidationError,
)

INDEXED_FIELDS = (
    "symbol",
    "direction",
    "decision_at",
    "research_run_id",
    "decision_artifact_id",
    "decision_run_manifest_id",
    "logical_as_of",
)


class DecisionJournalService:
    """Immutable durable journal of finalized decisions."""

    def __init__(self, store: ArtifactStore) -> None:
        self.store: ArtifactStore = store
        self._index: OrderedDict[str, str] = OrderedDict()
        self._secondary_indexes: dict[str, OrderedDict[str, list[str]]] = {field: OrderedDict() for field in INDEXED_FIELDS}
        self._lock = RLock()
        self._rebuild_indexes()

    def record_entry(self, entry: DecisionJournalEntry) -> ArtifactEnvelope:
        if not isinstance(entry, DecisionJournalEntry):
            raise TypeError("entry must be a DecisionJournalEntry")
        self._load_verified_artifact(entry.decision_artifact_id, ArtifactType.TRADE_DECISION, "decision artifact")
        manifest_envelope = self._load_verified_artifact(
            entry.decision_run_manifest_id, ArtifactType.DECISION_RUN_MANIFEST, "decision run manifest"
        )
        manifest_payload = manifest_envelope.payload if isinstance(manifest_envelope.payload, dict) else {}
        terminal_artifact_id = manifest_payload.get("terminal_artifact_id")
        research_run_id = manifest_payload.get("research_run_id")
        if terminal_artifact_id != entry.decision_artifact_id:
            raise DecisionJournalValidationError("manifest terminal artifact does not match decision artifact")
        if research_run_id != entry.research_run_id:
            raise DecisionJournalValidationError("manifest research_run_id does not match journal entry")
        expected_graph_fingerprint = self._manifest_graph_fingerprint(manifest_payload)
        if expected_graph_fingerprint != entry.graph_fingerprint:
            raise DecisionJournalValidationError("graph fingerprint does not match decision run manifest")
        persisted = self.store.put(entry.envelope())
        with self._lock:
            self._index[entry.decision_artifact_id] = persisted.artifact_id
            for field in INDEXED_FIELDS:
                value = self._index_value(entry, field)
                index = self._secondary_indexes[field]
                index.setdefault(value, []).append(persisted.artifact_id)
        return persisted

    def get_entry(self, decision_artifact_id: str) -> DecisionJournalEntry:
        envelope = self._load_entry_envelope(decision_artifact_id)
        return DecisionJournalEntry.model_validate(envelope.payload)

    def get_entry_envelope(self, decision_artifact_id: str) -> ArtifactEnvelope:
        return self._load_entry_envelope(decision_artifact_id)

    def _load_entry_envelope(self, decision_artifact_id: str) -> ArtifactEnvelope:
        with self._lock:
            if decision_artifact_id not in self._index:
                raise DecisionJournalEntryNotFoundError(decision_artifact_id)
            artifact_id = self._index[decision_artifact_id]
        return self.store.get(artifact_id)

    def query(self, **filters: Any) -> list[DecisionJournalEntry]:
        self._validate_filters(filters)
        with self._lock:
            candidates = self._candidate_ids(filters)

        entries: list[DecisionJournalEntry] = []
        for artifact_id in candidates:
            envelope = self.store.get(artifact_id)
            entry = DecisionJournalEntry.model_validate(envelope.payload)
            if self._entry_matches_filters(entry, filters):
                entries.append(entry)
        return entries

    def _validate_filters(self, filters: dict[str, Any]) -> None:
        unknown = sorted(set(filters) - set(INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise DecisionJournalQueryError(f"unsupported filters: {unknown}")

    def _candidate_ids(self, filters: dict[str, Any]) -> list[str]:
        candidates = None
        for field, value in filters.items():
            if field == "limit":
                continue
            index = self._secondary_indexes[field]
            normalized = self._index_value_for_filter(value)
            ids = index.get(normalized, [])
            if candidates is None:
                candidates = OrderedDict.fromkeys(ids)
            else:
                candidates = OrderedDict.fromkeys(candidate for candidate in candidates if candidate in ids)
        return list(candidates or self._index.values())

    def _index_value_for_filter(self, value: Any) -> str:
        if isinstance(value, Symbol):
            return value.root
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _entry_matches_filters(self, entry: DecisionJournalEntry, filters: dict[str, Any]) -> bool:
        return all(
            self._index_value(entry, field) == self._index_value_for_filter(value) for field, value in filters.items() if field != "limit"
        )

    def _index_value(self, entry: DecisionJournalEntry, field: str) -> str:
        value = getattr(entry, field)
        if isinstance(value, Symbol):
            return value.root
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _load_verified_artifact(self, artifact_id: str, expected_type: ArtifactType, label: str) -> ArtifactEnvelope:
        try:
            envelope = self.store.get(artifact_id)
        except ArtifactNotFoundError as exc:
            raise DecisionJournalValidationError(f"{label} not found: {exc.artifact_id}") from exc
        except ArtifactCorruptedError as exc:
            raise DecisionJournalValidationError(f"{label} is corrupted: {exc.reason}") from exc
        if envelope.artifact_type != expected_type:
            raise DecisionJournalValidationError(f"{label} has wrong artifact type: {envelope.artifact_type.value}")
        return envelope

    def _manifest_graph_fingerprint(self, manifest_payload: dict[str, Any]) -> str:
        material = {
            "manifest_schema_version": manifest_payload.get("manifest_schema_version"),
            "research_run_id": manifest_payload.get("research_run_id"),
            "terminal_artifact_id": manifest_payload.get("terminal_artifact_id"),
            "logical_as_of": manifest_payload.get("logical_as_of"),
            "ordered_graph_nodes": list(manifest_payload.get("ordered_graph_nodes", [])),
            "ordered_graph_edges": [list(edge) for edge in manifest_payload.get("ordered_graph_edges", [])],
            "root_artifact_ids": list(manifest_payload.get("root_artifact_ids", [])),
            "producer_version": manifest_payload.get("producer_version"),
        }
        return sha256_fingerprint(material)

    def _rebuild_indexes(self) -> None:
        for artifact_id in self.store.list_ids(filters={"artifact_type": ArtifactType.DECISION_JOURNAL_ENTRY}):
            envelope = self.store.get(artifact_id)
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            decision_artifact_id = payload.get("decision_artifact_id")
            if not isinstance(decision_artifact_id, str) or not decision_artifact_id:
                raise DecisionJournalValidationError("journal payload is missing decision_artifact_id")
            self._index[decision_artifact_id] = artifact_id
            for field in INDEXED_FIELDS:
                value = payload.get(field)
                if value is None:
                    continue
                self._secondary_indexes[field].setdefault(value, []).append(artifact_id)


__all__ = ["DecisionJournalService"]
