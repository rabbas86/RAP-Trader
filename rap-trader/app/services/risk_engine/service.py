from app.config import Settings
from app.domain.models.risk import RiskAssessment


class RiskEngine:
    """Deterministic, non-overridable pre-execution risk gate."""

    SUPPORTED_ACTIONS = {"BUY", "SELL"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def assess(self, *, action: str, quantity: int, position_percent: float, estimated_trade_loss_percent: float, portfolio_drawdown_percent: float, live_order: bool = False) -> RiskAssessment:
        reasons: list[str] = []
        if quantity <= 0:
            reasons.append("quantity must be positive")
        if action not in self.SUPPORTED_ACTIONS:
            reasons.append("unsupported order action")
        if position_percent > self.settings.max_position_percent:
            reasons.append("maximum position percent exceeded")
        if estimated_trade_loss_percent > self.settings.max_daily_loss_percent:
            reasons.append("maximum daily loss percent exceeded")
        if portfolio_drawdown_percent > self.settings.max_portfolio_drawdown_percent:
            reasons.append("maximum portfolio drawdown percent exceeded")
        if live_order and not self.settings.live_trading_enabled:
            reasons.append("live trading is disabled")
        maximum = quantity if position_percent <= 0 else max(0, int(quantity * self.settings.max_position_percent / position_percent))
        return RiskAssessment(approved=not reasons, rejection_reasons=reasons, maximum_allowed_quantity=maximum, estimated_position_percent=max(0, position_percent), estimated_daily_loss_percent=max(0, estimated_trade_loss_percent))
