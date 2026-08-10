"""Credential-free local stores with atomic JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from threading import RLock

from app.domain.models.data_platform import DataDomain, NormalizedDataRecord


def _key(record: NormalizedDataRecord) -> str:
    return f"{record.record_id}:{record.revision.revision_number}:{record.source_fingerprint}"


class InMemoryDataRecordStore:
    def __init__(self, records: Iterable[NormalizedDataRecord] = ()) -> None:
        self._records: dict[str, NormalizedDataRecord] = {}
        self._lock = RLock()
        for record in records:
            InMemoryDataRecordStore.put(self, record)

    def put(self, record: NormalizedDataRecord) -> None:
        with self._lock:
            self._records[_key(record)] = record

    save = put

    def put_many(self, records: Iterable[NormalizedDataRecord]) -> None:
        for record in records:
            self.put(record)

    save_many = put_many

    def list(self) -> tuple[NormalizedDataRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda record: (str(record.record_id), record.revision.revision_number, record.source_fingerprint),
                )
            )

    all = list

    def get(self, record_id: str) -> NormalizedDataRecord | None:
        matches = [record for record in self.list() if str(record.record_id) == str(record_id)]
        return max(matches, key=lambda record: (record.availability.available_at, record.revision.revision_number), default=None)

    def query(
        self, *, as_of: datetime | None = None, domains: Iterable[DataDomain | str] | None = None, symbol_or_entity: str | None = None
    ) -> tuple[NormalizedDataRecord, ...]:
        wanted = None if domains is None else {DataDomain(item) for item in domains}
        return tuple(
            record
            for record in self.list()
            if (as_of is None or record.availability.available_at <= as_of)
            and (wanted is None or record.domain in wanted)
            and (symbol_or_entity is None or record.symbol_or_entity == symbol_or_entity)
        )

    records_as_of = query

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


class JSONFileDataRecordStore(InMemoryDataRecordStore):
    def __init__(self, root: str | Path, filename: str = "records.json") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = (self.root / filename).resolve()
        if self.path == self.root or self.root not in self.path.parents:
            raise ValueError("store path escapes configured root")
        if self.path.suffix.lower() != ".json":
            raise ValueError("JSON store filename must end in .json")
        InMemoryDataRecordStore.__init__(self)
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise TypeError("data record store must contain a JSON array")
        for item in payload:
            InMemoryDataRecordStore.put(self, NormalizedDataRecord.model_validate_json(json.dumps(item, separators=(",", ":"))))

    def _persist(self) -> None:
        payload = [record.model_dump(mode="json") for record in self.list()]
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def put(self, record: NormalizedDataRecord) -> None:
        with self._lock:
            InMemoryDataRecordStore.put(self, record)
            self._persist()

    save = put

    def put_many(self, records: Iterable[NormalizedDataRecord]) -> None:
        with self._lock:
            for record in records:
                InMemoryDataRecordStore.put(self, record)
            self._persist()

    save_many = put_many


DataRecordStore = InMemoryDataRecordStore
__all__ = ["DataRecordStore", "InMemoryDataRecordStore", "JSONFileDataRecordStore"]
