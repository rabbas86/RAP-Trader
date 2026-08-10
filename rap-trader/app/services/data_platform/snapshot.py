"""Immutable deterministic point-in-time snapshot assembly."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from app.domain.models.data_platform import DataDomain, NormalizedDataRecord, QualitySummary, ResearchDataSnapshot, SnapshotProvenance
from app.domain.models.market_data import _require_aware_utc
from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.data_platform.revisions import PointInTimeRevisionService
from app.services.data_platform.validation import DataValidationService


def _logical_key(record: NormalizedDataRecord) -> tuple[str, ...]:
    times = tuple("" if value is None else value.isoformat() for value in (record.period_start, record.period_end, record.event_time))
    return (record.domain.value, record.symbol_or_entity or "", record.series_id or "", *times)


class ResearchDataSnapshotService:
    def __init__(self, *, schema_version: str = "1", platform_version: str = "8A") -> None:
        self.schema_version, self.platform_version = schema_version, platform_version
        self.revisions, self.validation = PointInTimeRevisionService(), DataValidationService()

    def create_snapshot(
        self,
        records: Iterable[NormalizedDataRecord],
        *,
        as_of: datetime,
        requested_domains: Iterable[DataDomain | str] | None = None,
        created_at: datetime | None = None,
    ) -> ResearchDataSnapshot:
        as_of = _require_aware_utc(as_of)
        domains = (
            tuple(sorted({DataDomain(item) for item in requested_domains}, key=lambda item: item.value))
            if requested_domains is not None
            else ()
        )
        grouped: dict[tuple[str, ...], list[NormalizedDataRecord]] = defaultdict(list)
        for record in records:
            if record.availability.available_at <= as_of and (not domains or record.domain in domains):
                grouped[_logical_key(record)].append(record)
        chosen = (self.revisions.select(group, as_of) for group in grouped.values())
        ordered = tuple(
            sorted(
                (item for item in chosen if isinstance(item, NormalizedDataRecord)),
                key=lambda item: (_logical_key(item), item.revision.revision_number, str(item.record_id)),
            )
        )
        self.validation.validate_records(ordered, as_of=as_of)
        if not domains:
            domains = tuple(sorted({record.domain for record in ordered}, key=lambda item: item.value))
        fingerprints = tuple(sorted(record.source_fingerprint for record in ordered))
        source_versions = dict(
            sorted({f"{record.source.provider}:{record.source.dataset}": record.source.source_version for record in ordered}.items())
        )
        identity = {
            "as_of": as_of,
            "domains": domains,
            "fingerprints": fingerprints,
            "schema_version": self.schema_version,
            "platform_version": self.platform_version,
        }
        snapshot_id, timestamp = sha256_fingerprint(identity), as_of if created_at is None else _require_aware_utc(created_at)
        scores = [record.quality.score for record in ordered]
        summary = QualitySummary(
            total_records=len(ordered),
            average_score=round(sum(scores) / len(scores), 6) if scores else 0.0,
            records_with_warnings=sum(bool(record.quality.warnings or record.quality.anomaly_flags) for record in ordered),
            domains_represented=tuple(sorted({record.domain for record in ordered}, key=lambda item: item.value)),
        )
        provenance = SnapshotProvenance(
            snapshot_id=snapshot_id,
            as_of=as_of,
            created_at=timestamp,
            source_versions=source_versions,
            input_fingerprints=fingerprints,
            schema_version=self.schema_version,
            platform_version=self.platform_version,
        )
        return ResearchDataSnapshot(
            snapshot_id=snapshot_id,
            as_of=as_of,
            requested_domains=domains,
            records=ordered,
            source_versions=source_versions,
            schema_version=self.schema_version,
            platform_version=self.platform_version,
            created_at=timestamp,
            input_fingerprints=fingerprints,
            quality_summary=summary,
            warnings=(),
            partial=False,
            provenance=provenance,
        )

    build = create_snapshot
    create = create_snapshot
    snapshot = create_snapshot


SnapshotService = ResearchDataSnapshotService
__all__ = ["ResearchDataSnapshotService", "SnapshotService"]
