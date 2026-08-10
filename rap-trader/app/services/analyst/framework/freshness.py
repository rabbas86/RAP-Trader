"""Canonical analyst evidence freshness assessment."""

from datetime import datetime, timedelta
from typing import ClassVar

from app.domain.models.analyst import DataFreshness, EvidenceType


class DataFreshnessService:
    THRESHOLDS: ClassVar[dict[EvidenceType, timedelta]] = {
        EvidenceType.MARKET_DATA: timedelta(days=1),
        EvidenceType.TECHNICAL_INDICATOR: timedelta(days=1),
        EvidenceType.NEWS: timedelta(days=1),
        EvidenceType.SENTIMENT: timedelta(days=1),
        EvidenceType.FORECAST: timedelta(days=1),
        EvidenceType.MACROECONOMIC: timedelta(days=7),
        EvidenceType.CENTRAL_BANK: timedelta(days=7),
        EvidenceType.FINANCIAL_STATEMENT: timedelta(days=120),
        EvidenceType.REGULATORY_FILING: timedelta(days=120),
        EvidenceType.VALUATION: timedelta(days=30),
        EvidenceType.BACKTEST: timedelta(days=30),
        EvidenceType.MODEL_OUTPUT: timedelta(days=1),
        EvidenceType.RISK: timedelta(days=1),
        EvidenceType.PORTFOLIO: timedelta(days=1),
        EvidenceType.EXPERT_ASSUMPTION: timedelta(days=30),
        EvidenceType.OTHER: timedelta(days=7),
    }

    def threshold(self, evidence_type: EvidenceType) -> timedelta:
        return self.THRESHOLDS[evidence_type]

    def assess(self, observed_at: datetime, available_at: datetime, evaluated_at: datetime, evidence_type: EvidenceType) -> DataFreshness:
        age = max(0.0, (evaluated_at - observed_at).total_seconds())
        threshold = self.threshold(evidence_type)
        return DataFreshness(
            observed_at=observed_at,
            available_at=available_at,
            evaluated_at=evaluated_at,
            stale_threshold=threshold,
            is_stale=age > threshold.total_seconds(),
            age_seconds=age,
        )
