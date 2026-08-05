import pytest

from app.domain.models.order import OrderRequest
from app.services.broker import DuplicateOrderError, PaperBroker


def order() -> OrderRequest:
    return OrderRequest(ticker="AAPL", side="BUY", quantity=1, order_type="market", idempotency_key="test-1")


def test_valid_paper_order_is_accepted() -> None:
    result = PaperBroker().submit_order(order())
    assert result.status == "accepted"
    assert result.paper_trade is True
    assert result.order_id.startswith("paper-")


def test_duplicate_idempotency_key_is_rejected() -> None:
    broker = PaperBroker()
    broker.submit_order(order())
    with pytest.raises(DuplicateOrderError):
        broker.submit_order(order())
