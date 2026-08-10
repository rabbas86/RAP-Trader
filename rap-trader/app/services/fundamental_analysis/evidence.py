"""Conversion of financial findings to shared Phase 5 evidence contracts."""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisLimitation,
    AnalysisWarning,
    Assumption,
    EvidenceItem,
    EvidenceStrength,
    EvidenceType,
    ProvenanceRecord,
)
from app.domain.models.fundamental import FundamentalMetric


class FundamentalEvidenceFactory:
    CATEGORIES = frozenset(
        {
            "growth",
            "profitability",
            "cash_flow",
            "balance_sheet",
            "capital_efficiency",
            "valuation",
            "earnings_quality",
            "shareholder",
            "data_quality",
        }
    )

    def create(self, item: FundamentalMetric, evaluated_at: datetime, source: str) -> EvidenceItem:
        evidence_type = EvidenceType.VALUATION if item.category == "valuation" else EvidenceType.FINANCIAL_STATEMENT
        observed = min(item.period_end or item.available_at, evaluated_at)
        available = min(item.available_at, evaluated_at)
        warnings = [AnalysisWarning(code="FUNDAMENTAL_WARNING", message=text) for text in item.warnings]
        return EvidenceItem(
            evidence_id=str(uuid5(NAMESPACE_URL, f"fundamental|{item.metric_id}|{item.source_fingerprint}|{evaluated_at.isoformat()}")),
            evidence_type=evidence_type,
            observed_at=observed,
            available_at=available,
            evaluated_at=evaluated_at,
            valid_until=evaluated_at + timedelta(days=1),
            strength=EvidenceStrength.MODERATE if not item.warnings else EvidenceStrength.WEAK,
            summary=f"{item.category}: {item.name}={item.value:.6g} {item.units}",
            confidence=0.6 if not item.warnings else 0.45,
            calibration_status="uncalibrated deterministic formula",
            has_historical_calibration=False,
            source_analyst="fundamental",
            assumptions=[Assumption(description=x) for x in item.assumptions],
            warnings=warnings,
            limitations=[AnalysisLimitation(code="HISTORICAL_FINANCIALS", message="Historical financial relationships may not persist")],
            provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
        )

    def build(self, metrics: list[FundamentalMetric], evaluated_at: datetime, source: str) -> list[EvidenceItem]:
        return [self.create(item, evaluated_at, source) for item in metrics if item.category in self.CATEGORIES]
