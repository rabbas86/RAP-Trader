"""Stable Chairman provenance and policy fingerprints."""

import hashlib
import json
import subprocess
from typing import Any

from pydantic import BaseModel


class ChairmanProvenanceService:
    @staticmethod
    def fingerprint(value: Any) -> str:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    @staticmethod
    def git_commit() -> str | None:
        try:
            return (
                subprocess.run(
                    ["git", "rev-parse", "HEAD"], capture_output=True, check=True, text=True, timeout=2, shell=False
                ).stdout.strip()
                or None
            )
        except (OSError, subprocess.SubprocessError):
            return None
