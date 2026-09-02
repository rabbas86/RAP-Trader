"""Phase 16F transaction-cost and corporate-action service."""

from __future__ import annotations

from typing import Any

from app.services.portfolio_accounting.costs import build_execution_cost_assessment
from app.services.portfolio_accounting.errors import (
    FutureCorporateActionError,
    InvalidCorporateActionError,
)
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    CorporateActionType,
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentApplicationResult,
    TransactionCostMethodology,
)


class Phase16FService:
    """Deterministic research-only service for transaction cost and corporate actions."""

    def __init__(self, *, ledger: Any) -> None:
        self.ledger = ledger

    def assess_execution_cost(
        self,
        *,
        paper_fill: Any,
        paper_order_id: str,
        methodology: TransactionCostMethodology,
        simulated_at: Any,
        replay_specification_id: str,
        replay_run_id: Any,
        producer_version: str = "phase16f-1.0",
    ) -> ExecutionCostAssessment:
        return build_execution_cost_assessment(
            paper_fill=paper_fill,
            paper_order_id=paper_order_id,
            methodology=methodology,
            simulated_at=simulated_at,
            replay_specification_id=replay_specification_id,
            replay_run_id=replay_run_id,
            producer_version=producer_version,
        )

    def apply_cost_assessment(
        self,
        prior_snapshot: Any,
        assessment: ExecutionCostAssessment,
    ) -> PortfolioAdjustmentApplicationResult:
        applied_snapshot = self.ledger.apply_execution_cost(prior_snapshot, assessment)
        return PortfolioAdjustmentApplicationResult(
            snapshot=applied_snapshot,
            ledger_entry=self.ledger._latest_execution_cost_ledger_entry(),
            applied=True,
        )

    def apply_corporate_action(
        self,
        prior_snapshot: Any,
        corporate_action: CorporateActionEvent,
        simulated_at: Any,
    ) -> PortfolioAdjustmentApplicationResult:
        if simulated_at < corporate_action.effective_at:
            raise FutureCorporateActionError(
                action_id=corporate_action.corporate_action_id,
                simulated_at=simulated_at.isoformat(),
            )
        applied_snapshot = self.ledger.apply_corporate_action(prior_snapshot, corporate_action)
        return PortfolioAdjustmentApplicationResult(
            snapshot=applied_snapshot,
            ledger_entry=self.ledger._latest_corporate_action_ledger_entry(),
            applied=True,
        )

    def create_dividend_entitlement(
        self,
        *,
        corporate_action: CorporateActionEvent,
        snapshot_id: str,
        symbol: Any,
        entitled_quantity: int,
        replay_specification_id: str,
        replay_run_id: Any,
        ex_date: Any,
        payment_at: Any,
        producer_version: str = "phase16f-1.0",
    ) -> DividendEntitlement:
        if corporate_action.action_type != CorporateActionType.CASH_DIVIDEND.value:
            raise InvalidCorporateActionError("dividend entitlement requires a cash_dividend corporate action")
        dividend_per_share = corporate_action.dividend_per_share
        if dividend_per_share is None:
            raise InvalidCorporateActionError("cash_dividend corporate action must define dividend_per_share")
        return DividendEntitlement.create(
            corporate_action_id=corporate_action.corporate_action_id,
            snapshot_id=snapshot_id,
            symbol=symbol,
            entitled_quantity=entitled_quantity,
            dividend_per_share=dividend_per_share,
            gross_cash_amount=round(entitled_quantity * dividend_per_share, 10),
            currency=corporate_action.currency,
            ex_date=ex_date,
            payment_at=payment_at,
            replay_specification_id=replay_specification_id,
            replay_run_id=replay_run_id,
            producer_version=producer_version,
        )

    def apply_dividend_payment(
        self,
        prior_snapshot: Any,
        entitlement: DividendEntitlement,
        simulated_at: Any,
    ) -> PortfolioAdjustmentApplicationResult:
        if simulated_at < entitlement.payment_at:
            raise FutureCorporateActionError(
                action_id=entitlement.entitlement_id,
                simulated_at=simulated_at.isoformat(),
            )
        applied_snapshot = self.ledger.apply_dividend_payment(prior_snapshot, entitlement)
        return PortfolioAdjustmentApplicationResult(
            snapshot=applied_snapshot,
            ledger_entry=self.ledger._latest_dividend_payment_ledger_entry(),
            applied=True,
        )


__all__ = [
    "Phase16FService",
    "PortfolioAdjustmentApplicationResult",
]
