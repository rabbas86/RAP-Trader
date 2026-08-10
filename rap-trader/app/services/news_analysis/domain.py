"""Domain models for the Phase 9 News Analyst.

These are deterministic, offline classification primitives that turn
``NormalizedDataRecord`` rows (and their embedded ``EventRecord`` payloads)
from the Phase 8A Unified Research Data Platform into typed events, clusters,
evidence, and a final ``AnalysisDirection``.

No network, no LLM, no model download.  These models are research-only and
never produce trades.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from app.domain.models.analyst import EvidenceType


class NewsEventType(StrEnum):
    """Typed classifications of market-moving events."""

    EARNINGS = "earnings"
    EARNINGS_GUIDANCE = "earnings_guidance"
    REVENUE_GUIDANCE = "revenue_guidance"
    ANALYST_REVISION = "analyst_revision"
    MERGER_ACQUISITION = "merger_acquisition"
    DIVESTITURE = "divestiture"
    PARTNERSHIP = "partnership"
    PRODUCT_LAUNCH = "product_launch"
    PRODUCT_FAILURE = "product_failure"
    REGULATORY = "regulatory"
    LITIGATION = "litigation"
    INVESTIGATION = "investigation"
    MANAGEMENT_CHANGE = "management_change"
    CAPITAL_RAISE = "capital_raise"
    BUYBACK = "buyback"
    DIVIDEND = "dividend"
    DEBT_EVENT = "debt_event"
    CREDIT_RATING = "credit_rating"
    BANKRUPTCY = "bankruptcy"
    RESTRUCTURING = "restructuring"
    LAYOFFS = "layoffs"
    SUPPLY_CHAIN = "supply_chain"
    CYBER_SECURITY = "cyber_security"
    DATA_BREACH = "data_breach"
    GEOPOLITICAL = "geopolitical"
    SANCTIONS = "sanctions"
    TRADE_POLICY = "trade_policy"
    CENTRAL_BANK = "central_bank"
    MACROECONOMIC = "macroeconomic"
    COMMODITY = "commodity"
    INDUSTRY = "industry"
    COMPETITOR = "competitor"
    INSIDER_TRANSACTION = "insider_transaction"
    CORPORATE_ACTION = "corporate_action"
    ACCOUNTING = "accounting"
    RESTATEMENT = "restatement"
    FRAUD_ALLEGATION = "fraud_allegation"
    OPERATIONAL = "operational"
    OTHER = "other"


class NewsScope(StrEnum):
    """Ge Scope of a market-moving event."""

    COMPANY = "company"
    SECTOR = "sector"
    INDUSTRY = "industry"
    COUNTRY = "country"
    REGION = "region"
    GLOBAL = "global"
    MARKET_WIDE = "market_wide"
    UNKNOWN = "unknown"


class NewsOrientation(StrEnum):
    """Deterministic event orientation (not a trade instruction)."""

    STRONGLY_POSITIVE = "strongly_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    STRONGLY_NEGATIVE = "strongly_negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class NewsImportance(StrEnum):
    """Importance derived from explicit event type and structured attributes."""

    TRIVIAL = "trivial"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class SourceQuality(StrEnum):
    """Deterministic source-quality assessment from supplied metadata."""

    AUTHORITATIVE = "authoritative"
    PRIMARY = "primary"
    HIGH_QUALITY_SECONDARY = "high_quality_secondary"
    SECONDARY = "secondary"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class ConfirmationStatus(StrEnum):
    """Confirmation state across multiple structured records."""

    CONFIRMED = "confirmed"
    PARTIALLY_CONFIRMED = "partially_confirmed"
    CONFLICTING = "conflicting"
    UNVERIFIED = "unverified"
    SUPERSEDED = "superseded"


# ---------------------------------------------------------------------------
# Evidence category labels and evidence type used for the canonical
# EvidenceItem.summary prefix so the framework's existing category-splitting
# logic works unchanged.
# ---------------------------------------------------------------------------
EARNINGS_CATEGORY: Final[str] = "earnings"
GUIDANCE_CATEGORY: Final[str] = "guidance"
REGULATORY_CATEGORY: Final[str] = "regulatory"
CORPORATE_ACTION_CATEGORY: Final[str] = "corporate_action"
MANAGEMENT_CATEGORY: Final[str] = "management"
CAPITAL_STRUCTURE_CATEGORY: Final[str] = "capital_structure"
OPERATIONS_CATEGORY: Final[str] = "operations"
LEGAL_CATEGORY: Final[str] = "legal"
CYBER_CATEGORY: Final[str] = "cyber"
MACRO_CATEGORY: Final[str] = "macro"
GEOPOLITICAL_CATEGORY: Final[str] = "geopolitical"
INDUSTRY_CATEGORY: Final[str] = "industry"
SOURCE_QUALITY_CATEGORY: Final[str] = "source_quality"
CONFIRMATION_CATEGORY: Final[str] = "confirmation"
NOVELTY_CATEGORY: Final[str] = "novelty"
DATA_QUALITY_CATEGORY: Final[str] = "data_quality"

# Evidence type assigned to all news evidence items.
NEWS_EVIDENCE_TYPE: Final[EvidenceType] = EvidenceType.NEWS

# Event types that carry an explicit orientation signal in structured payload.
# These map event_type -> (positive_orientation, negative_orientation) tuples
# derived deterministically from structured_payload fields when present.
