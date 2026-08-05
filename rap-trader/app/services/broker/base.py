from abc import ABC, abstractmethod

from app.domain.models.order import OrderRequest, OrderResult


class Broker(ABC):
    @abstractmethod
    def submit_order(self, order: OrderRequest) -> OrderResult:
        """Submit an order to this broker implementation."""
