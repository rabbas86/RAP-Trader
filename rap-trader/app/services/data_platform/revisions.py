"""Point-in-time revision selection."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from app.domain.models.data_platform import EconomicObservation, NormalizedDataRecord
from app.domain.models.market_data import _require_aware_utc

RevisionItem = NormalizedDataRecord | EconomicObservation


class PointInTimeRevisionService:
    @staticmethod
    def _available(item: RevisionItem) -> datetime:
        return item.availability.available_at if isinstance(item, NormalizedDataRecord) else item.available_at

    @staticmethod
    def _number(item: RevisionItem) -> int:
        return item.revision.revision_number if isinstance(item, NormalizedDataRecord) else item.revision_number

    def select(self, revisions: Iterable[RevisionItem], as_of: datetime) -> RevisionItem:
        as_of = _require_aware_utc(as_of)
        items = tuple(revisions)
        future = tuple(item for item in items if self._available(item) > as_of)
        eligible = tuple(item for item in items if self._available(item) <= as_of)
        if not eligible:
            if future:
                raise ValueError("future revision rejected: no revision was available as of the requested time")
            raise ValueError("no revisions supplied")
        return max(eligible, key=lambda item: (self._available(item), self._number(item)))

    select_as_of = select

    def first_release(self, revisions: Iterable[RevisionItem], as_of: datetime | None = None) -> RevisionItem:
        items = tuple(revisions)
        if as_of is not None:
            cutoff = _require_aware_utc(as_of)
            items = tuple(item for item in items if self._available(item) <= cutoff)
        if not items:
            raise ValueError("no release was available")
        return min(items, key=lambda item: (self._number(item), self._available(item)))

    select_first_release = first_release
    select_latest_revision = select


RevisionService = PointInTimeRevisionService
__all__ = ["PointInTimeRevisionService", "RevisionService"]
