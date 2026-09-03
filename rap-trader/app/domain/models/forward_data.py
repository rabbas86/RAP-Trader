"""Phase 17A: Forward data ingestion contracts.

This module defines the immutable canonical contracts for received forward
market data. It does not fetch data, place orders, or connect brokers.

Contracts
---------
* :class:`ForwardDataSource` — immutable provider/source identity.
* :class:`ForwardDataSession` — minimal immutable forward validation session.
* :class:`ObservationStatus` — canonical observation lifecycle states.
* :class:`ForwardMarketObservation` — immutable canonical forward observation.
* :class:`ForwardDataObservation` — alias preserving provider-neutral naming.
* :class:`ForwardIngestionResult` — deterministic ingestion result summary.

All contracts permanently enforce research-only, paper-only operation and
``suitable_for_live_trading=False``. No broker, execution, order, risk, or
portfolio components are imported or invoked from this module.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from re import search
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.canonical import sha256_fingerprint
from app.domain.models.market_data import Symbol, Timeframe, UtcDatetime, _require_aware_utc

FORWARD_SCHEMA_VERSION: Literal["1.0"] = "1.0"
FORWARD_ID_NAMESPACE = "7d9c24a1-4b2f-5e6d-8a7b-9c0d1e2f3a4b"

_VALID_FEED_TYPES = frozenset({"bars", "quotes", "news"})
_VALID_ENVIRONMENTS = frozenset({"LIVE", "DELAYED", "SIMULATED", "TEST"})
_VALID_BAR_STATUSES = frozenset({"in_progress", "final", "corrected"})
_VALID_OBSERVATION_TYPES = frozenset({"market_bar", "quote", "news"})


class ForwardDataErrorCode(StrEnum):
    INVALID_SOURCE = "INVALID_SOURCE"
    NAIVE_TIMESTAMP = "NAIVE_TIMESTAMP"
    INVALID_EVENT_INTERVAL = "INVALID_EVENT_INTERVAL"
    INVALID_OHLC = "INVALID_OHLC"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    UNSUPPORTED_TIMEFRAME = "UNSUPPORTED_TIMEFRAME"
    WRONG_SYMBOL = "WRONG_SYMBOL"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    REVISION_MISMATCH = "REVISION_MISMATCH"
    WRONG_ARTIFACT_TYPE = "WRONG_ARTIFACT_TYPE"
    UNSUPPORTED_OBSERVATION_TYPE = "UNSUPPORTED_OBSERVATION_TYPE"
    CORRUPT_ARTIFACT = "CORRUPT_ARTIFACT"


class ForwardDataError(Exception):
    """Stable public error with private diagnostic detail kept out of API output."""

    def __init__(
        self,
        code: ForwardDataErrorCode | str,
        safe_message: str,
        *,
        retryable: bool = False,
        internal_detail: str | None = None,
    ) -> None:
        self.code = ForwardDataErrorCode(code)
        self.safe_message = safe_message
        self.retryable = retryable
        self.internal_detail = internal_detail
        super().__init__(safe_message)


class _ForwardFrozenModel(BaseModel):
    """Base frozen contract for Phase 17A models."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1, max_length=64, default=FORWARD_SCHEMA_VERSION)
    research_only: bool = True
    paper_trading_only: bool = True
    suitable_for_live_trading: bool = False


def _normalize_forward_timestamp(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return _require_aware_utc(cast(datetime, value))


def _coerce_string_sequence(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


def _coerce_symbol_sequence(value: object) -> tuple[Symbol, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(Symbol(str(item)) for item in value)
    raise TypeError("sequence fields must be a list or tuple")


# ---------------------------------------------------------------------------
# ForwardDataSource
# ---------------------------------------------------------------------------


class ForwardDataSource(_ForwardFrozenModel):
    """Immutable provider/source identity for forward observations.

    Two semantically identical source configurations must produce the same
    ``source_id``. Secrets/credentials are explicitly forbidden.
    """

    source_id: str
    provider_name: str = Field(min_length=1, max_length=128)
    provider_version: str = Field(min_length=1, max_length=64)
    feed_name: str = Field(min_length=1, max_length=128)
    feed_type: str = Field(min_length=1, max_length=64)
    environment: str = Field(min_length=1, max_length=32)
    timezone_semantics: str | None = Field(default=None, max_length=128)
    supports_provider_available_at: bool = False
    supports_sequence: bool = False
    adjustment_convention: str = Field(min_length=1, max_length=128)
    schema_version: str = Field(min_length=1, max_length=64)
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=4096)

    @field_validator("feed_type")
    @classmethod
    def validate_feed_type(cls, value: str) -> str:
        if value not in _VALID_FEED_TYPES:
            raise ValueError("feed_type must be one of the allowed feed types")
        return value

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        if value not in _VALID_ENVIRONMENTS:
            raise ValueError("environment must be one of the allowed environments")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise TypeError("source metadata must be a dict")
        serialized = repr(value)
        if len(serialized) > 4096:
            raise ValueError("source metadata is too large")
        if search(r"(?i)(password|passwd|token|api[_-]?key|secret|credential)", serialized):
            raise ValueError("credentials and secrets are forbidden in source metadata")
        return value

    @model_validator(mode="after")
    def validate_source_identity(self) -> ForwardDataSource:
        if self.source_id != self._canonical_source_id():
            raise ValueError("source_id does not match canonical source identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"source_id", "schema_version", "metadata"},
        )

    def _canonical_source_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @classmethod
    def create(
        cls,
        *,
        provider_name: str,
        provider_version: str,
        feed_name: str,
        feed_type: str,
        environment: str,
        adjustment_convention: str,
        producer: str,
        producer_version: str,
        timezone_semantics: str | None = None,
        supports_provider_available_at: bool = False,
        supports_sequence: bool = False,
        schema_version: str = FORWARD_SCHEMA_VERSION,
        metadata: dict[str, Any] | None = None,
    ) -> ForwardDataSource:
        safe_metadata = metadata or {}
        provisional = cls.model_construct(
            source_id="0" * 64,
            provider_name=provider_name,
            provider_version=provider_version,
            feed_name=feed_name,
            feed_type=feed_type,
            environment=environment,
            timezone_semantics=timezone_semantics,
            supports_provider_available_at=supports_provider_available_at,
            supports_sequence=supports_sequence,
            adjustment_convention=adjustment_convention,
            schema_version=schema_version,
            producer=producer,
            producer_version=producer_version,
            metadata=safe_metadata,
        )
        return cls(
            source_id=provisional._canonical_source_id(),
            provider_name=provisional.provider_name,
            provider_version=provisional.provider_version,
            feed_name=provisional.feed_name,
            feed_type=provisional.feed_type,
            environment=provisional.environment,
            timezone_semantics=provisional.timezone_semantics,
            supports_provider_available_at=provisional.supports_provider_available_at,
            supports_sequence=provisional.supports_sequence,
            adjustment_convention=provisional.adjustment_convention,
            schema_version=provisional.schema_version,
            producer=provisional.producer,
            producer_version=provisional.producer_version,
            metadata=provisional.metadata,
        )


# ---------------------------------------------------------------------------
# Forward session / run identity
# ---------------------------------------------------------------------------


class ForwardDataSession(_ForwardFrozenModel):
    """Minimal immutable identity for one forward ingestion session."""

    session_id: str
    started_at: UtcDatetime
    source_ids: tuple[str, ...]
    instruments: tuple[Symbol, ...]
    timeframes: tuple[str, ...]
    environment: str = Field(min_length=1, max_length=32)
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=1024)

    @field_validator("started_at", mode="before")
    @classmethod
    def normalize_started_at(cls, value: object) -> datetime:
        return _normalize_forward_timestamp(value)

    @field_validator("source_ids")
    @classmethod
    def validate_source_ids(cls, value: Sequence[str]) -> tuple[str, ...]:
        values = tuple(str(item) for item in value)
        if not values:
            raise ValueError("source_ids must not be empty")
        return values

    @field_validator("instruments", mode="before")
    @classmethod
    def coerce_instruments(cls, value: object) -> tuple[Symbol, ...]:
        return _coerce_symbol_sequence(value)

    @field_validator("timeframes", mode="before")
    @classmethod
    def coerce_timeframes(cls, value: object) -> tuple[str, ...]:
        values = _coerce_string_sequence(value)
        for item in values:
            if item not in getattr(Timeframe, "__args__", ()):
                raise ValueError(f"unsupported timeframe: {item}")
        return values

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, value: str) -> str:
        if value not in _VALID_ENVIRONMENTS:
            raise ValueError("environment must be one of the allowed environments")
        return value

    @model_validator(mode="after")
    def validate_session(self) -> ForwardDataSession:
        if self.session_id != self._canonical_session_id():
            raise ValueError("session_id does not match canonical session identity")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"session_id", "schema_version", "notes"},
        )

    def _canonical_session_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @classmethod
    def create(
        cls,
        *,
        started_at: datetime,
        source_ids: list[str] | tuple[str, ...],
        instruments: list[str] | tuple[str, ...],
        timeframes: list[str] | tuple[str, ...],
        environment: str,
        producer: str,
        producer_version: str,
        notes: str | None = None,
    ) -> ForwardDataSession:
        provisional = cls.model_construct(
            session_id="0" * 64,
            started_at=_normalize_forward_timestamp(started_at),
            source_ids=tuple(str(item) for item in source_ids),
            instruments=tuple(Symbol(str(item)) for item in instruments),
            timeframes=tuple(str(item) for item in timeframes),
            environment=environment,
            producer=producer,
            producer_version=producer_version,
            notes=notes,
        )
        return cls(
            session_id=provisional._canonical_session_id(),
            started_at=provisional.started_at,
            source_ids=provisional.source_ids,
            instruments=provisional.instruments,
            timeframes=provisional.timeframes,
            environment=provisional.environment,
            producer=provisional.producer,
            producer_version=provisional.producer_version,
            notes=provisional.notes,
        )


# ---------------------------------------------------------------------------
# Observation status/types
# ---------------------------------------------------------------------------


class ObservationStatus(StrEnum):
    """Explicit bar completion state for forward market observations."""

    IN_PROGRESS = "in_progress"
    FINAL = "final"
    CORRECTED = "corrected"


# ---------------------------------------------------------------------------
# Forward market observation
# ---------------------------------------------------------------------------


class ForwardMarketObservation(_ForwardFrozenModel):
    """Immutable canonical forward observation.

    The observation identity is derived from canonical source/event identity.
    ``received_at`` participates in identity only when a source embeds it in
    canonical semantics; otherwise it remains metadata.
    """

    observation_id: str
    session_id: str
    source_id: str
    symbol: Symbol
    observation_type: str = Field(min_length=1, max_length=64)
    timeframe: Timeframe
    interval_start: UtcDatetime
    interval_end: UtcDatetime
    event_time: UtcDatetime
    provider_available_at: UtcDatetime | None = None
    received_at: UtcDatetime
    normalized_at: UtcDatetime
    sequence: int | None = Field(default=None, ge=0)
    open: float | None = Field(default=None, allow_inf_nan=False)
    high: float | None = Field(default=None, allow_inf_nan=False)
    low: float | None = Field(default=None, allow_inf_nan=False)
    close: float | None = Field(default=None, allow_inf_nan=False)
    volume: int | None = Field(default=None, ge=0)
    status: str = Field(min_length=1, max_length=32)
    supersedes_observation_id: str | None = Field(default=None, max_length=64)
    revision_number: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict, max_length=8192)
    source_metadata: dict[str, Any] = Field(default_factory=dict, max_length=4096)
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=64)
    observation_hash: str

    @field_validator("event_time", "received_at", "normalized_at", mode="before")
    @classmethod
    def normalize_event_timestamps(cls, value: object) -> datetime:
        return _normalize_forward_timestamp(value)

    @field_validator("interval_start", "interval_end", "provider_available_at", mode="before")
    @classmethod
    def normalize_interval_timestamps(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return _normalize_forward_timestamp(value)

    @field_validator("observation_hash", mode="after")
    @classmethod
    def set_observation_hash(cls, value: str | None, info: Any) -> str:
        if value is not None:
            return value
        data = info.data
        data.setdefault("observation_id", "0" * 64)
        material = {key: value for key, value in data.items() if key not in {"observation_hash", "observation_id"}}
        return sha256_fingerprint(material)

    @field_validator("observation_type")
    @classmethod
    def validate_observation_type(cls, value: str) -> str:
        if value not in _VALID_OBSERVATION_TYPES:
            raise ValueError("observation_type must be one of the allowed observation types")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _VALID_BAR_STATUSES:
            raise ValueError("status must be one of the allowed observation statuses")
        return value

    @field_validator("symbol", mode="before")
    @classmethod
    def normalize_symbol(cls, value: object) -> Symbol:
        return Symbol(str(value))

    @model_validator(mode="after")
    def validate_observation(self) -> ForwardMarketObservation:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("forward observations are research-only")
        if self.interval_start >= self.interval_end:
            raise ValueError("interval_start must be before interval_end")
        if self.event_time < self.interval_start or self.event_time > self.interval_end:
            raise ValueError("event_time must be within the observation interval")
        if self.received_at < self.event_time:
            raise ValueError("received_at cannot be before event_time")
        if self.provider_available_at is not None and self.provider_available_at > self.received_at:
            raise ValueError("provider_available_at cannot be after received_at")
        if self.observation_type == "market_bar":
            self._validate_market_bar()
        if self.revision_number == 0 and self.status == "corrected":
            raise ValueError("revision 0 cannot use corrected status")
        if self.revision_number > 0 and self.status != "corrected":
            raise ValueError("revisions after 0 require corrected status")
        if self.revision_number > 0 and not self.supersedes_observation_id:
            raise ValueError("revisions after 0 require supersedes_observation_id")
        return self

    def _validate_market_bar(self) -> None:
        prices = {"open": self.open, "high": self.high, "low": self.low, "close": self.close}
        missing = [name for name, value in prices.items() if value is None]
        if missing:
            raise ValueError(f"market_bar observation is missing required fields: {', '.join(missing)}")
        if self.volume is None:
            raise ValueError("market_bar observation is missing required fields: volume")
        open_ = float(self.open)
        high = float(self.high)
        low = float(self.low)
        close = float(self.close)
        volume = int(self.volume)
        if high < low or high < open_ or high < close:
            raise ValueError("high must be greater than or equal to low, open, and close")
        if low > open_ or low > close:
            raise ValueError("low must be less than or equal to open and close")
        if volume < 0:
            raise ValueError("volume must be greater than or equal to 0")

    def provider_latency(self) -> float | None:
        """Return deterministic provider latency when available."""
        if self.provider_available_at is None:
            return None
        return (self.received_at - self.provider_available_at).total_seconds()

    def _identity_material(self) -> dict[str, Any]:
        material = self.model_dump(
            mode="json",
            exclude={
                "observation_id",
                "observation_hash",
                "normalized_at",
                "received_at",
                "source_metadata",
                "producer",
                "producer_version",
                "supersedes_observation_id",
                "revision_number",
                "schema_version",
            },
        )
        if material.get("provider_available_at") is None:
            material.pop("provider_available_at", None)
        material.pop("sequence", None)
        return material

    def _canonical_observation_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @model_validator(mode="after")
    def validate_observation_identity(self) -> ForwardMarketObservation:
        if self.observation_id != self._canonical_observation_id():
            raise ValueError("observation_id does not match canonical observation identity")
        return self

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        source_id: str,
        symbol: str | Symbol,
        observation_type: str,
        timeframe: str,
        interval_start: datetime,
        interval_end: datetime,
        event_time: datetime,
        received_at: datetime,
        normalized_at: datetime,
        status: str = "final",
        open_: float | None = None,
        high: float | None = None,
        low: float | None = None,
        close: float | None = None,
        volume: int | None = None,
        provider_available_at: datetime | None = None,
        sequence: int | None = None,
        supersedes_observation_id: str | None = None,
        revision_number: int = 0,
        payload: dict[str, Any] | None = None,
        source_metadata: dict[str, Any] | None = None,
        producer: str = "phase17a",
        producer_version: str = "1.0",
    ) -> ForwardMarketObservation:
        material = {
            "session_id": session_id,
            "source_id": source_id,
            "symbol": str(symbol),
            "observation_type": observation_type,
            "timeframe": timeframe,
            "interval_start": _normalize_forward_timestamp(interval_start),
            "interval_end": _normalize_forward_timestamp(interval_end),
            "event_time": _normalize_forward_timestamp(event_time),
            "received_at": _normalize_forward_timestamp(received_at),
            "normalized_at": _normalize_forward_timestamp(normalized_at),
            "status": status,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "provider_available_at": provider_available_at,
            "sequence": sequence,
            "supersedes_observation_id": supersedes_observation_id,
            "revision_number": revision_number,
            "payload": payload or {},
            "source_metadata": source_metadata or {},
            "producer": producer,
            "producer_version": producer_version,
            "schema_version": FORWARD_SCHEMA_VERSION,
            "research_only": True,
            "paper_trading_only": True,
            "suitable_for_live_trading": False,
        }
        provisional = cls.model_construct(**{**material, "observation_id": "0" * 64})  # type: ignore[arg-type]
        observation_id = provisional._canonical_observation_id()
        observation_hash = sha256_fingerprint(provisional._identity_material())
        return cls(**{**material, "observation_id": observation_id, "observation_hash": observation_hash})  # type: ignore[arg-type]


ForwardDataObservation = ForwardMarketObservation


# ---------------------------------------------------------------------------
# Ingestion result
# ---------------------------------------------------------------------------


class ForwardIngestionResult(_ForwardFrozenModel):
    """Deterministic summary of an ingestion run."""

    result_id: str
    session_id: str
    source_id: str
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    conflict_count: int = Field(ge=0)
    accepted_observation_ids: tuple[str, ...] = ()
    persisted_artifact_ids: tuple[str, ...] = ()
    receipt_window_start: UtcDatetime | None = None
    receipt_window_end: UtcDatetime | None = None
    warnings: tuple[str, ...] = ()
    producer: str = Field(min_length=1, max_length=128)
    producer_version: str = Field(min_length=1, max_length=64)

    @field_validator("receipt_window_start", "receipt_window_end", mode="before")
    @classmethod
    def normalize_receipt_window(cls, value: object) -> datetime | None:
        if value is None:
            return None
        return _normalize_forward_timestamp(value)

    @model_validator(mode="after")
    def validate_result(self) -> ForwardIngestionResult:
        if self.result_id != self._canonical_result_id():
            raise ValueError("result_id does not match canonical result identity")
        if self.accepted_count != len(self.accepted_observation_ids):
            raise ValueError("accepted_count must match accepted_observation_ids length")
        if self.accepted_count != len(self.persisted_artifact_ids):
            raise ValueError("accepted_count must match persisted_artifact_ids length")
        return self

    def _identity_material(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"result_id", "schema_version"},
        )

    def _canonical_result_id(self) -> str:
        return sha256_fingerprint(self._identity_material())

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        source_id: str,
        accepted_count: int,
        duplicate_count: int,
        rejected_count: int,
        conflict_count: int,
        accepted_observation_ids: list[str] | tuple[str, ...],
        persisted_artifact_ids: list[str] | tuple[str, ...],
        receipt_window_start: datetime | None = None,
        receipt_window_end: datetime | None = None,
        warnings: list[str] | tuple[str, ...] | None = None,
        producer: str = "phase17a",
        producer_version: str = "1.0",
    ) -> ForwardIngestionResult:
        material = {
            "session_id": session_id,
            "source_id": source_id,
            "accepted_count": accepted_count,
            "duplicate_count": duplicate_count,
            "rejected_count": rejected_count,
            "conflict_count": conflict_count,
            "accepted_observation_ids": tuple(accepted_observation_ids),
            "persisted_artifact_ids": tuple(persisted_artifact_ids),
            "receipt_window_start": receipt_window_start,
            "receipt_window_end": receipt_window_end,
            "warnings": tuple(warnings or []),
            "producer": producer,
            "producer_version": producer_version,
            "schema_version": FORWARD_SCHEMA_VERSION,
            "research_only": True,
            "paper_trading_only": True,
            "suitable_for_live_trading": False,
        }
        provisional = cls.model_construct(**{**material, "result_id": "0" * 64})
        return cls(**{**material, "result_id": provisional._canonical_result_id()})


# ---------------------------------------------------------------------------
# Provider-neutral protocol
# ---------------------------------------------------------------------------


class ForwardDataProvider:
    """Minimal provider-neutral forward data protocol.

    Production implementations may perform network access. Core ingestion
    logic must not depend on network behavior.
    """

    def fetch_since(self, *, source: ForwardDataSource, since: datetime) -> Sequence[ForwardMarketObservation]:
        raise NotImplementedError

    def poll(self, *, source: ForwardDataSource) -> Sequence[ForwardMarketObservation]:
        raise NotImplementedError


__all__ = [
    "FORWARD_ID_NAMESPACE",
    "FORWARD_SCHEMA_VERSION",
    "ForwardDataError",
    "ForwardDataErrorCode",
    "ForwardDataObservation",
    "ForwardDataProvider",
    "ForwardDataSession",
    "ForwardDataSource",
    "ForwardIngestionResult",
    "ForwardMarketObservation",
    "ObservationStatus",
]
