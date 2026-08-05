import logging
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.models.order import OrderRequest, OrderResult
from app.services.broker.base import Broker

logger = logging.getLogger(__name__)


class DuplicateOrderError(ValueError):
    pass


class PaperBroker(Broker):
    """Process-local simulator; it has no real-broker connectivity."""

    def __init__(self) -> None:
        self.orders: dict[str, OrderResult] = {}

    def submit_order(self, order: OrderRequest) -> OrderResult:
        if order.idempotency_key in self.orders:
            raise DuplicateOrderError(f"duplicate idempotency key: {order.idempotency_key}")
        result = OrderResult(order_id=f"paper-{uuid4()}", status="accepted", broker="in-memory-paper", paper_trade=True, message="Simulated order accepted; no real trade was placed", created_at=datetime.now(UTC))
        self.orders[order.idempotency_key] = result
        logger.info("paper order accepted", extra={"order_id": result.order_id, "service": "paper_broker", "event": "submit_order", "result": result.status})
        return result
