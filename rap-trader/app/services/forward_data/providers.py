"""Forward data provider helpers and fake test providers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from app.domain.models.forward_data import ForwardDataProvider, ForwardDataSource, ForwardMarketObservation
from app.services.forward_data.errors import InvalidSourceError


class FakeForwardDataProvider(ForwardDataProvider):
    """Deterministic fake forward provider for tests.

    Marked explicitly as TEST/SIMULATED so it can never masquerade as live data.
    """

    def __init__(self, observations: Sequence[ForwardMarketObservation]) -> None:
        self._observations = tuple(observations)

    def fetch_since(self, *, source: ForwardDataSource, since: datetime) -> Sequence[ForwardMarketObservation]:
        self._assert_fake(source)
        since = since.astimezone(UTC)
        return tuple(obs for obs in self._observations if obs.received_at >= since)

    def poll(self, *, source: ForwardDataSource) -> Sequence[ForwardMarketObservation]:
        self._assert_fake(source)
        return tuple(self._observations)

    @staticmethod
    def _assert_fake(source: ForwardDataSource) -> None:
        if source.environment not in {"TEST", "SIMULATED"}:
            raise InvalidSourceError("Fake providers must use TEST or SIMULATED environment sources.")
        if source.provider_name != "fake_forward_provider":
            raise InvalidSourceError("Fake providers must use the canonical fake provider name.")


class SimulatedForwardDataProvider(ForwardDataProvider):
    """Historical backfill provider for development/test purposes.

    Explicitly marked SIMULATED so historical backfill cannot masquerade as LIVE.
    """

    def __init__(self, observations: Sequence[ForwardMarketObservation]) -> None:
        self._observations = tuple(observations)

    def fetch_since(self, *, source: ForwardDataSource, since: datetime) -> Sequence[ForwardMarketObservation]:
        self._assert_simulated(source)
        since = since.astimezone(UTC)
        return tuple(obs for obs in self._observations if obs.received_at >= since)

    def poll(self, *, source: ForwardDataSource) -> Sequence[ForwardMarketObservation]:
        self._assert_simulated(source)
        return tuple(self._observations)

    @staticmethod
    def _assert_simulated(source: ForwardDataSource) -> None:
        if source.environment != "SIMULATED":
            raise InvalidSourceError("Simulated providers must use SIMULATED environment sources.")
        if source.provider_name != "simulated_forward_provider":
            raise InvalidSourceError("Simulated providers must use the canonical simulated provider name.")


__all__ = [
    "FakeForwardDataProvider",
    "SimulatedForwardDataProvider",
]
