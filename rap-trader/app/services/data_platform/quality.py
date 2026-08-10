"""Deterministic quality checks for normalized research data."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from itertools import pairwise
from math import isfinite
from typing import Any

from app.domain.models.data_platform import DataQuality, NormalizedDataRecord
from app.domain.models.market_data import _require_aware_utc
from app.services.data_platform.normalization import DataNormalizationService


class DataQualityService:
    def assess_record(
        self, record: NormalizedDataRecord, *, as_of: datetime | None = None, stale_after_seconds: int | None = None
    ) -> DataQuality:
        flags: list[str] = []
        warnings: list[str] = []
        missing = sum(x is None for x in (record.symbol_or_entity, record.series_id, record.period_start, record.event_time))
        if record.value is None:
            flags.append("missing_value")
        if isinstance(record.value, (int, float)) and (not isfinite(float(record.value))):
            flags.append("impossible_value")
        if record.period_start and record.period_end and record.period_start > record.period_end:
            flags.append("chronology_problem")
        if record.revision.revision_number > 0 and not record.revision.previous_revision_id:
            flags.append("revision_anomaly")
        timeliness = 1.0
        if as_of is not None:
            cutoff = _require_aware_utc(as_of)
            if record.availability.available_at > cutoff:
                flags.append("chronology_problem")
                timeliness = 0.0
            elif stale_after_seconds is not None:
                age = (cutoff - record.availability.available_at).total_seconds()
                timeliness = max(0.0, 1.0 - age / max(1, stale_after_seconds))
                if age > stale_after_seconds:
                    warnings.append("stale_record")
        completeness = max(0.0, 1.0 - (missing + (record.value is None)) / 5)
        consistency = max(0.0, 1.0 - 0.2 * len(flags))
        score = round((completeness + consistency + timeliness + 1.0) / 4, 6)
        return DataQuality(
            completeness=completeness,
            consistency=consistency,
            timeliness=round(timeliness, 6),
            source_reliability=1.0,
            anomaly_flags=tuple(sorted(set(flags))),
            warnings=tuple(sorted(set(warnings))),
            score=score,
        )

    assess = assess_record

    def assess_records(
        self,
        records: Iterable[NormalizedDataRecord],
        *,
        as_of: datetime | None = None,
        expected_gap_seconds: int | None = None,
        stale_after_seconds: int | None = None,
    ) -> dict[str, tuple[str, ...]]:
        items = tuple(records)
        findings: dict[str, list[str]] = defaultdict(list)
        for record in items:
            quality = self.assess_record(record, as_of=as_of, stale_after_seconds=stale_after_seconds)
            findings[str(record.record_id)].extend(quality.anomaly_flags + quality.warnings)
        for first, second in DataNormalizationService().find_duplicates(items):
            findings[first].append("duplicate_record")
            findings[second].append("duplicate_record")
        groups: dict[tuple[Any, ...], list[NormalizedDataRecord]] = defaultdict(list)
        for record in items:
            groups[(record.domain, record.symbol_or_entity, record.series_id)].append(record)
        for group in groups.values():
            units = {r.units for r in group}
            if len(units) > 1:
                for record in group:
                    findings[str(record.record_id)].append("inconsistent_units")
            by_time = sorted(group, key=lambda r: r.event_time or r.period_end or r.availability.observed_at)
            if expected_gap_seconds is not None:
                for left, right in pairwise(by_time):
                    lt = left.event_time or left.period_end or left.availability.observed_at
                    rt = right.event_time or right.period_end or right.availability.observed_at
                    if (rt - lt).total_seconds() > expected_gap_seconds:
                        findings[str(right.record_id)].append("unexpected_gap")
            by_observation = defaultdict(list)
            for record in group:
                by_observation[record.availability.observed_at].append(record)
            for versions in by_observation.values():
                values = Counter(repr(r.value) for r in versions if r.revision.revision_number == 0)
                if len(values) > 1:
                    for record in versions:
                        findings[str(record.record_id)].append("source_conflict")
        return {key: tuple(sorted(set(value))) for key, value in sorted(findings.items())}

    evaluate = assess_records


QualityService = DataQualityService
__all__ = ["DataQualityService", "QualityService"]
