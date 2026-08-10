"""Stable portfolio proposal provenance."""

import hashlib
import json
import subprocess
from typing import Any

from pydantic import BaseModel


def _fingerprint(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class PortfolioProvenanceService:
    fingerprint = staticmethod(_fingerprint)

    @staticmethod
    def git_commit() -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True, timeout=2, shell=False
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
        return result or None
