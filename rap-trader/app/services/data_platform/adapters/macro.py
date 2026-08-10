"""Offline deterministic macroeconomic observations."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.models.data_platform import DataDomain, DataSourceIdentity, NormalizedDataRecord
from app.services.data_platform.adapters._common import make_record

SERIES: dict[str, tuple[float, str]] = {
    "CPI": (3.2, "percent"),
    "PCE": (2.8, "percent"),
    "UNEMPLOYMENT": (4.1, "percent"),
    "PAYROLLS": (175_000.0, "persons"),
    "GDP": (2.4, "percent"),
    "PMI": (51.5, "index"),
    "POLICY_RATE": (5.25, "percent"),
    "YIELDS": (4.25, "percent"),
    "CREDIT_SPREAD": (1.2, "percent"),
    "MONEY_SUPPLY": (21_000.0, "usd_billions"),
    "INDUSTRIAL_PRODUCTION": (102.1, "index"),
    "RETAIL_SALES": (710.0, "usd_billions"),
}


class MacroAdapter:
    source = DataSourceIdentity(
        provider="deterministic_mock",
        dataset="macro",
        source_version="1",
        schema_version="1",
        offline_capable=True,
        authoritative=False,
        metadata={},
    )

    def fetch(self, *, as_of: datetime, series_ids: tuple[str, ...] | None = None) -> tuple[NormalizedDataRecord, ...]:
        at = as_of.astimezone(UTC)
        requested = tuple(sorted(series_ids or tuple(SERIES)))
        unknown = set(requested) - SERIES.keys()
        if unknown:
            raise ValueError(f"unknown macro series: {', '.join(sorted(unknown))}")
        period_key = at.strftime("%Y%m")
        return tuple(
            make_record(
                record_id=f"macro.{series.lower()}.{period_key}",
                domain=DataDomain.MACRO,
                value=SERIES[series][0],
                units=SERIES[series][1],
                observed_at=at,
                available_at=at,
                symbol_or_entity="US",
                series_id=series,
                source=self.source,
                metadata={"synthetic": True},
            )
            for series in requested
        )

    get_records = fetch


__all__ = ["SERIES", "MacroAdapter"]
