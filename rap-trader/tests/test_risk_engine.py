from app.config import Settings
from app.services.risk_engine import RiskEngine


def engine() -> RiskEngine:
    return RiskEngine(Settings(_env_file=None))  # type: ignore[call-arg]


def test_live_execution_is_rejected() -> None:
    result = engine().assess(
        action="BUY", quantity=1, position_percent=1, estimated_trade_loss_percent=0.1, portfolio_drawdown_percent=0, live_order=True
    )
    assert not result.approved
    assert "live trading is disabled" in result.rejection_reasons


def test_excessive_position_is_rejected() -> None:
    result = engine().assess(action="BUY", quantity=10, position_percent=6, estimated_trade_loss_percent=1, portfolio_drawdown_percent=0)
    assert not result.approved
    assert "maximum position percent exceeded" in result.rejection_reasons


def test_invalid_quantity_action_loss_and_drawdown_are_rejected() -> None:
    result = engine().assess(action="WAIT", quantity=0, position_percent=1, estimated_trade_loss_percent=3, portfolio_drawdown_percent=11)
    assert len(result.rejection_reasons) == 4
