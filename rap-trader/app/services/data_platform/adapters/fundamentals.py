"""Adapter for Phase 7 fundamental-domain inputs."""

from __future__ import annotations

from app.domain.models.data_platform import DataDomain, DataSourceIdentity, NormalizedDataRecord
from app.domain.models.fundamental import CompanyFundamentals, FundamentalMetric, FundamentalSnapshot
from app.services.data_platform.adapters._common import make_record


class FundamentalsAdapter:
    source = DataSourceIdentity(
        provider="phase7",
        dataset="fundamentals",
        source_version="7",
        schema_version="1",
        offline_capable=True,
        authoritative=False,
        metadata={},
    )

    def normalize(self, value: CompanyFundamentals | FundamentalSnapshot) -> tuple[NormalizedDataRecord, ...]:
        if isinstance(value, FundamentalSnapshot):
            return tuple(self._metric(value.symbol, metric) for metric in value.metrics if metric.available_at <= value.as_of)
        records: list[NormalizedDataRecord] = []
        statements: tuple[object, ...] = (*value.income_statements, *value.balance_sheets, *value.cash_flow_statements)
        for statement_object in statements:
            statement = statement_object
            if not hasattr(statement, "period") or not hasattr(statement, "model_dump"):
                continue
            period = statement.period
            for name, amount in statement.model_dump().items():
                if name == "period" or amount is None or isinstance(amount, bool) or not isinstance(amount, (int, float)):
                    continue
                identifier = f"fundamental.{value.symbol.upper()}.{name}.{int(period.period_end.timestamp())}"
                records.append(
                    make_record(
                        record_id=identifier,
                        domain=DataDomain.FUNDAMENTAL,
                        value=float(amount),
                        units="currency",
                        currency=period.currency.upper(),
                        observed_at=period.period_end,
                        available_at=period.available_at,
                        symbol_or_entity=value.symbol.upper(),
                        series_id=name.upper(),
                        source=self.source,
                        metadata={"audited": period.audited, "restated": period.restated, "period_type": period.period_type.value},
                    )
                )
        return tuple(sorted(records, key=lambda r: str(r.record_id)))

    def _metric(self, symbol: str, metric: FundamentalMetric) -> NormalizedDataRecord:
        period = metric.period_end or metric.available_at
        identifier = f"fundamental.{symbol.upper()}.{metric.metric_id}.{int(period.timestamp())}"
        return make_record(
            record_id=identifier,
            domain=DataDomain.FUNDAMENTAL,
            value=metric.value,
            units=metric.units,
            observed_at=period,
            available_at=metric.available_at,
            symbol_or_entity=symbol.upper(),
            series_id=metric.metric_id.upper(),
            source=self.source,
            metadata={"category": metric.category, "formula_version": metric.formula_version, "valid": metric.valid},
        )

    fetch = normalize
    get_records = normalize


__all__ = ["FundamentalsAdapter"]
