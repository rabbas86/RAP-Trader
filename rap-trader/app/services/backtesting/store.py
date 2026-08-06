"""Backtest result persistence.

Implements:

* ``BacktestResultStore`` — abstract interface.
* ``InMemoryBacktestResultStore`` — process-local, TTL, LRU.
* ``JSONFileBacktestResultStore`` — atomic JSON file writes to a
  project-local allowed directory.

JSON file behavior:

* **Explicit output only** — nothing is written unless ``save`` is called.
* **Atomic writes** — results are written to a temp file first, then
  ``os.replace``d into place.
* **Schema version** — every file includes ``schema_version``.
* **Safe filenames** — backtest IDs are sanitized (alphanumeric + ``-``)
  to prevent path traversal.
* **Project-local allowed directory** — writes are confined to a
  configurable directory (default ``backtest_results/`` under the project
  root).  Paths containing ``..`` or absolute paths outside the allowed
  root are rejected.
* **No pickle** — only JSON serialization.
* **No cloud** — no remote storage.
* **No database** — flat files only.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from collections import OrderedDict
from threading import RLock
from time import monotonic

from app.domain.models.backtesting import BACKTEST_SCHEMA_VERSION, BacktestRunResult

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,127}$")


class BacktestResultStore(ABC):
    """Abstract interface for backtest result persistence."""

    @abstractmethod
    def save(self, result: BacktestRunResult) -> str:
        """Persist ``result`` and return the backtest_id."""

    @abstractmethod
    def load(self, backtest_id: str) -> BacktestRunResult | None:
        """Load a result by ID, or ``None`` if not found."""

    @abstractmethod
    def list(self) -> list[str]:
        """Return all known backtest IDs."""

    @abstractmethod
    def delete(self, backtest_id: str) -> bool:
        """Delete a result by ID.  Returns ``True`` if it existed."""


class InMemoryBacktestResultStore(BacktestResultStore):
    """Thread-safe in-memory store with TTL and LRU eviction.

    Parameters
    ----------
    ttl_seconds:
        Optional time-to-live.  After this many seconds, entries expire
        and are lazily evicted on access.  ``None`` = never expires.
    max_size:
        Maximum number of entries.  When exceeded, the least-recently-used
        entry is evicted.
    """

    def __init__(
        self,
        ttl_seconds: float | None = None,
        max_size: int = 1000,
    ) -> None:
        if ttl_seconds is not None and ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive or None")
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._entries: OrderedDict[str, tuple[float | None, BacktestRunResult]] = OrderedDict()
        self._lock = RLock()

    def save(self, result: BacktestRunResult) -> str:
        with self._lock:
            if len(self._entries) >= self.max_size and result.backtest_id not in self._entries:
                self._entries.popitem(last=False)  # evict LRU
            expires_at = monotonic() + self.ttl_seconds if self.ttl_seconds is not None else None
            self._entries[result.backtest_id] = (expires_at, result)
            self._entries.move_to_end(result.backtest_id)
        return result.backtest_id

    def load(self, backtest_id: str) -> BacktestRunResult | None:
        with self._lock:
            entry = self._entries.get(backtest_id)
            if entry is None:
                return None
            expires_at, result = entry
            if expires_at is not None and expires_at <= monotonic():
                del self._entries[backtest_id]
                return None
            self._entries.move_to_end(backtest_id)
            return result

    def list(self) -> list[str]:
        with self._lock:
            # Clean up expired entries
            if self.ttl_seconds is not None:
                now = monotonic()
                expired = [k for k, (exp, _) in self._entries.items() if exp is not None and exp <= now]
                for k in expired:
                    del self._entries[k]
            return list(self._entries.keys())

    def delete(self, backtest_id: str) -> bool:
        with self._lock:
            if backtest_id in self._entries:
                del self._entries[backtest_id]
                return True
            return False


class JSONFileBacktestResultStore(BacktestResultStore):
    """Atomic JSON file store writing to a project-local allowed directory.

    Parameters
    ----------
    root_dir:
        Directory where backtest result files are stored.  Must be a
        project-local path.  The directory is created if it does not exist.
    """

    SCHEMA_VERSION = BACKTEST_SCHEMA_VERSION

    def __init__(self, root_dir: str) -> None:
        self.root_dir = os.path.abspath(root_dir)
        os.makedirs(self.root_dir, exist_ok=True)

    def _validate_backtest_id(self, backtest_id: str) -> str:
        """Validate and sanitize the backtest ID for safe filenames."""
        if not backtest_id or not _SAFE_ID_RE.match(backtest_id):
            raise ValueError(f"Invalid backtest_id: {backtest_id!r}")
        return backtest_id

    def _filepath(self, backtest_id: str) -> str:
        """Return the file path for a backtest ID, with path-traversal protection."""
        safe_id = self._validate_backtest_id(backtest_id)
        filename = f"{safe_id}.json"
        filepath = os.path.abspath(os.path.join(self.root_dir, filename))
        # Ensure the resolved path is still inside the root directory
        if not filepath.startswith(self.root_dir + os.sep) and filepath != self.root_dir:
            raise ValueError(f"Path traversal detected for backtest_id: {backtest_id!r}")
        return filepath

    @staticmethod
    def _serialize(result: BacktestRunResult) -> str:
        """Serialize a result to JSON, ensuring schema version and safety."""
        data = result.model_dump(mode="json")
        data["schema_version"] = BACKTEST_SCHEMA_VERSION
        # Ensure no pickle — we use JSON serialization only
        return json.dumps(data, sort_keys=True, indent=2, default=str)

    @staticmethod
    def _deserialize(data: dict[str, object]) -> BacktestRunResult:
        """Deserialize a result from a JSON dict, validating schema version."""
        schema_version = data.get("schema_version")
        if schema_version != BACKTEST_SCHEMA_VERSION:
            raise ValueError(f"Schema version mismatch: expected {BACKTEST_SCHEMA_VERSION}, got {schema_version}")
        # Remove schema_version before model validation (not a field)
        data.pop("schema_version", None)
        return BacktestRunResult.model_validate(data)

    def save(self, result: BacktestRunResult) -> str:
        filepath = self._filepath(result.backtest_id)
        serialized = self._serialize(result)

        # Atomic write: write to temp file, then os.replace
        dir_name = os.path.dirname(filepath)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=f".{result.backtest_id}_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(serialized)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, filepath)
        except Exception:
            # Clean up temp file on any error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return result.backtest_id

    def load(self, backtest_id: str) -> BacktestRunResult | None:
        try:
            filepath = self._filepath(backtest_id)
        except ValueError:
            return None

        if not os.path.isfile(filepath):
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return self._deserialize(data)
        except (json.JSONDecodeError, ValueError, OSError):
            return None

    def list(self) -> list[str]:
        if not os.path.isdir(self.root_dir):
            return []
        ids: list[str] = []
        for filename in sorted(os.listdir(self.root_dir)):
            if filename.endswith(".json") and not filename.startswith("."):
                backtest_id = filename[:-5]  # strip .json
                if _SAFE_ID_RE.match(backtest_id):
                    ids.append(backtest_id)
        return ids

    def delete(self, backtest_id: str) -> bool:
        try:
            filepath = self._filepath(backtest_id)
        except ValueError:
            return False
        if not os.path.isfile(filepath):
            return False
        os.unlink(filepath)
        return True
