"""Provenance construction without credentials or environment details."""

from __future__ import annotations

from datetime import datetime

from app.domain.models.data_platform import DataAvailability, DataRevision, DataSourceIdentity


class DataProvenanceService:
    def source(
        self, *, provider: str, dataset: str, source_version: str = "1", schema_version: str = "1", **kwargs: object
    ) -> DataSourceIdentity:
        return DataSourceIdentity.model_validate(
            {"provider": provider, "dataset": dataset, "source_version": source_version, "schema_version": schema_version, **kwargs},
            strict=True,
        )

    def availability(
        self, *, observed_at: datetime, available_at: datetime, ingested_at: datetime | None = None, **kwargs: object
    ) -> DataAvailability:
        return DataAvailability.model_validate(
            {"observed_at": observed_at, "available_at": available_at, "ingested_at": ingested_at or available_at, **kwargs}, strict=True
        )

    def revision(
        self, *, revision_id: str, available_at: datetime, source_fingerprint: str, revision_number: int = 0, **kwargs: object
    ) -> DataRevision:
        return DataRevision.model_validate(
            {
                "revision_id": revision_id,
                "revision_number": revision_number,
                "revised_at": available_at,
                "available_at": available_at,
                "source_fingerprint": source_fingerprint,
                **kwargs,
            },
            strict=True,
        )


ProvenanceService = DataProvenanceService
__all__ = ["DataProvenanceService", "ProvenanceService"]
