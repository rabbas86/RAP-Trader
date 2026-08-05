from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class OrderRequest(BaseModel):
    ticker: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    order_type: Literal["market", "limit"]
    limit_price: float | None = Field(default=None, gt=0)
    idempotency_key: str = Field(min_length=1)

    @model_validator(mode="after")
    def limit_orders_require_price(self) -> "OrderRequest":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        return self


class OrderResult(BaseModel):
    order_id: str
    status: Literal["accepted", "rejected"]
    broker: str
    paper_trade: bool
    message: str
    created_at: datetime
