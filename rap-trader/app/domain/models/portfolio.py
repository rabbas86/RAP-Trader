from pydantic import BaseModel, Field


class PortfolioContext(BaseModel):
    equity: float = Field(gt=0)
    current_drawdown_percent: float = Field(ge=0)
    daily_loss_percent: float = Field(ge=0)
