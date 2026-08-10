"""Deterministic news decay assessment.

Events lose relevance over time.  Decay varies by event type and is computed
using configurable deterministic half-lives.  This is event relevance decay —
distinct from the analyst platform's general data freshness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.domain import NewsEventType


@dataclass(frozen=True)
class DecayResult:
    """The deterministic decay assessment for a single event."""

    age: timedelta
    half_life: timedelta
    decay_factor: float  # 1.0 = fresh, 0.0 = fully stale
    is_stale: bool
    effective_relevance: float  # decay_factor scaled by base importance weight


class NewsDecayService:
    """Apply event-type-specific deterministic decay to news events."""

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()

    def assess(
        self,
        observed_at: datetime,
        evaluated_at: datetime,
        event_type: NewsEventType | str | None = None,
    ) -> DecayResult:
        """Compute the decay factor for an event.

        ``decay_factor = 0.5 ^ (age / half_life)``

        An event is considered stale when ``decay_factor`` falls below
        ``config.stale_decay_threshold``.
        """
        age = max(timedelta(0), evaluated_at - observed_at)
        half_life = self._half_life(event_type)
        if half_life.total_seconds() <= 0:
            # No decay configured → treat as immediately stale.
            decay_factor = 0.0
        else:
            age_fraction = age.total_seconds() / half_life.total_seconds()
            decay_factor = max(0.0, 1.0 * (0.5**age_fraction))
        is_stale = decay_factor < self.config.stale_decay_threshold
        # Effective relevance also scales by a base weight for event category.
        base_weight = 1.0
        return DecayResult(
            age=age,
            half_life=half_life,
            decay_factor=round(decay_factor, 6),
            is_stale=is_stale,
            effective_relevance=round(decay_factor * base_weight, 6),
        )

    def _half_life(self, event_type: NewsEventType | str | None) -> timedelta:
        if event_type is None:
            return self.config.default_decay_half_life
        if isinstance(event_type, NewsEventType):
            key = event_type.value
        else:
            key = str(event_type).strip().lower()
        return self.config.decay_half_lives.get(key, self.config.default_decay_half_life)
