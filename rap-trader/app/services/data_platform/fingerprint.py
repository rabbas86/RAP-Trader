"""Canonical SHA-256 fingerprints for research data."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _json_default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=False)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"unsupported fingerprint value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class DataFingerprintService:
    canonical_json = staticmethod(canonical_json)
    fingerprint = staticmethod(sha256_fingerprint)


FingerprintService = DataFingerprintService

__all__ = ["DataFingerprintService", "FingerprintService", "canonical_json", "sha256_fingerprint"]
