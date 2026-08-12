"""Architecture-neutral canonical serialization and SHA-256 fingerprints."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _canonical_set_sort_key(value: Any) -> bytes:
    """Order set members by both their Python type and canonical representation."""
    value_type = type(value)
    type_name = f"{value_type.__module__}.{value_type.__qualname__}"
    return type_name.encode("utf-8") + b"\x00" + canonical_bytes(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (Path, UUID)):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=_canonical_set_sort_key)
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Return compact, key-sorted JSON, rejecting non-finite numbers."""
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def canonical_bytes(value: Any) -> bytes:
    """Return the UTF-8 encoding of canonical JSON."""
    return canonical_json(value).encode("utf-8")


def sha256_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


__all__ = ["canonical_bytes", "canonical_json", "sha256_fingerprint"]
