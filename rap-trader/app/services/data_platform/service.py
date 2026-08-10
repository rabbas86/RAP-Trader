"""Facade for the Phase 8A unified research data platform."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.domain.models.data_platform import (
    DataDomain,
    EventRecord,
    NormalizedDataRecord,
    ResearchDataSnapshot,
    SnapshotRequest,
)
from app.services.data_platform.registry import DataSourceRegistry
from app.services.data_platform.snapshot import ResearchDataSnapshotService
from app.services.data_platform.store import InMemoryDataRecordStore


class UnifiedResearchDataPlatformService:
    research_only = True
    suitable_for_live_trading = False

    def __init__(
        self,
        *,
        store: InMemoryDataRecordStore | None = None,
        registry: DataSourceRegistry | None = None,
        snapshots: ResearchDataSnapshotService | None = None,
    ) -> None:
        self.store = store or InMemoryDataRecordStore()
        self.registry = registry or DataSourceRegistry()
        self.snapshots = snapshots or ResearchDataSnapshotService()
        self._events: list[EventRecord] = []

    def ingest(self, records: NormalizedDataRecord | Iterable[NormalizedDataRecord]) -> tuple[NormalizedDataRecord, ...]:
        items = (records,) if isinstance(records, NormalizedDataRecord) else tuple(records)
        self.store.put_many(items)
        return items

    def add_events(self, events: EventRecord | Iterable[EventRecord]) -> tuple[EventRecord, ...]:
        items = (events,) if isinstance(events, EventRecord) else tuple(events)
        self._events.extend(items)
        return items

    def health(self) -> dict[str, str]:
        return {
            "status": "healthy",
            "platform_version": self.snapshots.platform_version,
            "source_count": str(len(self.registry.list())),
            "record_count": str(len(self.store)),
        }

    def sources(self) -> list[dict[str, str]]:
        return [
            {
                "provider": source.provider,
                "dataset": source.dataset,
                "source_version": source.source_version,
                "schema_version": source.schema_version,
                "offline_capable": str(source.offline_capable),
                "authoritative": str(source.authoritative),
            }
            for source in self.registry.list()
        ]

    def domains(self) -> list[str]:
        return sorted(domain.value for domain in DataDomain)

    def query_records(
        self,
        *,
        domain: DataDomain | None = None,
        symbol: str | None = None,
        series: str | None = None,
        as_of: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[NormalizedDataRecord, ...]:
        result = self.store.query(
            as_of=as_of,
            domains=(domain,) if domain is not None else None,
            symbol_or_entity=symbol if symbol is not None else None,
        )
        if series is not None:
            result = tuple(record for record in result if record.series_id == series)
        if limit is not None:
            result = result[:limit]
        return result

    def calendar_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        event_type: str | None = None,
    ) -> tuple[EventRecord, ...]:
        events = tuple(self._events)
        if start is not None:
            events = tuple(event for event in events if (event.occurred_at or event.available_at) >= start)
        if end is not None:
            events = tuple(event for event in events if (event.occurred_at or event.available_at) <= end)
        if event_type is not None:
            events = tuple(event for event in events if event.event_type == event_type)
        return tuple(sorted(events, key=lambda item: (str(item.event_id), str(item.occurred_at or item.available_at))))

    def snapshot(self, request: SnapshotRequest) -> ResearchDataSnapshot:
        records = self.store.query(
            as_of=request.as_of,
            domains=request.domains if request.domains else None,
        )
        if request.symbols:
            records = tuple(record for record in records if record.symbol_or_entity in request.symbols)
        if request.series_ids:
            records = tuple(record for record in records if record.series_id in request.series_ids)
        if request.max_records is not None and len(records) > request.max_records:
            records = records[: request.max_records]
            partial = True
        else:
            partial = False
        result = self.snapshots.create_snapshot(
            records,
            as_of=request.as_of,
            requested_domains=request.domains if request.domains else None,
        )
        if not partial:
            return result
        if not request.allow_partial:
            from app.domain.models.data_platform import DataPlatformError, SnapshotErrorCode

            raise DataPlatformError(SnapshotErrorCode.MAX_RECORDS_EXCEEDED, "Snapshot exceeds max_records")
        return result.model_copy(update={"partial": True, "warnings": (*result.warnings, "max_records_limited")})

    def create_snapshot(self, *, as_of: datetime, domains: Iterable[DataDomain | str] | None = None) -> ResearchDataSnapshot:
        selected = None if domains is None else tuple(domains)
        records = self.store.query(as_of=as_of, domains=selected)
        return self.snapshots.create_snapshot(records, as_of=as_of, requested_domains=selected)


DataPlatformService = UnifiedResearchDataPlatformService
__all__ = ["DataPlatformService", "UnifiedResearchDataPlatformService"]
