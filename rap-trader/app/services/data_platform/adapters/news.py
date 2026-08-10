"""Offline normalization of supplied news events; performs no retrieval."""

from __future__ import annotations

from app.domain.models.data_platform import DataDomain, EventRecord, NormalizedDataRecord
from app.services.data_platform.adapters._common import make_record


class NewsAdapter:
    def normalize(self, event: EventRecord) -> NormalizedDataRecord:
        observed = event.published_at or event.occurred_at or event.available_at
        return make_record(
            record_id=f"news.{event.event_id}",
            domain=DataDomain.NEWS,
            value={"headline": event.headline_or_title, "summary": event.summary},
            units="article",
            observed_at=observed,
            available_at=event.available_at,
            symbol_or_entity=event.entity,
            series_id=event.event_type.upper(),
            source=event.source,
            metadata={"importance": event.importance.value},
            revision_number=event.revision.revision_number,
        )

    def fetch(self, events: tuple[EventRecord, ...]) -> tuple[NormalizedDataRecord, ...]:
        return tuple(sorted((self.normalize(event) for event in events), key=lambda r: str(r.record_id)))

    get_records = fetch


__all__ = ["NewsAdapter"]
