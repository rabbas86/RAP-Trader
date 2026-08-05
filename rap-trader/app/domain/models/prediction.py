from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class KronosPrediction(BaseModel):
    ticker: str = Field(min_length=1)
    direction: Literal["UP", "DOWN", "FLAT"]
    confidence: float = Field(ge=0, le=1)
    expected_return: float
    time_horizon: str
    generated_at: datetime
    model_version: str
