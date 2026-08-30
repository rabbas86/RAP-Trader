"""Immutable point-in-time data snapshot artifact assembly."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType, ProvenanceReference

if TYPE_CHECKING:
    from app.domain.models.historical_replay import HistoricalReplaySpecification
    from app.services.historical.boundary import PointInTimeDataBoundary
    from app.services.historical.clock import HistoricalClock


class PointInTimeDataSnapshot:
    """Immutable snapshot of data visible at a simulated historical time."""

    def __init__(
        self,
        *,
        snapshot_id: str,
        simulated_at: datetime,
        replay_specification_id: str,
        clock_start: datetime,
        clock_end: datetime,
        point_in_time_policy: str,
        available_at_aware: bool,
        event_time_only: bool,
        record_identities: tuple[str, ...],
        bar_identities: tuple[str, ...],
        observation_identities: tuple[str, ...],
        source_versions: dict[str, str],
        input_fingerprints: tuple[str, ...],
        availability_policy: str,
        methodology_version: str = "phase16b-1.0",
    ) -> None:
        self.snapshot_id = snapshot_id
        self.simulated_at = simulated_at
        self.replay_specification_id = replay_specification_id
        self.clock_start = clock_start
        self.clock_end = clock_end
        self.point_in_time_policy = point_in_time_policy
        self.available_at_aware = available_at_aware
        self.event_time_only = event_time_only
        self.record_identities = record_identities
        self.bar_identities = bar_identities
        self.observation_identities = observation_identities
        self.source_versions = source_versions
        self.input_fingerprints = input_fingerprints
        self.availability_policy = availability_policy
        self.methodology_version = methodology_version

    def to_artifact_payload(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "simulated_at": self.simulated_at.isoformat(),
            "replay_specification_id": self.replay_specification_id,
            "clock_start": self.clock_start.isoformat(),
            "clock_end": self.clock_end.isoformat(),
            "point_in_time_policy": self.point_in_time_policy,
            "available_at_aware": self.available_at_aware,
            "event_time_only": self.event_time_only,
            "record_identities": self.record_identities,
            "bar_identities": self.bar_identities,
            "observation_identities": self.observation_identities,
            "source_versions": self.source_versions,
            "input_fingerprints": self.input_fingerprints,
            "availability_policy": self.availability_policy,
            "methodology_version": self.methodology_version,
        }

    def to_envelope(self, *, producer_version: str, provenance_references: tuple[ProvenanceReference, ...]) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=self.to_artifact_payload(),
            artifact_type=ArtifactType.POINT_IN_TIME_DATA_SNAPSHOT,
            logical_as_of=self.simulated_at,
            producer_version=producer_version,
            provenance_references=provenance_references,
        )


def build_snapshot(
    *,
    clock: HistoricalClock,
    specification: HistoricalReplaySpecification,
    boundary: PointInTimeDataBoundary,
    record_identities: tuple[str, ...],
    bar_identities: tuple[str, ...] = (),
    observation_identities: tuple[str, ...] = (),
    source_versions: dict[str, str] | None = None,
    input_fingerprints: tuple[str, ...] = (),
) -> PointInTimeDataSnapshot:
    source_versions = source_versions or {}
    availability_policy = "available_at_aware" if specification.point_in_time_policy == "available_at_aware" else "event_time_only"
    identity = {
        "simulated_at": clock.now.isoformat(),
        "replay_specification_id": specification.specification_id,
        "clock_start": clock.start.isoformat(),
        "clock_end": clock.end.isoformat(),
        "point_in_time_policy": specification.point_in_time_policy,
        "record_identities": tuple(sorted(record_identities)),
        "bar_identities": tuple(sorted(bar_identities)),
        "observation_identities": tuple(sorted(observation_identities)),
        "source_versions": tuple(sorted(source_versions.items())),
        "input_fingerprints": tuple(sorted(input_fingerprints)),
        "availability_policy": availability_policy,
        "methodology_version": "phase16b-1.0",
    }
    snapshot_id = sha256_fingerprint(identity)
    return PointInTimeDataSnapshot(
        snapshot_id=snapshot_id,
        simulated_at=clock.now,
        replay_specification_id=specification.specification_id,
        clock_start=clock.start,
        clock_end=clock.end,
        point_in_time_policy=specification.point_in_time_policy,
        available_at_aware=specification.point_in_time_policy == "available_at_aware",
        event_time_only=specification.point_in_time_policy == "event_time_only",
        record_identities=tuple(sorted(record_identities)),
        bar_identities=tuple(sorted(bar_identities)),
        observation_identities=tuple(sorted(observation_identities)),
        source_versions=dict(sorted(source_versions.items())),
        input_fingerprints=tuple(sorted(input_fingerprints)),
        availability_policy=availability_policy,
    )


__all__ = ["PointInTimeDataSnapshot", "build_snapshot"]
