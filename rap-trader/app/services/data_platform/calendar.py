"""Offline research calendar helpers."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta


class ResearchCalendarService:
    def is_business_day(self, value: date) -> bool:
        return value.weekday() < 5

    def business_days(self, start: date, end: date) -> tuple[date, ...]:
        if start > end:
            raise ValueError("start cannot be after end")
        days, current = [], start
        while current <= end:
            if self.is_business_day(current):
                days.append(current)
            current += timedelta(days=1)
        return tuple(days)

    def canonical_release_time(self, value: date, hour: int = 0, minute: int = 0) -> datetime:
        return datetime.combine(value, time(hour, minute), tzinfo=UTC)


CalendarService = ResearchCalendarService
__all__ = ["CalendarService", "ResearchCalendarService"]
