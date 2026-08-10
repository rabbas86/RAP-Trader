"""Canonical normalization primitives and duplicate detection."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.domain.models.data_platform import NormalizedDataRecord
from app.domain.models.market_data import _require_aware_utc

_UNIT_ALIASES = {
    "$": "currency",
    "usd": "currency",
    "%": "percent",
    "pct": "percent",
    "percentage": "percent",
    "shares": "shares",
    "share": "shares",
}


class DataNormalizationService:
    def timestamp(self, value: datetime) -> datetime:
        return _require_aware_utc(value)

    canonical_timestamp = timestamp

    def currency(self, value: str | None) -> str | None:
        if value is None:
            return None
        result = value.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", result):
            raise ValueError("currency must be a three-letter ISO-style code")
        return result

    canonical_currency = currency

    def units(self, value: str) -> str:
        result = value.strip().lower().replace(" ", "_")
        if not result:
            raise ValueError("units cannot be empty")
        return _UNIT_ALIASES.get(result, result)

    canonical_units = units

    def symbol(self, value: str) -> str:
        result = value.strip().upper().replace("-", ".")
        if not re.fullmatch(r"[A-Z0-9]+(?:\.[A-Z0-9]+)*", result):
            raise ValueError("invalid canonical symbol")
        return result

    canonical_symbol = symbol

    def entity(self, value: str) -> str:
        result = " ".join(value.strip().split())
        if not result:
            raise ValueError("entity cannot be empty")
        return result

    canonical_entity = entity

    def signed_value(self, value: float, *, convention: str = "as_reported") -> int | float:
        if convention in {"outflow_negative", "expense_positive"}:
            return -abs(value) if convention == "outflow_negative" else abs(value)
        if convention != "as_reported":
            raise ValueError("unknown sign convention")
        return value

    normalize_sign = signed_value

    def duplicate_key(self, record: NormalizedDataRecord) -> tuple[Any, ...]:
        return (
            record.domain.value,
            record.symbol_or_entity,
            record.series_id,
            record.period_start,
            record.period_end,
            record.event_time,
            record.revision.revision_number,
        )

    def find_duplicates(self, records: Iterable[NormalizedDataRecord]) -> tuple[tuple[str, str], ...]:
        seen: dict[tuple[Any, ...], str] = {}
        duplicates: list[tuple[str, str]] = []
        for record in records:
            key = self.duplicate_key(record)
            if key in seen:
                duplicates.append((seen[key], str(record.record_id)))
            else:
                seen[key] = str(record.record_id)
        return tuple(sorted(duplicates))

    detect_duplicates = find_duplicates


NormalizationService = DataNormalizationService
__all__ = ["DataNormalizationService", "NormalizationService"]
