"""Deterministic portfolio manager configuration."""

from pydantic import BaseModel, ConfigDict, Field


class PortfolioManagerConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    algorithm_version: str = "phase-10-v1"
    minimum_analyst_coverage: int = Field(default=1, ge=1)
    stale_opinion_factor: float = Field(default=0.50, ge=0, le=1, allow_inf_nan=False)
    weak_conviction_threshold: float = Field(default=0.10, ge=0, le=1, allow_inf_nan=False)
    minimum_correlation_samples: int = Field(default=3, ge=2)
    correlation_threshold: float = Field(default=0.70, ge=-1, le=1, allow_inf_nan=False)
    weight_tolerance: float = Field(default=1e-8, gt=0, allow_inf_nan=False)
