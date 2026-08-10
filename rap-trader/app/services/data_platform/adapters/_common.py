"""Shared construction helpers for offline adapters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.domain.models.data_platform import (
    DataAvailability,
    DataDomain,
    DataQuality,
    DataRecordId,
    DataRevision,
    DataSourceIdentity,
    NormalizedDataRecord,
)
from app.services.data_platform.fingerprint import sha256_fingerprint

GOOD_QUALITY = DataQuality(
    completeness=1.0, consistency=1.0, timeliness=1.0, source_reliability=1.0, anomaly_flags=(), warnings=(), score=1.0
)


def make_record(
    *,
    record_id: str,
    domain: DataDomain,
    value: Any,
    units: str,
    observed_at: datetime,
    available_at: datetime | None = None,
    symbol_or_entity: str | None = None,
    series_id: str | None = None,
    currency: str | None = None,
    source: DataSourceIdentity,
    metadata: dict[str, Any] | None = None,
    revision_number: int = 0,
) -> NormalizedDataRecord:
    available = available_at or observed_at
    fingerprint = sha256_fingerprint(
        {
            "record_id": record_id,
            "value": value,
            "observed_at": observed_at,
            "available_at": available,
            "source": source,
            "revision": revision_number,
        }
    )
    return NormalizedDataRecord(
        record_id=DataRecordId(record_id),
        domain=domain,
        symbol_or_entity=symbol_or_entity,
        series_id=series_id,
        period_start=None,
        period_end=None,
        event_time=observed_at,
        value=value,
        units=units,
        currency=currency,
        availability=DataAvailability(
            observed_at=observed_at,
            published_at=available,
            available_at=available,
            ingested_at=available,
            revised_at=available if revision_number else None,
            effective_from=None,
            effective_to=None,
        ),
        revision=DataRevision(
            revision_id=f"{record_id}.r{revision_number}",
            revision_number=revision_number,
            previous_revision_id=f"{record_id}.r{revision_number - 1}" if revision_number else None,
            revised_at=available,
            available_at=available,
            reason="revision" if revision_number else None,
            changed_fields=("value",) if revision_number else (),
            source_fingerprint=fingerprint,
        ),
        source=source,
        quality=GOOD_QUALITY,
        source_fingerprint=fingerprint,
        schema_version="1",
        metadata=metadata or {},
        research_only=True,
        suitable_for_live_trading=False,
    )
