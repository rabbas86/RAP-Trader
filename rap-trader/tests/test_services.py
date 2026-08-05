from datetime import UTC, datetime

import pytest

from app.domain.models.decision import AgentEvidence
from app.domain.models.order import OrderRequest
from app.domain.models.portfolio import PortfolioContext
from app.services.broker import PaperBroker
from app.services.decision_engine import WaitDecisionEngine
from app.services.execution import ExecutionService
from app.services.kronos import MockKronosService


def evidence(source: str) -> AgentEvidence:
    return AgentEvidence(
        source=source,
        ticker="AAPL",
        recommendation="neutral",
        confidence=0,
        reasoning_summary="Phase 1 placeholder",
        generated_at=datetime.now(UTC),
    )


def test_kronos_mock_is_clearly_identified() -> None:
    service = MockKronosService()
    prediction = service.predict("aapl")
    assert prediction.model_version == "mock-kronos-v0"
    assert service.LIVE_TRADING_SUITABLE is False
    assert "not suitable for live trading" in prediction.time_horizon


def test_decision_engine_defaults_to_wait() -> None:
    prediction = MockKronosService().predict("AAPL")
    result = WaitDecisionEngine().decide(
        prediction,
        evidence("technical"),
        evidence("fundamental"),
        evidence("news"),
        PortfolioContext(equity=10000, current_drawdown_percent=0, daily_loss_percent=0),
    )
    assert result.action == "WAIT"
    assert result.quantity == 0


def test_execution_cannot_override_risk_rejection() -> None:
    order = OrderRequest(ticker="AAPL", side="BUY", quantity=1, order_type="market", idempotency_key="risk-rejected")
    broker = PaperBroker()

    with pytest.raises(PermissionError, match="risk engine approval is required"):
        ExecutionService(broker).execute_approved(order, risk_approved=False)

    assert broker.orders == {}
