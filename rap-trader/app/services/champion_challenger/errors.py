"""Champion/challenger evaluation errors."""

from __future__ import annotations


class ChampionChallengerError(Exception):
    """Base champion/challenger evaluation error."""


class ChampionChallengerValidationError(ChampionChallengerError):
    """Raised when evaluation inputs violate comparison contracts."""


class ChampionChallengerNotFoundError(ChampionChallengerError):
    """Raised when a requested evaluation artifact does not exist."""

    def __init__(self, evaluation_id: str) -> None:
        super().__init__(f"Champion/challenger evaluation not found: {evaluation_id}")
        self.evaluation_id = evaluation_id


class ChampionChallengerQueryError(ChampionChallengerError):
    """Raised when a champion/challenger query cannot be satisfied."""


class ChampionChallengerCorruptionError(ChampionChallengerError):
    """Raised when a persisted champion/challenger artifact cannot be trusted."""


__all__ = [
    "ChampionChallengerCorruptionError",
    "ChampionChallengerError",
    "ChampionChallengerNotFoundError",
    "ChampionChallengerQueryError",
    "ChampionChallengerValidationError",
]
