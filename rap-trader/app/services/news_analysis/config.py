"""Configuration for the Phase 9 News Analyst.

Contains the deterministic thresholds, half-lives, and importance mappings that
govern how news events are classified, aged, and weighted. All thresholds are
documented constants so that policy changes are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from app.domain.models.analyst import AnalystRole


@dataclass(frozen=True)
class NewsAnalystConfig:
    """Configuration for the deterministic, research-only news analyst."""

    analyst_id: str = "news"
    role: AnalystRole = AnalystRole.NEWS
    research_only: bool = True
    suitable_for_live_trading: bool = False

    # --- Confidence ---------------------------------------------------------
    uncalibrated_confidence_cap: float = 0.65
    stale_input_allowed: bool = False
    base_evidence_confidence: float = 0.7

    # --- Novelty scoring thresholds ----------------------------------------
    # A payload change >= this fraction is treated as a revision/follow-up
    # rather than a duplicate.
    payload_change_threshold: float = 0.05
    # Fingerprint tokens that always indicate a new event regardless of
    # headline overlap.
    revision_indicators: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"revised", "updated", "corrected", "amended", "restated", "raised", "lowered", "narrowed", "raised_guidance"}
        )
    )

    # --- Decay half lives by event type (in hours) -------------------------
    # Events lose relevance over time. Decay varies by event type.
    decay_half_lives: dict[str, timedelta] = field(
        default_factory=lambda: {
            "earnings": timedelta(hours=72),
            "earnings_guidance": timedelta(hours=168),
            "revenue_guidance": timedelta(hours=168),
            "analyst_revision": timedelta(hours=24),
            "merger_acquisition": timedelta(hours=336),
            "divestiture": timedelta(hours=336),
            "partnership": timedelta(hours=168),
            "product_launch": timedelta(hours=168),
            "product_failure": timedelta(hours=72),
            "regulatory": timedelta(hours=336),
            "litigation": timedelta(hours=336),
            "investigation": timedelta(hours=168),
            "management_change": timedelta(hours=168),
            "capital_raise": timedelta(hours=336),
            "buyback": timedelta(hours=336),
            "dividend": timedelta(hours=336),
            "debt_event": timedelta(hours=336),
            "credit_rating": timedelta(hours=336),
            "bankruptcy": timedelta(hours=720),
            "restructuring": timedelta(hours=336),
            "layoffs": timedelta(hours=168),
            "supply_chain": timedelta(hours=72),
            "cyber_security": timedelta(hours=336),
            "data_breach": timedelta(hours=336),
            "geopolitical": timedelta(hours=168),
            "sanctions": timedelta(hours=336),
            "trade_policy": timedelta(hours=168),
            "central_bank": timedelta(hours=168),
            "macroeconomic": timedelta(hours=168),
            "commodity": timedelta(hours=72),
            "industry": timedelta(hours=72),
            "competitor": timedelta(hours=72),
            "insider_transaction": timedelta(hours=72),
            "corporate_action": timedelta(hours=336),
            "accounting": timedelta(hours=168),
            "restatement": timedelta(hours=336),
            "fraud_allegation": timedelta(hours=168),
            "operational": timedelta(hours=72),
            "other": timedelta(hours=24),
        }
    )

    # Fallback half-life when event type is unknown or not listed.
    default_decay_half_life: timedelta = field(default_factory=lambda: timedelta(hours=48))

    # An event is considered stale (fully decayed) when its decay factor
    # drops below this threshold.
    stale_decay_threshold: float = 0.05

    # --- Importance mapping by event type -----------------------------------
    # Critical: earnings misses/beats with large magnitude, bankruptcy, major M&A
    # High: guidance changes, regulatory rulings, major product events
    # Moderate: routine corporate actions, minor guidance
    # Low: analyst notes, minor operational updates
    importance_by_event_type: dict[str, str] = field(
        default_factory=lambda: {
            "earnings": "high",
            "earnings_guidance": "high",
            "revenue_guidance": "high",
            "analyst_revision": "moderate",
            "merger_acquisition": "critical",
            "divestiture": "high",
            "partnership": "moderate",
            "product_launch": "moderate",
            "product_failure": "high",
            "regulatory": "high",
            "litigation": "high",
            "investigation": "moderate",
            "management_change": "high",
            "capital_raise": "moderate",
            "buyback": "moderate",
            "dividend": "moderate",
            "debt_event": "moderate",
            "credit_rating": "high",
            "bankruptcy": "critical",
            "restructuring": "high",
            "layoffs": "high",
            "supply_chain": "high",
            "cyber_security": "high",
            "data_breach": "high",
            "geopolitical": "critical",
            "sanctions": "critical",
            "trade_policy": "high",
            "central_bank": "critical",
            "macroeconomic": "high",
            "commodity": "moderate",
            "industry": "low",
            "competitor": "low",
            "insider_transaction": "low",
            "corporate_action": "moderate",
            "accounting": "high",
            "restatement": "critical",
            "fraud_allegation": "critical",
            "operational": "low",
            "other": "unknown",
        }
    )

    # --- Authority metadata categories --------------------------------------
    # These are checked against the source provider/dataset metadata to
    # determine source quality deterministically.
    authoritative_providers: frozenset[str] = field(
        default_factory=lambda: frozenset({"seced", "edgar", "sec", "fda", "federalreserve", "treasury", "irs", "court"})
    )

    # Provider/dataset combinations treated as authoritative filings.
    authoritative_dataset_patterns: tuple[str, ...] = (
        "regulatory_filing",
        "company_filing",
        "central_bank_publication",
        "government_release",
        "court_document",
    )

    # Minimum number of news events required before the analyst renders a
    # non-insufficient opinion.
    min_events_for_opinion: int = 1

    # --- Materiality --------------------------------------------------------
    # A news event must score >= this threshold on the materiality scale
    # (importance * source_quality * scope) to participate in synthesis with
    # full weight.  Events below the threshold are still reported as evidence
    # but receive a confidence penalty.
    materiality_threshold: float = 0.15
