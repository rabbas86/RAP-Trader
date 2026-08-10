"""Conversion of news clusters into shared Phase 5 evidence contracts.

``NewsEvidenceFactory`` turns ``EventCluster`` objects into ``EvidenceItem``
records suitable for the Phase 5 analyst-opinion lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisLimitation,
    AnalysisWarning,
    Assumption,
    EvidenceItem,
    EvidenceStrength,
    ProvenanceRecord,
)
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import (
    CONFIRMATION_CATEGORY,
    DATA_QUALITY_CATEGORY,
    NEWS_EVIDENCE_TYPE,
    NOVELTY_CATEGORY,
    SOURCE_QUALITY_CATEGORY,
    ConfirmationStatus,
    NewsImportance,
    NewsOrientation,
    NewsScope,
    SourceQuality,
)
from app.services.news_analysis.event_grouping import EventCluster


@dataclass(frozen=True)
class EvidenceSpec:
    """A specification for a single evidence item derived from a cluster."""

    cluster: EventCluster
    evaluated_at: datetime
    source: str
    observation: Any  # NewsObservation


class NewsEvidenceFactory:
    """Build Phase-5 ``EvidenceItem`` objects from news event clusters."""

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    def create(self, cluster: EventCluster, evaluated_at: datetime, source: str) -> EvidenceItem:
        """Build a single EvidenceItem from a cluster (summary-level)."""
        latest = cluster.records[-1] if cluster.records else None
        if latest is None:
            observed = evaluated_at
            available = evaluated_at
        else:
            observed = latest.occurred_at or latest.available_at
            available = latest.available_at

        # Point-in-time safety: clamp observed/available to evaluated_at.
        observed = min(observed, evaluated_at)
        available = min(available, evaluated_at)

        # Build summary with category prefix for framework compatibility.
        category = self._category_for_cluster(cluster)
        importance = cluster_records_importance(cluster)
        orientation = cluster_orientation(cluster)
        summary = self._build_summary(category, cluster, importance, orientation)

        # Strength from confidence penalty and importance.
        base_confidence = self.config.base_evidence_confidence
        penalty = cluster.confidence_penalty
        confidence = max(0.0, min(1.0, base_confidence * (1.0 - penalty)))

        strength = self._strength_for_importance(importance, confidence)

        evidence_id = str(uuid5(NAMESPACE_URL, f"news|{cluster.cluster_id}|{evaluated_at.isoformat()}"))

        # Warnings and limitations.
        warnings = self._warnings(cluster)
        limitations = [
            AnalysisLimitation(
                code="NEWS_LIMITATION",
                message="News orientation is based on deterministic rules and structured metadata; may not capture nuance",
            ),
            AnalysisLimitation(
                code="NO_TRADING",
                message="News analyst output does not generate trades or allocate capital",
            ),
        ]
        if cluster.confirmation_status is ConfirmationStatus.UNVERIFIED:
            warnings.append(AnalysisWarning(code="UNVERIFIED_SOURCE", message="Event reported by a single source"))
        if cluster.novelty_score < 0.5:
            warnings.append(AnalysisWarning(code="LOW_NOVELTY", message="Event is a follow-up with limited new information"))

        return EvidenceItem(
            evidence_id=evidence_id,
            evidence_type=NEWS_EVIDENCE_TYPE,
            observed_at=observed,
            available_at=available,
            evaluated_at=evaluated_at,
            valid_until=evaluated_at + timedelta(days=3),
            strength=strength,
            summary=summary,
            confidence=round(confidence, 6),
            capped=False,
            calibration_status="uncalibrated deterministic news rules",
            has_historical_calibration=False,
            source_analyst="news",
            assumptions=[
                Assumption(description="News events are point-in-time deterministic structured records from the Research Data Platform"),
                Assumption(description="Event orientation is derived deterministically from structured payload fields and keyword rules"),
            ],
            warnings=warnings,
            limitations=limitations,
            provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
        )

    def build(
        self,
        clusters: list[EventCluster],
        evaluated_at: datetime,
        source: str,
    ) -> list[EvidenceItem]:
        """Build evidence items — one per cluster for the main evidence, plus
        supplementary items for source quality, confirmation, and novelty
        when they add signal.
        """
        items: list[EvidenceItem] = []
        for cluster in clusters:
            items.append(self.create(cluster, evaluated_at, source))
        return items

    def build_supplementary(
        self,
        cluster: EventCluster,
        evaluated_at: datetime,
        source: str,
    ) -> list[EvidenceItem]:
        """Build supplementary evidence items for a cluster: source quality,
        confirmation, novelty, and data quality.
        """
        items: list[EvidenceItem] = []
        latest = cluster.records[-1] if cluster.records else None
        if latest is None:
            return items
        available = min(latest.available_at, evaluated_at)
        observed = min(latest.occurred_at or latest.available_at, evaluated_at)

        # Source quality evidence.
        items.append(
            EvidenceItem(
                evidence_id=str(uuid5(NAMESPACE_URL, f"news|sq|{cluster.cluster_id}|{evaluated_at.isoformat()}")),
                evidence_type=NEWS_EVIDENCE_TYPE,
                observed_at=observed,
                available_at=available,
                evaluated_at=evaluated_at,
                valid_until=evaluated_at + timedelta(days=3),
                strength=EvidenceStrength.MODERATE,
                summary=f"{SOURCE_QUALITY_CATEGORY}: source_quality={cluster.aggregate_source_quality.value}",
                confidence=0.7,
                capped=False,
                calibration_status=None,
                has_historical_calibration=False,
                source_analyst="news",
                assumptions=[Assumption(description="Source quality derived from supplied source metadata")],
                warnings=[],
                limitations=[],
                provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
            )
        )

        # Confirmation evidence.
        items.append(
            EvidenceItem(
                evidence_id=str(uuid5(NAMESPACE_URL, f"news|cf|{cluster.cluster_id}|{evaluated_at.isoformat()}")),
                evidence_type=NEWS_EVIDENCE_TYPE,
                observed_at=observed,
                available_at=available,
                evaluated_at=evaluated_at,
                valid_until=evaluated_at + timedelta(days=3),
                strength=EvidenceStrength.MODERATE,
                summary=f"{CONFIRMATION_CATEGORY}: status={cluster.confirmation_status.value}",
                confidence=0.7,
                capped=False,
                calibration_status=None,
                has_historical_calibration=False,
                source_analyst="news",
                assumptions=[Assumption(description="Confirmation status derived from source count and payload agreement")],
                warnings=[],
                limitations=[],
                provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
            )
        )

        # Novelty evidence.
        items.append(
            EvidenceItem(
                evidence_id=str(uuid5(NAMESPACE_URL, f"news|nv|{cluster.cluster_id}|{evaluated_at.isoformat()}")),
                evidence_type=NEWS_EVIDENCE_TYPE,
                observed_at=observed,
                available_at=available,
                evaluated_at=evaluated_at,
                valid_until=evaluated_at + timedelta(days=3),
                strength=EvidenceStrength.MODERATE,
                summary=f"{NOVELTY_CATEGORY}: score={cluster.novelty_score:.3f} duplicate={cluster.is_duplicate}",
                confidence=0.7,
                capped=False,
                calibration_status=None,
                has_historical_calibration=False,
                source_analyst="news",
                assumptions=[Assumption(description="Novelty derived from deterministic event fingerprinting")],
                warnings=[],
                limitations=[],
                provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
            )
        )

        # Data quality evidence.
        items.append(
            EvidenceItem(
                evidence_id=str(uuid5(NAMESPACE_URL, f"news|dq|{cluster.cluster_id}|{evaluated_at.isoformat()}")),
                evidence_type=NEWS_EVIDENCE_TYPE,
                observed_at=observed,
                available_at=available,
                evaluated_at=evaluated_at,
                valid_until=evaluated_at + timedelta(days=3),
                strength=EvidenceStrength.WEAK,
                summary=f"{DATA_QUALITY_CATEGORY}: quality_score={latest.quality_score:.3f}",
                confidence=0.6,
                capped=False,
                calibration_status=None,
                has_historical_calibration=False,
                source_analyst="news",
                assumptions=[Assumption(description="Data quality score supplied by the source record")],
                warnings=[],
                limitations=[],
                provenance=[ProvenanceRecord(source=source, retrieved_at=available, uri=None)],
            )
        )

        return items

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _category_for_cluster(self, cluster: EventCluster) -> str:
        """Map a cluster's event type to an evidence category prefix."""
        from app.services.news_analysis.domain import (
            CAPITAL_STRUCTURE_CATEGORY,
            CORPORATE_ACTION_CATEGORY,
            CYBER_CATEGORY,
            EARNINGS_CATEGORY,
            GUIDANCE_CATEGORY,
            INDUSTRY_CATEGORY,
            LEGAL_CATEGORY,
            MACRO_CATEGORY,
            MANAGEMENT_CATEGORY,
            OPERATIONS_CATEGORY,
            REGULATORY_CATEGORY,
            NewsEventType,
        )

        try:
            etype = NewsEventType(cluster.event_type)
        except ValueError:
            etype = NewsEventType.OTHER

        mapping = {
            NewsEventType.EARNINGS: EARNINGS_CATEGORY,
            NewsEventType.EARNINGS_GUIDANCE: GUIDANCE_CATEGORY,
            NewsEventType.REVENUE_GUIDANCE: GUIDANCE_CATEGORY,
            NewsEventType.ANALYST_REVISION: EARNINGS_CATEGORY,
            NewsEventType.MERGER_ACQUISITION: CORPORATE_ACTION_CATEGORY,
            NewsEventType.DIVESTITURE: CORPORATE_ACTION_CATEGORY,
            NewsEventType.CAPITAL_RAISE: CAPITAL_STRUCTURE_CATEGORY,
            NewsEventType.BUYBACK: CAPITAL_STRUCTURE_CATEGORY,
            NewsEventType.DIVIDEND: CORPORATE_ACTION_CATEGORY,
            NewsEventType.DEBT_EVENT: CAPITAL_STRUCTURE_CATEGORY,
            NewsEventType.MANAGEMENT_CHANGE: MANAGEMENT_CATEGORY,
            NewsEventType.REGULATORY: REGULATORY_CATEGORY,
            NewsEventType.LITIGATION: LEGAL_CATEGORY,
            NewsEventType.INVESTIGATION: LEGAL_CATEGORY,
            NewsEventType.CYBER_SECURITY: CYBER_CATEGORY,
            NewsEventType.DATA_BREACH: CYBER_CATEGORY,
            NewsEventType.SUPPLY_CHAIN: OPERATIONS_CATEGORY,
            NewsEventType.LAYOFFS: OPERATIONS_CATEGORY,
            NewsEventType.CREDIT_RATING: CAPITAL_STRUCTURE_CATEGORY,
            NewsEventType.BANKRUPTCY: CORPORATE_ACTION_CATEGORY,
            NewsEventType.RESTRUCTURING: CORPORATE_ACTION_CATEGORY,
            NewsEventType.GEOPOLITICAL: INDUSTRY_CATEGORY,
            NewsEventType.MACROECONOMIC: MACRO_CATEGORY,
            NewsEventType.CENTRAL_BANK: MACRO_CATEGORY,
            NewsEventType.COMMODITY: MACRO_CATEGORY,
            NewsEventType.TRADE_POLICY: REGULATORY_CATEGORY,
            NewsEventType.SANCTIONS: REGULATORY_CATEGORY,
            NewsEventType.INSIDER_TRANSACTION: CAPITAL_STRUCTURE_CATEGORY,
            NewsEventType.CORPORATE_ACTION: CORPORATE_ACTION_CATEGORY,
            NewsEventType.ACCOUNTING: EARNINGS_CATEGORY,
            NewsEventType.RESTATEMENT: EARNINGS_CATEGORY,
            NewsEventType.FRAUD_ALLEGATION: LEGAL_CATEGORY,
            NewsEventType.OPERATIONAL: OPERATIONS_CATEGORY,
            NewsEventType.OTHER: "other",
        }
        return mapping.get(etype, "other")

    def _build_summary(
        self,
        category: str,
        cluster: EventCluster,
        importance: NewsImportance,
        orientation: NewsOrientation,
    ) -> str:
        latest = cluster.records[-1] if cluster.records else None
        title = latest.title if latest else cluster.event_type
        orientation_str = orientation.value if isinstance(orientation, NewsOrientation) else str(orientation)
        importance_str = importance.value if isinstance(importance, NewsImportance) else str(importance)
        scope_str = _cluster_scope(cluster)
        if isinstance(scope_str, NewsScope):
            scope_str = scope_str.value
        return (
            f"{category}: {title} | "
            f"orientation={orientation_str} | "
            f"importance={importance_str} | "
            f"scope={scope_str} | "
            f"confirmation={cluster.confirmation_status.value}"
        )

    def _strength_for_importance(self, importance: NewsImportance, confidence: float) -> EvidenceStrength:
        if importance is NewsImportance.CRITICAL:
            return EvidenceStrength.STRONG
        if importance is NewsImportance.HIGH:
            return EvidenceStrength.STRONG if confidence >= 0.6 else EvidenceStrength.MODERATE
        if importance is NewsImportance.MODERATE:
            return EvidenceStrength.MODERATE if confidence >= 0.5 else EvidenceStrength.WEAK
        if importance is NewsImportance.LOW:
            return EvidenceStrength.WEAK
        return EvidenceStrength.WEAK

    def _warnings(self, cluster: EventCluster) -> list[AnalysisWarning]:
        warnings: list[AnalysisWarning] = []
        if cluster.aggregate_source_quality is SourceQuality.UNVERIFIED:
            warnings.append(AnalysisWarning(code="UNVERIFIED", message="Source quality is unverified"))
        if cluster.confirmation_status is ConfirmationStatus.CONFLICTING:
            warnings.append(AnalysisWarning(code="CONFLICTING", message="Multiple sources report conflicting payloads"))
        if not cluster.records:
            warnings.append(AnalysisWarning(code="EMPTY_CLUSTER", message="Event cluster has no observations"))
        return warnings


# ---------------------------------------------------------------------------
# Cluster-level helpers (imported lazily to avoid circular deps)
# ---------------------------------------------------------------------------


def cluster_importance(cluster: EventCluster) -> NewsImportance:
    """Derive the importance of a cluster from its latest record."""
    latest = cluster.records[-1] if cluster.records else None
    if latest is None:
        return NewsImportance.UNKNOWN
    val = getattr(latest, "importance", None)
    if val is None:
        return NewsImportance.UNKNOWN
    if isinstance(val, NewsImportance):
        return val
    try:
        return NewsImportance(val)
    except ValueError:
        return NewsImportance.UNKNOWN


def cluster_orientation(cluster: EventCluster) -> NewsOrientation:
    """Derive the orientation of a cluster from its latest record."""
    latest = cluster.records[-1] if cluster.records else None
    if latest is None:
        return NewsOrientation.NEUTRAL
    val = getattr(latest, "orientation", None)
    if val is None:
        return NewsOrientation.NEUTRAL
    if isinstance(val, NewsOrientation):
        return val
    try:
        return NewsOrientation(val)
    except ValueError:
        return NewsOrientation.NEUTRAL


def cluster_records_importance(cluster: EventCluster) -> NewsImportance:
    """Alias for cluster_importance — kept for clarity in evidence.py."""
    return cluster_importance(cluster)


def _cluster_scope(cluster: EventCluster) -> str:
    latest = cluster.records[-1] if cluster.records else None
    if latest is None:
        return "unknown"
    val = getattr(latest, "scope", None)
    if val is None:
        return "unknown"
    if isinstance(val, NewsScope):
        return val.value
    return str(val)
