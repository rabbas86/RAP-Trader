"""Point-in-time boundary service for historical replay."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.domain.models.historical_replay import VALID_POINT_IN_TIME_POLICIES, HistoricalReplaySpecification
from app.services.historical.clock import TIMEFRAME_DELTAS, HistoricalClock, _bar_available_at, _normalize_timeframe
from app.services.historical.errors import (
    PointInTimeBoundaryError,
    PointInTimeLookaheadError,
    PointInTimeMissingAvailabilityError,
    PointInTimeTemporalViolationError,
)

if TYPE_CHECKING:
    from app.domain.models.data_platform import EconomicObservation, NormalizedDataRecord
    from app.domain.models.market_data import HistoricalBarsResult, OHLCVBar


class PointInTimeDataBoundary:
    """Deterministic point-in-time data boundary for historical replay."""

    def __init__(self, *, clock: HistoricalClock, specification: HistoricalReplaySpecification) -> None:
        self.clock = clock
        self.specification = specification
        self.point_in_time_policy = specification.point_in_time_policy
        if self.point_in_time_policy not in VALID_POINT_IN_TIME_POLICIES:
            raise ValueError(f"unsupported point-in-time policy: {self.point_in_time_policy}")

    def _current_time(self) -> datetime:
        return self.clock.now

    def validate_record_availability(self, record: NormalizedDataRecord) -> None:
        available_at = record.availability.available_at
        if available_at > self._current_time():
            raise PointInTimeLookaheadError(available_at.isoformat(), self._current_time().isoformat())
        if available_at > record.availability.ingested_at:
            raise PointInTimeTemporalViolationError(
                str(record.record_id),
                "record available_at cannot be after ingested_at",
            )

    def validate_record_revision(self, record: NormalizedDataRecord) -> None:
        if record.availability.available_at != record.revision.available_at:
            raise PointInTimeTemporalViolationError(
                str(record.record_id),
                "record and revision availability timestamps must match",
            )

    def is_record_visible(self, record: NormalizedDataRecord) -> bool:
        try:
            if self.point_in_time_policy == "event_time_only":
                self.validate_record_revision(record)
                self._check_event_time_only_metadata(record)
                return record.availability.available_at <= self._current_time()
            if record.availability.available_at is None:
                raise PointInTimeMissingAvailabilityError(str(record.record_id))
            self.validate_record_availability(record)
            self.validate_record_revision(record)
        except PointInTimeBoundaryError:
            return False
        return True

    def filter_records(self, records: tuple[NormalizedDataRecord, ...]) -> tuple[NormalizedDataRecord, ...]:
        visible: list[NormalizedDataRecord] = []
        for record in records:
            if self.is_record_visible(record):
                visible.append(record)
        visible.sort(key=lambda item: str(item.record_id))
        return tuple(visible)

    def latest_revision(self, revisions: tuple[NormalizedDataRecord, ...]) -> NormalizedDataRecord:
        if not revisions:
            raise PointInTimeBoundaryError("revisions supplied to latest_revision must not be empty")
        eligible = tuple(item for item in revisions if item.availability.available_at <= self._current_time())
        if not eligible:
            raise PointInTimeLookaheadError(
                max(item.availability.available_at.isoformat() for item in revisions),
                self._current_time().isoformat(),
            )
        return max(eligible, key=lambda item: (item.availability.available_at, item.revision.revision_number, str(item.record_id)))

    def validate_historical_bars(self, result: HistoricalBarsResult) -> None:
        visible_bars = []
        for bar in result.bars:
            available_at = _bar_available_at(bar, result.timeframe)
            if available_at > self._current_time():
                raise PointInTimeLookaheadError(available_at.isoformat(), self._current_time().isoformat())
            visible_bars.append(bar)
        if not visible_bars:
            raise PointInTimeLookaheadError(
                (result.bars[0].timestamp + TIMEFRAME_DELTAS[_normalize_timeframe(result.timeframe)]).isoformat(),
                self._current_time().isoformat(),
            )

    def filter_historical_bars(self, result: HistoricalBarsResult) -> tuple[OHLCVBar, ...]:
        visible_bars = []
        for bar in result.bars:
            available_at = _bar_available_at(bar, result.timeframe)
            if available_at <= self._current_time():
                visible_bars.append(bar)
        if not visible_bars:
            raise PointInTimeLookaheadError(
                (result.bars[0].timestamp + TIMEFRAME_DELTAS[_normalize_timeframe(result.timeframe)]).isoformat(),
                self._current_time().isoformat(),
            )
        visible_bars.sort(key=lambda bar: bar.timestamp)
        return tuple(visible_bars)

    def filter_economic_observations(self, observations: tuple[EconomicObservation, ...]) -> tuple[EconomicObservation, ...]:
        visible = []
        for observation in observations:
            if observation.available_at > self._current_time():
                raise PointInTimeLookaheadError(observation.available_at.isoformat(), self._current_time().isoformat())
            visible.append(observation)
        return tuple(visible)

    def _check_event_time_only_metadata(self, record: NormalizedDataRecord) -> None:
        if record.availability.available_at is None:
            raise PointInTimeMissingAvailabilityError(str(record.record_id))
        if record.revision.available_at is None:
            raise PointInTimeMissingAvailabilityError(str(record.record_id))


__all__ = ["PointInTimeDataBoundary"]
