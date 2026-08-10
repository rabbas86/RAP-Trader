"""Deterministic news materiality assessment.

``NewsMaterialityService`` determines whether a classified news event carries
enough signal to influence the analyst opinion.  Materiality is a function of:

* the event type (some event types are inherently material — bankruptcy,
  restatement, central-bank policy, etc.);
* the event importance tier (CRITICAL / HIGH / MODERATE / LOW / TRIVIAL);
* the news scope (company-scoped events are more material to a single ticker
  than global macro events, and vice-versa);
* the source quality (authoritative sources raise materiality for the same
  event type; unverified sources lower it).

Materiality is **not** a trade recommendation.  It simply gates whether an
event participates in the synthesis score with full weight.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import (
    NewsEventType,
    NewsImportance,
    NewsScope,
    SourceQuality,
)


@dataclass(frozen=True)
class MaterialityResult:
    """The deterministic materiality assessment for a single news event."""

    is_material: bool
    score: float  # 0.0 — 1.0, higher = more material
    is_critical: bool
    reason: str


# Event types that are always material regardless of other factors.
_CRITICAL_EVENT_TYPES: frozenset[NewsEventType] = frozenset(
    {
        NewsEventType.BANKRUPTCY,
        NewsEventType.RESTATEMENT,
        NewsEventType.FRAUD_ALLEGATION,
        NewsEventType.MERGER_ACQUISITION,
        NewsEventType.CREDIT_RATING,
        NewsEventType.CAPITAL_RAISE,
        NewsEventType.DEBT_EVENT,
        NewsEventType.DIVIDEND,
        NewsEventType.DIVESTITURE,
        NewsEventType.BUYBACK,
        NewsEventType.CENTRAL_BANK,
        NewsEventType.MACROECONOMIC,
        NewsEventType.GEOPOLITICAL,
        NewsEventType.SANCTIONS,
        NewsEventType.TRADE_POLICY,
        NewsEventType.LITIGATION,
        NewsEventType.DATA_BREACH,
        NewsEventType.CYBER_SECURITY,
        NewsEventType.MANAGEMENT_CHANGE,
        NewsEventType.REGULATORY,
        NewsEventType.ACCOUNTING,
    }
)

# Source-quality → materiality multiplier.
_QUALITY_WEIGHTS: dict[SourceQuality, float] = {
    SourceQuality.AUTHORITATIVE: 1.0,
    SourceQuality.PRIMARY: 0.9,
    SourceQuality.HIGH_QUALITY_SECONDARY: 0.8,
    SourceQuality.SECONDARY: 0.6,
    SourceQuality.UNVERIFIED: 0.3,
    SourceQuality.CONFLICTING: 0.2,
    SourceQuality.UNKNOWN: 0.5,
}

# Scope → materiality multiplier for the *target* entity.
_SCOPE_WEIGHTS: dict[NewsScope, float] = {
    NewsScope.COMPANY: 1.0,
    NewsScope.SECTOR: 0.8,
    NewsScope.INDUSTRY: 0.6,
    NewsScope.COUNTRY: 0.5,
    NewsScope.REGION: 0.4,
    NewsScope.GLOBAL: 0.3,
    NewsScope.MARKET_WIDE: 0.3,
    NewsScope.UNKNOWN: 0.7,
}


class NewsMaterialityService:
    """Assess the materiality of a classified news event deterministically."""

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    def assess(
        self,
        event_type: NewsEventType,
        importance: NewsImportance,
        scope: NewsScope,
        source_quality: SourceQuality,
    ) -> MaterialityResult:
        """Return the materiality assessment for a single classified event.

        Parameters
        ----------
        event_type
            The canonical ``NewsEventType`` of the event.
        importance
            The importance tier assigned by ``classify_importance``.
        scope
            The geographic / functional scope.
        source_quality
            The deterministic source-quality rating.
        """
        # Critical event types are always material.
        if event_type in _CRITICAL_EVENT_TYPES:
            quality_mult = _QUALITY_WEIGHTS.get(source_quality, 0.5)
            scope_mult = _SCOPE_WEIGHTS.get(scope, 0.7)
            score = 0.8 * quality_mult * scope_mult
            return MaterialityResult(
                is_material=True,
                score=min(score, 1.0),
                is_critical=True,
                reason=f"event type {event_type.value} is inherently critical",
            )

        # Non-critical: derive score from importance × quality × scope.
        importance_weight = _importance_weight(importance)
        quality_mult = _QUALITY_WEIGHTS.get(source_quality, 0.5)
        scope_mult = _SCOPE_WEIGHTS.get(scope, 0.7)
        score = round(importance_weight * quality_mult * scope_mult, 6)

        # TRIVIAL importance with unverified source → not material.
        if importance is NewsImportance.TRIVIAL and source_quality in {
            SourceQuality.UNVERIFIED,
            SourceQuality.UNKNOWN,
        }:
            return MaterialityResult(
                is_material=False,
                score=score,
                is_critical=False,
                reason="trivial event from unverified source",
            )

        if importance is NewsImportance.UNKNOWN:
            return MaterialityResult(
                is_material=False,
                score=score,
                is_critical=False,
                reason="event type and importance are unknown",
            )

        is_material = score >= self.config.materiality_threshold
        if is_material:
            reason = f"score {score:.3f} >= threshold {self.config.materiality_threshold}"
        else:
            reason = f"score {score:.3f} below threshold {self.config.materiality_threshold}"

        return MaterialityResult(
            is_material=is_material,
            score=score,
            is_critical=False,
            reason=reason,
        )


def _importance_weight(importance: NewsImportance) -> float:
    """Map an importance tier to a [0, 1] weight."""
    mapping: dict[NewsImportance, float] = {
        NewsImportance.CRITICAL: 1.0,
        NewsImportance.HIGH: 0.8,
        NewsImportance.MODERATE: 0.5,
        NewsImportance.LOW: 0.25,
        NewsImportance.TRIVIAL: 0.1,
        NewsImportance.UNKNOWN: 0.05,
    }
    return mapping.get(importance, 0.1)
