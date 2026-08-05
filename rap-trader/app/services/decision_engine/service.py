from abc import ABC, abstractmethod
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.models.decision import AgentEvidence, TradeDecision
from app.domain.models.portfolio import PortfolioContext
from app.domain.models.prediction import KronosPrediction


class DecisionEngine(ABC):
    @abstractmethod
    def decide(
        self,
        prediction: KronosPrediction,
        technical: AgentEvidence,
        fundamental: AgentEvidence,
        news: AgentEvidence,
        portfolio: PortfolioContext,
    ) -> TradeDecision:
        """Fuse evidence into a decision; risk approval remains separate."""


class WaitDecisionEngine(DecisionEngine):
    def decide(
        self,
        prediction: KronosPrediction,
        technical: AgentEvidence,
        fundamental: AgentEvidence,
        news: AgentEvidence,
        portfolio: PortfolioContext,
    ) -> TradeDecision:
        del portfolio
        return TradeDecision(
            decision_id=uuid4(),
            ticker=prediction.ticker,
            action="WAIT",
            confidence=0,
            quantity=0,
            order_type="market",
            rationale="Phase 1 deterministic safety default: WAIT",
            evidence=[technical, fundamental, news],
            created_at=datetime.now(UTC),
        )
