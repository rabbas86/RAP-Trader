"""Point-in-time and record validation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.domain.models.data_platform import NormalizedDataRecord
from app.domain.models.market_data import _require_aware_utc


class DataValidationService:
    def validate_record(self, record: NormalizedDataRecord, *, as_of: datetime | None = None) -> NormalizedDataRecord:
        if as_of is not None and record.availability.available_at > _require_aware_utc(as_of):
            raise ValueError(f"record {record.record_id} is not available as of the requested time")
        if not record.research_only or record.suitable_for_live_trading:
            raise ValueError("record must be research-only")
        return record

    def validate_records(
        self, records: Iterable[NormalizedDataRecord], *, as_of: datetime | None = None
    ) -> tuple[NormalizedDataRecord, ...]:
        return tuple(self.validate_record(record, as_of=as_of) for record in records)

    ensure_point_in_time_safe = validate_records


ValidationService = DataValidationService
__all__ = ["DataValidationService", "ValidationService"]
