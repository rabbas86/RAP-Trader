"""Deterministic synthetic normalized records for tests and research."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.models.data_platform import (
    DataAvailability,
    DataDomain,
    DataQuality,
    DataRecordId,
    DataRevision,
    DataSourceIdentity,
    NormalizedDataRecord,
)
from app.domain.models.market_data import _require_aware_utc
from app.services.data_platform.fingerprint import sha256_fingerprint


class MockAdapter:
    research_only = True
    suitable_for_live_trading = False

    def __init__(self, *, count: int = 10, domain: DataDomain = DataDomain.ALTERNATIVE, symbol_or_entity: str = "SYNTHETIC") -> None:
        if count < 0:
            raise ValueError("count must be non-negative")
        self.count, self.domain, self.symbol_or_entity = count, domain, symbol_or_entity

    def fetch(self, as_of: datetime) -> tuple[NormalizedDataRecord, ...]:
        as_of = _require_aware_utc(as_of)
        source = DataSourceIdentity(
            provider="deterministic_mock",
            dataset=self.domain.value,
            source_version="1",
            schema_version="1",
            offline_capable=True,
            authoritative=False,
        )
        quality = DataQuality(
            completeness=1.0,
            consistency=1.0,
            timeliness=1.0,
            source_reliability=0.75,
            anomaly_flags=(),
            warnings=("synthetic data",),
            score=0.9,
        )
        output = []
        for index in range(self.count):
            event_time = as_of - timedelta(days=self.count - index - 1)
            value = round(100 + index * 1.25, 4)
            fingerprint = sha256_fingerprint({"domain": self.domain, "entity": self.symbol_or_entity, "time": event_time, "value": value})
            record_id = DataRecordId(f"mock.{self.domain.value}.{self.symbol_or_entity}.{event_time:%Y%m%dT%H%M%SZ}")
            availability = DataAvailability(observed_at=event_time, available_at=event_time, ingested_at=event_time)
            revision = DataRevision(
                revision_id=f"{record_id}.r0",
                revision_number=0,
                revised_at=event_time,
                available_at=event_time,
                source_fingerprint=fingerprint,
            )
            output.append(
                NormalizedDataRecord(
                    record_id=record_id,
                    domain=self.domain,
                    symbol_or_entity=self.symbol_or_entity,
                    series_id="synthetic",
                    period_start=event_time,
                    period_end=event_time,
                    event_time=event_time,
                    value=value,
                    units="index",
                    availability=availability,
                    revision=revision,
                    source=source,
                    quality=quality,
                    source_fingerprint=fingerprint,
                    schema_version="1",
                    metadata={"synthetic": True},
                )
            )
        return tuple(output)

    get_records = fetch
    generate = fetch


__all__ = ["MockAdapter"]
