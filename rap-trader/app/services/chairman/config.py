"""Conservative deterministic Chairman governance policy."""

from datetime import timedelta

from pydantic import BaseModel, ConfigDict, Field


class ChairmanConfig(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    service_version: str = "phase-13-v1"
    minimum_governance_score: float = Field(default=0.80, ge=0, le=1, allow_inf_nan=False)
    allow_missing_committee: bool = False
    require_complete_provenance: bool = True
    require_complete_trace: bool = True
    require_dissent_acknowledgement: bool = True
    require_all_specialists: bool = True
    maximum_input_age: timedelta = timedelta(days=7)
    maximum_as_of_delta_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
