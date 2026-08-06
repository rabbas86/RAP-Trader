from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models.market_data import Timeframe


class KronosPrediction(BaseModel):
    model_config = ConfigDict(strict=True)

    ticker: str = Field(min_length=1)
    direction: Literal["UP", "DOWN", "FLAT"]
    confidence: float = Field(ge=0, le=1)
    expected_return: float
    time_horizon: str
    generated_at: datetime
    model_version: str
    timeframe: Timeframe | None = None
    source_provider: str | None = None
    data_start: datetime | None = None
    data_end: datetime | None = None
