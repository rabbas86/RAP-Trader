"""Deterministic classification of news observations.

Turns a ``NewsObservation`` into typed classifications:
- ``NewsEventType`` — what kind of event it is
- ``NewsScope`` — how broadly scoped
- ``NewsOrientation`` — whether it is positive or negative for the entity
- ``NewsImportance`` — derived from explicit event type and structured attributes

No LLM, no network, no embeddings.  All classification is deterministic.
"""

from __future__ import annotations

from typing import Any

from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import (
    NewsEventType,
    NewsImportance,
    NewsOrientation,
    NewsScope,
)
from app.services.news_analysis.observations import NewsObservation

# ---------------------------------------------------------------------------
# Event type classification
# ---------------------------------------------------------------------------

# Mapping from lowercased event-type strings found in records to canonical
# NewsEventType values.  The event_type on a NewsObservation comes from the
# record's series_id (upper-cased) or event_type (lower-cased).  We normalize.
_TYPE_ALIASES: dict[str, NewsEventType] = {
    "earnings": NewsEventType.EARNINGS,
    "earnings_guidance": NewsEventType.EARNINGS_GUIDANCE,
    "revenue_guidance": NewsEventType.REVENUE_GUIDANCE,
    "earnings.guide": NewsEventType.EARNINGS_GUIDANCE,
    "revenue.guide": NewsEventType.REVENUE_GUIDANCE,
    "analyst_revision": NewsEventType.ANALYST_REVISION,
    "merger_acquisition": NewsEventType.MERGER_ACQUISITION,
    "ma": NewsEventType.MERGER_ACQUISITION,
    "m&a": NewsEventType.MERGER_ACQUISITION,
    "merger": NewsEventType.MERGER_ACQUISITION,
    "acquisition": NewsEventType.MERGER_ACQUISITION,
    "divestiture": NewsEventType.DIVESTITURE,
    "spinoff": NewsEventType.DIVESTITURE,
    "partnership": NewsEventType.PARTNERSHIP,
    "product_launch": NewsEventType.PRODUCT_LAUNCH,
    "product_failure": NewsEventType.PRODUCT_FAILURE,
    "recall": NewsEventType.PRODUCT_FAILURE,
    "regulatory": NewsEventType.REGULATORY,
    "litigation": NewsEventType.LITIGATION,
    "investigation": NewsEventType.INVESTIGATION,
    "management_change": NewsEventType.MANAGEMENT_CHANGE,
    "ceo": NewsEventType.MANAGEMENT_CHANGE,
    "capital_raise": NewsEventType.CAPITAL_RAISE,
    "buyback": NewsEventType.BUYBACK,
    "dividend": NewsEventType.DIVIDEND,
    "debt_event": NewsEventType.DEBT_EVENT,
    "credit_rating": NewsEventType.CREDIT_RATING,
    "bankruptcy": NewsEventType.BANKRUPTCY,
    "restructuring": NewsEventType.RESTRUCTURING,
    "layoffs": NewsEventType.LAYOFFS,
    "supply_chain": NewsEventType.SUPPLY_CHAIN,
    "cyber_security": NewsEventType.CYBER_SECURITY,
    "cybersecurity": NewsEventType.CYBER_SECURITY,
    "data_breach": NewsEventType.DATA_BREACH,
    "geopolitical": NewsEventType.GEOPOLITICAL,
    "sanctions": NewsEventType.SANCTIONS,
    "trade_policy": NewsEventType.TRADE_POLICY,
    "central_bank": NewsEventType.CENTRAL_BANK,
    "centralbank": NewsEventType.CENTRAL_BANK,
    "macroeconomic": NewsEventType.MACROECONOMIC,
    "commodity": NewsEventType.COMMODITY,
    "industry": NewsEventType.INDUSTRY,
    "competitor": NewsEventType.COMPETITOR,
    "insider_transaction": NewsEventType.INSIDER_TRANSACTION,
    "corporate_action": NewsEventType.CORPORATE_ACTION,
    "accounting": NewsEventType.ACCOUNTING,
    "restatement": NewsEventType.RESTATEMENT,
    "fraud_allegation": NewsEventType.FRAUD_ALLEGATION,
    "operational": NewsEventType.OPERATIONAL,
    "other": NewsEventType.OTHER,
}


def classify_event_type(observation: NewsObservation, config: NewsAnalystConfig | None = None) -> NewsEventType:
    """Map a raw event type string to a canonical ``NewsEventType``."""
    raw = (observation.event_type or "other").strip().lower()
    # Try direct alias match.
    if raw in _TYPE_ALIASES:
        return _TYPE_ALIASES[raw]
    # Try matching against enum values directly.
    for member in NewsEventType:
        if member.value == raw:
            return member
    return NewsEventType.OTHER


# ---------------------------------------------------------------------------
# Scope classification
# ---------------------------------------------------------------------------

# Keywords that indicate scope from the title, summary, or metadata.
_SCOPE_KEYWORDS: dict[NewsScope, tuple[str, ...]] = {
    NewsScope.MARKET_WIDE: ("stock market", "markets", "wall street", "s&p", "nasdaq", "dow jones", "all stocks"),
    NewsScope.GLOBAL: ("global", "worldwide", "international", "world "),
    NewsScope.REGION: ("asia", "europe", "north america", "middle east", "africa"),
    NewsScope.COUNTRY: ("united states", "us ", "u.s. ", "china", "japan", "europe", "uk", "brexit"),
    NewsScope.INDUSTRY: ("sector", "industry", "semiconductor", "banking", "energy", "healthcare"),
    NewsScope.SECTOR: ("tech sector", "energy sector", "financial sector"),
    NewsScope.COMPANY: ("the company", "the firm", "corp", "incorporated"),
}


def classify_scope(observation: NewsObservation, config: NewsAnalystConfig | None = None) -> NewsScope:
    """Determine the geographic/functional scope of an event.

    Scope is inferred from the entity field and keyword matching on the
    title/summary.  It is deterministic, not LLM-based.
    """
    entity = (observation.entity or "").strip()
    text = " ".join(filter(None, [observation.title, observation.summary or ""])).lower()

    # Company-level scope when an entity is present and the text doesn't
    # strongly indicate a broader scope.
    if entity and not _has_scope_keywords(text, (NewsScope.GLOBAL, NewsScope.REGION, NewsScope.COUNTRY, NewsScope.MARKET_WIDE)):
        if _has_scope_keywords(text, NewsScope.COMPANY):
            return NewsScope.COMPANY
        if _has_scope_keywords(text, (NewsScope.INDUSTRY, NewsScope.SECTOR)):
            return NewsScope.INDUSTRY if _has_scope_keywords(text, NewsScope.INDUSTRY) else NewsScope.SECTOR
        return NewsScope.COMPANY

    # No entity or broader scope keywords present.
    if _has_scope_keywords(text, NewsScope.MARKET_WIDE):
        return NewsScope.MARKET_WIDE
    if _has_scope_keywords(text, NewsScope.GLOBAL):
        return NewsScope.GLOBAL
    if _has_scope_keywords(text, NewsScope.REGION):
        return NewsScope.REGION
    if _has_scope_keywords(text, NewsScope.COUNTRY):
        return NewsScope.COUNTRY
    if _has_scope_keywords(text, NewsScope.INDUSTRY):
        return NewsScope.INDUSTRY
    if _has_scope_keywords(text, NewsScope.SECTOR):
        return NewsScope.SECTOR
    if _has_scope_keywords(text, NewsScope.COMPANY):
        return NewsScope.COMPANY

    return NewsScope.UNKNOWN


def _has_scope_keywords(text: str, scopes: NewsScope | tuple[NewsScope, ...]) -> bool:
    if isinstance(scopes, NewsScope):
        scopes = (scopes,)
    keywords: list[str] = []
    for scope in scopes:
        keywords.extend(_SCOPE_KEYWORDS.get(scope, ()))
    return any(kw in text for kw in keywords)


# ---------------------------------------------------------------------------
# Orientation classification
# ---------------------------------------------------------------------------

# Structured payload fields that indicate positive vs negative orientation.
_POSITIVE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"beat", "positive", "raised", "increased", "hiked", "upgraded", "approved", "completed", "launched", "authorized"}
)
_NEGATIVE_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"miss", "negative", "lowered", "decreased", "cut", "downgraded", "rejected", "delayed", "recalled", "restated_negative"}
)

# Strong indicator keywords in the title/summary.
_POSITIVE_KEYWORDS: tuple[str, ...] = (
    "beat",
    "exceeded expectations",
    "raised guidance",
    "increased",
    "approved",
    "launched",
    "upgraded",
    "completed",
    "gain",
    "profit beat",
    "surpassed",
    "above estimates",
    "bullish",
    "optimis",
    "optimis",
)
_NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "miss",
    "missed expectations",
    "below estimates",
    "fell short",
    "lowered guidance",
    "declined",
    "decrease",
    "cut guidance",
    "downgraded",
    "delayed",
    "recall",
    "restated",
    "bearish",
    "pessimis",
    "disappointed",
    "short of",
    "misses",
    "weak",
)

# Critical event types that are inherently negative regardless of payload.
_STRONGLY_NEGATIVE_TYPES: frozenset[NewsEventType] = frozenset(
    {
        NewsEventType.BANKRUPTCY,
        NewsEventType.RESTATEMENT,
        NewsEventType.FRAUD_ALLEGATION,
        NewsEventType.INVESTIGATION,
        NewsEventType.LITIGATION,
        NewsEventType.DATA_BREACH,
        NewsEventType.CYBER_SECURITY,
        NewsEventType.LAYOFFS,
    }
)

_STRONGLY_NEGATIVE_TYPES = frozenset(
    {
        NewsEventType.BANKRUPTCY,
        NewsEventType.RESTATEMENT,
        NewsEventType.FRAUD_ALLEGATION,
        NewsEventType.INVESTIGATION,
        NewsEventType.LITIGATION,
        NewsEventType.DATA_BREACH,
        NewsEventType.LAYOFFS,
    }
)

# Critical event types that are inherently positive.
_STRONGLY_POSITIVE_TYPES: frozenset[NewsEventType] = frozenset({NewsEventType.BUYBACK, NewsEventType.DIVIDEND})


def classify_orientation(
    observation: NewsObservation, event_type: NewsEventType, config: NewsAnalystConfig | None = None
) -> NewsOrientation:
    """Determine the orientation of an event from structured payload + keywords.

    This is event orientation, not a trade instruction.  The orientation is
    derived deterministically from:
    1. Explicit structured-payload fields (surprise, beat/miss, direction).
    2. Strong keyword signals in the title/summary.
    3. Event-type-level defaults (bankruptcy is negative, buyback is positive).
    """
    payload = observation.structured_payload or {}
    text = " ".join(filter(None, [observation.title, observation.summary or ""])).lower()

    # 1. Structured payload orientation.
    positive_score = _payload_orientation_score(payload, text, positive=True)
    negative_score = _payload_orientation_score(payload, text, positive=False)

    # 2. Event-type defaults.
    if event_type in _STRONGLY_NEGATIVE_TYPES:
        negative_score += 2
    elif event_type in _STRONGLY_POSITIVE_TYPES:
        positive_score += 2

    # 3. Keyword-based scoring.
    positive_score += sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    negative_score += sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)

    # 4. Structured surprise field (e.g. earnings surprise > 0 is positive).
    surprise = _extract_numeric(payload.get("surprise"), payload.get("earnings_surprise"))
    if surprise is not None:
        if surprise > 0:
            positive_score += 1
        elif surprise < 0:
            negative_score += 1

    if positive_score > 0 and negative_score > 0:
        if abs(positive_score - negative_score) <= 1:
            return NewsOrientation.MIXED
        return NewsOrientation.STRONGLY_POSITIVE if positive_score > negative_score else NewsOrientation.STRONGLY_NEGATIVE
    if positive_score >= 2:
        return NewsOrientation.STRONGLY_POSITIVE
    if positive_score == 1:
        return NewsOrientation.POSITIVE
    if negative_score >= 2:
        return NewsOrientation.STRONGLY_NEGATIVE
    if negative_score == 1:
        return NewsOrientation.NEGATIVE
    return NewsOrientation.NEUTRAL


def _payload_orientation_score(payload: dict[str, Any], text: str, positive: bool) -> int:
    score = 0
    keys = _POSITIVE_PAYLOAD_KEYS if positive else _NEGATIVE_PAYLOAD_KEYS
    for key in keys:
        if key in payload:
            score += 1
        if key in text:
            score += 1
    # Explicit direction field.
    direction = payload.get("direction", "")
    if isinstance(direction, str):
        direction_lower = direction.lower()
        if positive and direction_lower in {"positive", "up", "increase", "beat"}:
            score += 1
        if not positive and direction_lower in {"negative", "down", "decrease", "miss"}:
            score += 1
    return score


def _extract_numeric(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            inner = value.get("value")
            if isinstance(inner, (int, float)):
                return float(inner)
    return None


# ---------------------------------------------------------------------------
# Importance classification
# ---------------------------------------------------------------------------

# Critical event types that are always at least HIGH importance.
_CRITICAL_TYPES: frozenset[NewsEventType] = frozenset(
    {
        NewsEventType.BANKRUPTCY,
        NewsEventType.RESTATEMENT,
        NewsEventType.FRAUD_ALLEGATION,
        NewsEventType.MERGER_ACQUISITION,
        NewsEventType.GEOPOLITICAL,
        NewsEventType.SANCTIONS,
        NewsEventType.CENTRAL_BANK,
        NewsEventType.MACROECONOMIC,
        NewsEventType.CREDIT_RATING,
        NewsEventType.CAPITAL_RAISE,
        NewsEventType.DIVIDEND,
        NewsEventType.DEBT_EVENT,
    }
)

_HIGH_TYPES: frozenset[NewsEventType] = frozenset(
    {
        NewsEventType.EARNINGS,
        NewsEventType.MANAGEMENT_CHANGE,
        NewsEventType.REGULATORY,
        NewsEventType.LITIGATION,
        NewsEventType.DATA_BREACH,
        NewsEventType.CYBER_SECURITY,
        NewsEventType.LAYOFFS,
        NewsEventType.SUPPLY_CHAIN,
        NewsEventType.ACCOUNTING,
    }
)


def classify_importance(
    observation: NewsObservation,
    event_type: NewsEventType,
    config: NewsAnalystConfig | None = None,
) -> NewsImportance:
    """Derive importance from explicit event type and structured attributes.

    Does not invent importance based on prose style — only from the event
    type and structured payload fields.
    """
    config = config or NewsAnalystConfig()
    level_str = config.importance_by_event_type.get(event_type.value, "unknown")
    if level_str == "unknown":
        # Try to infer from payload magnitude.
        payload = observation.structured_payload or {}
        surprise = _extract_numeric(payload.get("surprise"), payload.get("magnitude"))
        if surprise is not None and abs(surprise) >= 10:
            return NewsImportance.HIGH
        if surprise is not None and abs(surprise) >= 1:
            return NewsImportance.MODERATE
        return NewsImportance.UNKNOWN
    try:
        return NewsImportance(level_str)
    except ValueError:
        return NewsImportance.UNKNOWN


# ---------------------------------------------------------------------------
# Composite classification result
# ---------------------------------------------------------------------------

from dataclasses import dataclass


@dataclass(frozen=True)
class EventClassification:
    """The complete deterministic classification of a single event."""

    event_type: NewsEventType
    scope: NewsScope
    orientation: NewsOrientation
    importance: NewsImportance
    source_quality: Any  # set by SourceQualityService
    confirmation_status: Any  # set by NewsConfirmationService

    @property
    def is_negative(self) -> bool:
        return self.orientation in {NewsOrientation.NEGATIVE, NewsOrientation.STRONGLY_NEGATIVE}

    @property
    def is_positive(self) -> bool:
        return self.orientation in {NewsOrientation.POSITIVE, NewsOrientation.STRONGLY_POSITIVE}

    @property
    def is_strongly_negative(self) -> bool:
        return self.orientation is NewsOrientation.STRONGLY_NEGATIVE

    @property
    def is_strongly_positive(self) -> bool:
        return self.orientation is NewsOrientation.STRONGLY_POSITIVE


def classify(observation: NewsObservation, config: NewsAnalystConfig | None = None) -> EventClassification:
    """Run the full deterministic classification pipeline on an observation."""
    event_type = classify_event_type(observation, config)
    scope = classify_scope(observation, config)
    orientation = classify_orientation(observation, event_type, config)
    importance = classify_importance(observation, event_type, config)
    return EventClassification(
        event_type=event_type,
        scope=scope,
        orientation=orientation,
        importance=importance,
        source_quality=None,
        confirmation_status=None,
    )
