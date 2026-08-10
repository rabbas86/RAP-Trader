"""Offline normalization for event-domain records."""

from __future__ import annotations

from app.domain.models.data_platform import DataDomain, EventRecord, NormalizedDataRecord
from app.services.data_platform.adapters._common import make_record


class EventsAdapter:
    def normalize(self, event: EventRecord) -> NormalizedDataRecord:
        event_type = event.event_type.lower()
        if event_type == "news":
            domain = DataDomain.NEWS
        elif event_type == "earnings":
            domain = DataDomain.EARNINGS
        elif "central" in event_type:
            domain = DataDomain.CENTRAL_BANK
        else:
            domain = DataDomain.CALENDAR
        observed = event.occurred_at or event.scheduled_at or event.published_at or event.available_at
        value = (
            {"headline": event.headline_or_title, "summary": event.summary, "payload": event.structured_payload}
            if domain is DataDomain.NEWS
            else {"title": event.headline_or_title, "summary": event.summary, "payload": event.structured_payload}
        )
        return make_record(
            record_id=f"event.{event.event_id}",
            domain=domain,
            value=value,
            units="event",
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


EventAdapter = EventsAdapter
__all__ = ["EventAdapter", "EventsAdapter"]
