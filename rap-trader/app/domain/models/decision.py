from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AgentEvidence(BaseModel):
    source: str
    ticker: str
    recommendation: str
    confidence: float = Field(ge=0, le=1)
    reasoning_summary: str
    generated_at: datetime


class TradeDecision(BaseModel):
    decision_id: UUID
    ticker: str
    action: Literal["BUY", "SELL", "WAIT"]
    confidence: float = Field(ge=0, le=1)
    quantity: int = Field(ge=0)
    order_type: Literal["market", "limit"]
    limit_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    rationale: str
    evidence: list[AgentEvidence]
    created_at: datetime
