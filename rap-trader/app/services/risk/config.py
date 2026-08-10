"""Configuration for deterministic portfolio risk review."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.models.risk import RiskConstraintSet


@dataclass(frozen=True)
class RiskOfficerConfig:
    algorithm_version: str = "risk-officer-v1"
    constraints: RiskConstraintSet = field(default_factory=RiskConstraintSet)
    correlation_cluster_threshold: float = 0.70
    illiquid_dollar_volume: float = 1_000_000.0
