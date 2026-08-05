from app.domain.models.order import OrderRequest, OrderResult
from app.services.broker.base import Broker


class ExecutionService:
    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    def execute_approved(self, order: OrderRequest, *, risk_approved: bool) -> OrderResult:
        if not risk_approved:
            raise PermissionError("risk engine approval is required and cannot be overridden")
        return self.broker.submit_order(order)
