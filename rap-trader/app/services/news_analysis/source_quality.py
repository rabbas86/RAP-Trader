"""Deterministic source-quality assessment for news events.

Uses only supplied source metadata (provider, dataset, and source-level
metadata fields such as ``authoritative``).  Does NOT hardcode arbitrary
news-outlet reputations unless the source registry explicitly provides
reliability metadata.
"""

from __future__ import annotations

from app.domain.models.data_platform import DataSourceIdentity
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import SourceQuality


class SourceQualityService:
    """Assess source quality deterministically from metadata alone."""

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    def assess(self, source: DataSourceIdentity) -> SourceQuality:
        """Determine ``SourceQuality`` from the ``DataSourceIdentity`` metadata.

        Priority order:
        1. ``authoritative=True`` with a recognized authoritative dataset → AUTHORITATIVE
        2. Provider in the configured authoritative set → PRIMARY
        3. ``authoritative=True`` → HIGH_QUALITY_SECONDARY
        4. Authoritative metadata flag on provider → PRIMARY
        5. Dataset matches an authoritative pattern → HIGH_QUALITY_SECONDARY
        6. Otherwise → SECONDARY or UNKNOWN
        """
        provider_lower = (source.provider or "").lower().strip()
        dataset_lower = (source.dataset or "").lower().strip()

        # Check for authoritative filing/dataset patterns.
        for pattern in self.config.authoritative_dataset_patterns:
            if pattern in dataset_lower or pattern in provider_lower:
                return SourceQuality.AUTHORITATIVE

        # Check if provider is in the authoritative set.
        if provider_lower in {p.lower() for p in self.config.authoritative_providers}:
            return SourceQuality.PRIMARY

        # If authoritatively flagged by the registry.
        if source.authoritative:
            return SourceQuality.HIGH_QUALITY_SECONDARY

        # Source reliability metadata (if provided by registry).
        reliability = source.metadata.get("source_reliability", source.metadata.get("reliability"))
        if reliability is not None:
            if isinstance(reliability, (int, float)):
                rel = float(reliability)
                if rel >= 0.8:
                    return SourceQuality.HIGH_QUALITY_SECONDARY
                if rel >= 0.5:
                    return SourceQuality.SECONDARY
                return SourceQuality.UNVERIFIED
            if isinstance(reliability, str):
                if reliability.lower() in {"reliable", "high", "authoritative", "primary"}:
                    return SourceQuality.HIGH_QUALITY_SECONDARY
                if reliability.lower() in {"unverified", "unknown"}:
                    return SourceQuality.UNVERIFIED

        return SourceQuality.SECONDARY
