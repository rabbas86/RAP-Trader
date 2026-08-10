"""Read-only Data Platform endpoints (Phase 8A).

These endpoints expose the Unified Research Data Platform as a read-only
query boundary. They do not accept mutations, credentials, or trade directives.
All output is research-only, deterministic, and offline by default.
"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domain.models.data_platform import (
    DataDomain,
    DataPlatformError,
    ResearchDataSnapshot,
    SnapshotErrorCode,
)
from app.services.data_platform import (
    DataPlatformService,
    SnapshotRequest,
)

router = APIRouter(prefix="/data-platform", tags=["data-platform"])


@lru_cache
def get_data_platform_service() -> DataPlatformService:
    return DataPlatformService()


ServiceDep = Annotated[DataPlatformService, Depends(get_data_platform_service)]


@router.get("/health")
def health(service: ServiceDep) -> dict[str, str]:
    """Return data-platform health status."""
    info = service.health()
    return {"status": info["status"], "platform_version": info["platform_version"]}


@router.get("/sources")
def sources(service: ServiceDep) -> dict[str, list[dict[str, str]]]:
    """List registered data sources."""
    return {"sources": service.sources()}


@router.get("/domains")
def domains(service: ServiceDep) -> dict[str, list[str]]:
    """List supported data domains."""
    return {"domains": service.domains()}


@router.get("/series")
def series(
    service: ServiceDep,
    domain: Annotated[DataDomain | None, Query()] = None,
    symbol: Annotated[str | None, Query(min_length=1)] = None,
    limit: Annotated[int | None, Query(gt=0, le=100_000)] = None,
) -> dict[str, list[dict[str, object]]]:
    """Query normalized records with point-in-time and domain filters."""
    try:
        records = service.query_records(domain=domain, symbol=symbol, limit=limit)
        return {"records": [record.model_dump(mode="json") for record in records]}
    except DataPlatformError as exc:
        _raise_http_error(exc)
        raise  # unreachable; satisfies type checker


@router.get("/calendar")
def calendar(
    service: ServiceDep,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
    event_type: Annotated[str | None, Query()] = None,
) -> dict[str, list[dict[str, object]]]:
    """Return calendar events within an optional time window."""
    events = service.calendar_events(start=start, end=end, event_type=event_type)
    return {"events": [event.model_dump(mode="json") for event in events]}


@router.post("/snapshot", response_model=ResearchDataSnapshot)
def snapshot(request: SnapshotRequest, service: ServiceDep) -> ResearchDataSnapshot:
    """Produce a point-in-time-safe, deterministic research data snapshot."""
    try:
        return service.snapshot(request)
    except DataPlatformError as exc:
        status = 404 if exc.code is SnapshotErrorCode.SOURCE_NOT_AVAILABLE else 400
        raise HTTPException(
            status_code=status,
            detail={"code": exc.code.value, "safe_message": exc.safe_message},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_REQUEST", "safe_message": "Invalid data platform request"},
        ) from exc


def _raise_http_error(exc: DataPlatformError) -> None:
    status = 404 if exc.code is SnapshotErrorCode.SOURCE_NOT_AVAILABLE else 400
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code.value, "safe_message": exc.safe_message},
    )
