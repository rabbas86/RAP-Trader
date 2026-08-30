"""Deterministic simulated historical clock for replay/backtest runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models.market_data import OHLCVBar
from app.services.historical.errors import HistoricalClockBackwardError, HistoricalClockBoundsError, HistoricalClockError

TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


class HistoricalClock:
    """Immutable-in-spirit deterministic simulated historical clock.

    The clock represents simulated historical time. It is initialized from
    explicit UTC timestamps and never consults wall time. Clock transitions
    are explicit and auditable by replaying ``advance_to`` calls from the
    recorded start state.
    """

    def __init__(self, *, now: datetime, start: datetime, end: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise HistoricalClockError("historical clock requires a timezone-aware UTC start time")
        self._now = now.astimezone(UTC)
        self._start = start.astimezone(UTC)
        self._end = end.astimezone(UTC)
        if self._start > self._end:
            raise HistoricalClockBoundsError("clock replay start must be before end", "replay_range")
        if not (self._start <= self._now <= self._end):
            raise HistoricalClockBoundsError(
                f"clock initial time {self._now.isoformat()} is outside replay range [{self._start.isoformat()}, {self._end.isoformat()}]",
                "replay_range",
            )

    @property
    def now(self) -> datetime:
        return self._now

    @property
    def start(self) -> datetime:
        return self._start

    @property
    def end(self) -> datetime:
        return self._end

    def advance_to(self, target: datetime) -> None:
        if target.tzinfo is None or target.utcoffset() is None:
            raise HistoricalClockError("clock target requires a timezone-aware UTC timestamp")
        target = target.astimezone(UTC)
        if target < self._now:
            raise HistoricalClockBackwardError(self._now.isoformat(), target.isoformat())
        if not (self._start <= target <= self._end):
            raise HistoricalClockBoundsError(
                f"clock target {target.isoformat()} is outside replay range [{self._start.isoformat()}, {self._end.isoformat()}]",
                "replay_range",
            )
        self._now = target

    def advance_by(self, delta: timedelta) -> None:
        if delta < timedelta(0):
            raise HistoricalClockBackwardError(self._now.isoformat(), (self._now + delta).isoformat())
        self.advance_to(self._now + delta)

    def copy(self) -> HistoricalClock:
        return HistoricalClock(now=self._now, start=self._start, end=self._end)


def _normalize_timeframe(timeframe: str) -> str:
    return timeframe.lower()


TIMEFRAME_DELTAS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


def _bar_available_at(bar: OHLCVBar, timeframe: str) -> datetime:
    """A complete bar becomes available after the session that it covers closes."""
    normalized = _normalize_timeframe(timeframe)
    step = TIMEFRAME_DELTAS.get(normalized)
    if step is None:
        raise ValueError(f"unsupported timeframe for bar availability: {timeframe}")
    return bar.timestamp + step


__all__ = [
    "TIMEFRAME_DELTAS",
    "HistoricalClock",
    "_bar_available_at",
    "_normalize_timeframe",
]
