"""Smoke test for the data platform revision engine."""

from datetime import UTC, datetime

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
from app.services.data_platform.revisions import PointInTimeRevisionService


def _make_record(value: float, revision_number: int, available_at: datetime) -> NormalizedDataRecord:
    source = DataSourceIdentity(provider="mock", dataset="test", source_version="1", schema_version="1")
    availability = DataAvailability(
        observed_at=available_at,
        available_at=available_at,
        ingested_at=available_at,
    )
    fingerprint = sha256_fingerprint({"v": value, "r": revision_number})
    revision = DataRevision(
        revision_id=f"r{revision_number}",
        revision_number=revision_number,
        previous_revision_id=f"r{revision_number - 1}" if revision_number > 0 else None,
        revised_at=available_at,
        available_at=available_at,
        source_fingerprint=fingerprint,
    )
    return NormalizedDataRecord(
        record_id=DataRecordId("test.1"),
        domain=DataDomain.MACRO,
        value=value,
        units="percent",
        availability=availability,
        revision=revision,
        source=source,
        quality=DataQuality(completeness=1.0, consistency=1.0, timeliness=1.0, score=1.0),
        source_fingerprint=fingerprint,
        schema_version="1",
    )


def test_revision_selection():
    r1 = _make_record(3.1, 0, datetime(2026, 2, 10, tzinfo=UTC))
    r2 = _make_record(3.0, 1, datetime(2026, 3, 15, tzinfo=UTC))
    service = PointInTimeRevisionService()
    # Feb 20 -> 3.1
    assert service.select([r1, r2], datetime(2026, 2, 20, tzinfo=UTC)).value == 3.1
    # Apr 1 -> 3.0
    assert service.select([r1, r2], datetime(2026, 4, 1, tzinfo=UTC)).value == 3.0
    # Future revision rejection
    try:
        service.select([r2], datetime(2026, 2, 1, tzinfo=UTC))
        assert False, "should have raised"
    except ValueError:
        pass
    print("Revision engine test passed!")


if __name__ == "__main__":
    test_revision_selection()
