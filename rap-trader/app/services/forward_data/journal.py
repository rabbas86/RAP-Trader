"""Append-only journal for Phase 17A forward observations."""

from __future__ import annotations

from collections import OrderedDict
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from app.domain.models.forward_data import ForwardMarketObservation
from app.services.artifacts.errors import ArtifactCorruptedError, ArtifactNotFoundError


class ForwardDataJournal:
    """Rebuildable append-only journal over immutable forward observations.

    The journal maintains deterministic secondary indexes. It never overwrites
    persisted observations. After restart it rebuilds indexes from an
    ``ArtifactStore``.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._lock = RLock()
        self._by_id: OrderedDict[str, ForwardMarketObservation] = OrderedDict()
        self._by_received: OrderedDict[datetime, list[str]] = OrderedDict()
        self._by_event: OrderedDict[datetime, list[str]] = OrderedDict()
        self._by_symbol: dict[str, list[str]] = {}
        self._by_source: dict[str, list[str]] = {}
        self._by_type: dict[str, list[str]] = {}
        self._rebuilt = False

    def ensure_rebuilt(self) -> None:
        if self._rebuilt:
            return
        self.rebuild()

    def rebuild(self) -> None:
        with self._lock:
            self._by_id = OrderedDict()
            self._by_received = OrderedDict()
            self._by_event = OrderedDict()
            self._by_symbol = {}
            self._by_source = {}
            self._by_type = {}
            for artifact_id in self._store.list_ids(filters={"artifact_type": "forward_data_observation"}):
                envelope = self._store.get(artifact_id)
                try:
                    observation = ForwardMarketObservation.model_validate(envelope.payload)
                except Exception as exc:
                    raise ArtifactCorruptedError(
                        artifact_id=artifact_id,
                        reason=f"invalid forward observation payload: {exc}",
                    ) from exc
                self._index(observation)
            self._rebuilt = True

    def append(self, observation: ForwardMarketObservation) -> ForwardMarketObservation:
        self.ensure_rebuilt()
        with self._lock:
            self._index(observation)
            return observation

    def get(self, observation_id: str) -> ForwardMarketObservation:
        self.ensure_rebuilt()
        with self._lock:
            observation = self._by_id.get(observation_id)
            if observation is None:
                raise ArtifactNotFoundError(artifact_id=observation_id)
            return observation

    def query_received_before(self, timestamp: datetime) -> tuple[ForwardMarketObservation, ...]:
        self.ensure_rebuilt()
        timestamp = timestamp.astimezone(UTC)
        with self._lock:
            result: list[ForwardMarketObservation] = []
            for observed_at, ids in self._by_received.items():
                if observed_at > timestamp:
                    continue
                for observation_id in ids:
                    result.append(self._by_id[observation_id])
            return tuple(sorted(result, key=lambda item: (item.received_at, item.observation_id)))

    def query_event_interval(self, *, start: datetime, end: datetime) -> tuple[ForwardMarketObservation, ...]:
        self.ensure_rebuilt()
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        with self._lock:
            result: list[ForwardMarketObservation] = []
            for event_time, ids in self._by_event.items():
                if event_time < start or event_time > end:
                    continue
                for observation_id in ids:
                    result.append(self._by_id[observation_id])
            return tuple(sorted(result, key=lambda item: (item.event_time, item.observation_id)))

    def query_symbol(self, symbol: str) -> tuple[ForwardMarketObservation, ...]:
        self.ensure_rebuilt()
        with self._lock:
            ids = self._by_symbol.get(str(symbol).upper(), [])
            return tuple(
                sorted((self._by_id[observation_id] for observation_id in ids), key=lambda item: (item.event_time, item.observation_id))
            )

    def query_source(self, source_id: str) -> tuple[ForwardMarketObservation, ...]:
        self.ensure_rebuilt()
        with self._lock:
            ids = self._by_source.get(source_id, [])
            return tuple(
                sorted((self._by_id[observation_id] for observation_id in ids), key=lambda item: (item.received_at, item.observation_id))
            )

    def query_observation_type(self, observation_type: str) -> tuple[ForwardMarketObservation, ...]:
        self.ensure_rebuilt()
        with self._lock:
            ids = self._by_type.get(observation_type, [])
            return tuple(
                sorted((self._by_id[observation_id] for observation_id in ids), key=lambda item: (item.received_at, item.observation_id))
            )

    def latest_revision(
        self, *, source_id: str, symbol: str, interval_start: datetime, interval_end: datetime, observation_type: str
    ) -> ForwardMarketObservation:
        self.ensure_rebuilt()
        candidates = tuple(
            observation
            for observation in self.query_event_interval(start=interval_start, end=interval_end)
            if observation.source_id == source_id
            and str(observation.symbol) == str(symbol).upper()
            and observation.observation_type == observation_type
        )
        if not candidates:
            raise ArtifactNotFoundError(artifact_id="unknown")
        return max(candidates, key=lambda item: (item.revision_number, item.received_at, item.observation_id))

    def _index(self, observation: ForwardMarketObservation) -> None:
        self._by_id[observation.observation_id] = observation
        self._by_received.setdefault(observation.received_at, []).append(observation.observation_id)
        self._by_event.setdefault(observation.event_time, []).append(observation.observation_id)
        self._by_symbol.setdefault(str(observation.symbol), []).append(observation.observation_id)
        self._by_source.setdefault(observation.source_id, []).append(observation.observation_id)
        self._by_type.setdefault(observation.observation_type, []).append(observation.observation_id)


__all__ = ["ForwardDataJournal"]
