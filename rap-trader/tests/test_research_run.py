from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domain.canonical import canonical_bytes, sha256_fingerprint
from app.domain.models.research_run import GENESIS_EVENT_HASH, ResearchRun, ResearchRunStatus, RunEvent
from app.services.data_platform.fingerprint import canonical_bytes as platform_canonical_bytes
from app.services.data_platform.fingerprint import sha256_fingerprint as platform_sha256_fingerprint

AS_OF = datetime(2026, 8, 11, 18, 0, tzinfo=UTC)
RECORDED = datetime(2026, 8, 11, 18, 1, tzinfo=UTC)
CORRELATION_ID = UUID("7bc77dd7-1cd9-4f21-ad7c-119d92a97862")


def _event(**overrides: object) -> RunEvent:
    values: dict[str, object] = {
        "run_id": UUID("93af76f2-30bc-5ef4-b864-52afcc27a71a"),
        "sequence": 1,
        "correlation_id": CORRELATION_ID,
        "causation_id": None,
        "logical_as_of": AS_OF,
        "recorded_at": RECORDED,
        "event_type": "run.created",
        "producer": "test",
        "producer_version": "1.0",
        "payload_reference": "artifact://payload/1",
        "payload_hash": "a" * 64,
        "prior_event_hash": GENESIS_EVENT_HASH,
    }
    values.update(overrides)
    return RunEvent.create(**values)


def test_run_lifecycle_and_permanent_safety() -> None:
    run = ResearchRun.create(
        correlation_id=CORRELATION_ID,
        logical_as_of=AS_OF,
        recorded_at=RECORDED,
        producer="test",
        producer_version="1.0",
    )
    assert run.transition_to(ResearchRunStatus.RUNNING).transition_to(ResearchRunStatus.COMPLETED).status is ResearchRunStatus.COMPLETED
    with pytest.raises(ValidationError, match="logical_as_of cannot be after recorded_at"):
        ResearchRun.create(
            correlation_id=CORRELATION_ID,
            logical_as_of=RECORDED + timedelta(seconds=1),
            recorded_at=RECORDED,
            producer="test",
            producer_version="1.0",
        )
    for unsafe_update in (
        {"research_only": False},
        {"paper_trading_only": False},
        {"suitable_for_live_trading": True},
    ):
        with pytest.raises(ValidationError):
            run.model_copy(update=unsafe_update)
    with pytest.raises(ValidationError, match="invalid research run lifecycle transition"):
        run.transition_to(ResearchRunStatus.COMPLETED)


@pytest.mark.parametrize("field", ["logical_as_of", "recorded_at"])
def test_research_run_rejects_naive_timestamps(field: str) -> None:
    values = {
        "correlation_id": CORRELATION_ID,
        "logical_as_of": AS_OF,
        "recorded_at": RECORDED,
        "producer": "test",
        "producer_version": "1.0",
    }
    values[field] = values[field].replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone information"):
        ResearchRun.create(**values)


def test_event_rejects_invalid_sequence_hash_chain_self_causation_and_live_suitability() -> None:
    with pytest.raises(ValidationError):
        _event(sequence=0)
    with pytest.raises(ValidationError):
        _event(payload_hash="")
    with pytest.raises(ValidationError, match="non-first event must reference a prior event hash"):
        _event(sequence=2)
    valid = _event()
    self_caused = valid.model_dump()
    self_caused["causation_id"] = valid.event_id
    with pytest.raises(ValidationError, match="event cannot cause itself"):
        RunEvent.model_validate(self_caused)
    with pytest.raises(ValidationError):
        _event(suitable_for_live_trading=True)


@pytest.mark.parametrize("field", ["logical_as_of", "recorded_at"])
def test_run_event_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValueError, match="timezone information"):
        _event(**{field: AS_OF.replace(tzinfo=None)})


@pytest.mark.parametrize("prior_event_hash", [None, ""])
def test_run_event_rejects_missing_prior_event_hash(prior_event_hash: object) -> None:
    values = {
        "run_id": UUID("93af76f2-30bc-5ef4-b864-52afcc27a71a"),
        "sequence": 1,
        "correlation_id": CORRELATION_ID,
        "causation_id": None,
        "logical_as_of": AS_OF,
        "recorded_at": RECORDED,
        "event_type": "run.created",
        "producer": "test",
        "producer_version": "1.0",
        "payload_reference": "artifact://payload/1",
        "payload_hash": "a" * 64,
    }
    if prior_event_hash is not None:
        values["prior_event_hash"] = prior_event_hash

    with pytest.raises(ValidationError):
        RunEvent.create(**values)


def test_event_copy_cannot_change_permanent_safety_flags() -> None:
    event = _event()
    for unsafe_update in (
        {"research_only": False},
        {"paper_trading_only": False},
        {"suitable_for_live_trading": True},
    ):
        with pytest.raises(ValidationError):
            event.model_copy(update=unsafe_update)


def test_event_identity_uses_normalized_timestamp_material() -> None:
    offset = timezone(timedelta(hours=3))
    utc_event = _event(logical_as_of=AS_OF, recorded_at=RECORDED)
    offset_event = _event(logical_as_of=AS_OF.astimezone(offset), recorded_at=RECORDED.astimezone(offset))
    string_event = _event(logical_as_of="2026-08-11T21:00:00+03:00", recorded_at="2026-08-11T18:01:00Z")

    assert utc_event._identity_material() == offset_event._identity_material() == string_event._identity_material()
    assert utc_event.event_id == offset_event.event_id == string_event.event_id
    assert utc_event.event_hash == offset_event.event_hash == string_event.event_hash


def test_event_identity_is_independent_of_input_mapping_insertion_order() -> None:
    values = {
        "run_id": UUID("93af76f2-30bc-5ef4-b864-52afcc27a71a"),
        "sequence": 1,
        "correlation_id": CORRELATION_ID,
        "causation_id": None,
        "logical_as_of": AS_OF,
        "recorded_at": RECORDED,
        "event_type": "run.created",
        "producer": "test",
        "producer_version": "1.0",
        "payload_reference": "artifact://payload/1",
        "payload_hash": "a" * 64,
        "prior_event_hash": GENESIS_EVENT_HASH,
    }
    reversed_values = dict(reversed(tuple(values.items())))

    first = RunEvent.create(**values)
    second = RunEvent.create(**reversed_values)

    assert first._identity_material() == second._identity_material()
    assert first.event_id == second.event_id
    assert first.event_hash == second.event_hash


def test_canonical_sets_are_type_aware_and_insertion_order_independent() -> None:
    first = {"values": {1, "1", 2, "2"}}
    reverse_order: set[int | str] = set()
    for value in ("2", 2, "1", 1):
        reverse_order.add(value)
    second = {"values": reverse_order}
    expected = b'{"values":[1,2,"1","2"]}'

    assert canonical_bytes(first) == canonical_bytes(second) == expected
    assert sha256_fingerprint(first) == sha256_fingerprint(second)
    assert platform_canonical_bytes(first) == platform_canonical_bytes(second) == expected
    assert platform_sha256_fingerprint(first) == platform_sha256_fingerprint(second) == sha256_fingerprint(first)
    assert canonical_bytes({1}) != canonical_bytes({"1"})
    assert sha256_fingerprint({1}) != sha256_fingerprint({"1"})


def test_canonical_set_is_hash_seed_independent() -> None:
    script = (
        "from app.domain.canonical import canonical_bytes as d; "
        "from app.services.data_platform.fingerprint import canonical_bytes as p; "
        "value={'values': {1, '1', 2, '2'}}; print(d(value).hex(), p(value).hex())"
    )
    outputs = []
    for seed in ("1", "8675309"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [str(Path(os.sys.executable)), "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1]
    domain_bytes, platform_bytes = outputs[0].split()
    assert domain_bytes == platform_bytes
