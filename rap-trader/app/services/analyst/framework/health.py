"""Shared analyst health reporting."""

from datetime import UTC, datetime

from app.domain.models.analyst import AnalystHealth


def build_health(
    analyst_id: str, detail: str, *, configured: bool = True, reachable: bool | None = True, status: str = "healthy"
) -> AnalystHealth:
    return AnalystHealth(
        analyst_id=analyst_id, configured=configured, reachable=reachable, checked_at=datetime.now(UTC), status=status, detail=detail
    )
