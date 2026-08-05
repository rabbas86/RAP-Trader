from pydantic import BaseModel, Field


class RiskAssessment(BaseModel):
    approved: bool
    rejection_reasons: list[str]
    maximum_allowed_quantity: int = Field(ge=0)
    estimated_position_percent: float = Field(ge=0)
    estimated_daily_loss_percent: float = Field(ge=0)
