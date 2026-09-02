"""Deterministic transaction-cost assessment for Phase 16F."""

from __future__ import annotations

from typing import Any

from app.services.paper_execution.contracts import PaperOrderSide
from app.services.portfolio_accounting.errors import InvalidCostInputError
from app.services.portfolio_accounting.phase16f_models import ExecutionCostAssessment, TransactionCostMethodology


def _non_negative(value: float, name: str) -> float:
    if value < 0:
        raise InvalidCostInputError(f"{name} must be non-negative")
    return value


def compute_side_cost_bps(*, methodology: TransactionCostMethodology) -> float:
    """Return the deterministic per-side cost in basis points.

    Full spread is interpreted as the total bid/ask spread.
    Per-side spread cost is therefore half of ``spread_bps``.
    """
    return round(methodology.spread_bps / 2 + methodology.slippage_bps, 10)


def compute_effective_price(
    *,
    reference_price: float,
    side: PaperOrderSide,
    methodology: TransactionCostMethodology,
) -> float:
    """Apply the deterministic Phase 16F slippage policy.

    BUY effective price = reference * (1 + side_cost_bps / 10_000)
    SELL effective price = reference * (1 - side_cost_bps / 10_000)
    """
    side_cost_bps = compute_side_cost_bps(methodology=methodology)
    if side == PaperOrderSide.BUY:
        return round(reference_price * (1 + side_cost_bps / 10_000), 10)
    return round(reference_price * (1 - side_cost_bps / 10_000), 10)


def compute_commission(
    *,
    notional: float,
    quantity: int,
    methodology: TransactionCostMethodology,
) -> float:
    """Deterministic commission from explicit methodology components."""
    candidate = round(
        methodology.fixed_commission + methodology.per_unit_commission * quantity + notional * methodology.commission_bps / 10_000,
        10,
    )
    if methodology.minimum_commission is not None:
        candidate = max(methodology.minimum_commission, candidate)
    return round(candidate, 10)


def compute_total_transaction_cost(
    *,
    reference_notional: float,
    effective_notional: float,
    commission: float,
) -> float:
    """Return the non-negative economic transaction cost.

    BUY cost = effective_notional - reference_notional + commission
    SELL cost = reference_notional - effective_notional + commission
    Unified: abs(effective_notional - reference_notional) + commission
    """
    return round(abs(effective_notional - reference_notional) + commission, 10)


def build_execution_cost_assessment(
    *,
    paper_fill: Any,
    paper_order_id: str,
    methodology: Any,
    simulated_at: Any,
    replay_specification_id: str,
    replay_run_id: Any,
    producer_version: str = "phase16f-1.0",
) -> ExecutionCostAssessment:
    """Build an immutable deterministic execution-cost assessment.

    The original ``PaperFill`` is never mutated.
    """
    _non_negative(methodology.commission_bps, "commission_bps")
    _non_negative(methodology.fixed_commission, "fixed_commission")
    _non_negative(methodology.per_unit_commission, "per_unit_commission")
    if methodology.minimum_commission is not None:
        _non_negative(methodology.minimum_commission, "minimum_commission")
    _non_negative(methodology.spread_bps, "spread_bps")
    _non_negative(methodology.slippage_bps, "slippage_bps")

    reference_price = paper_fill.execution_price
    quantity = paper_fill.quantity
    side = paper_fill.side
    reference_notional = round(reference_price * quantity, 10)
    effective_price = compute_effective_price(
        reference_price=reference_price,
        side=side,
        methodology=methodology,
    )
    effective_notional = round(effective_price * quantity, 10)
    spread_cost = round(reference_notional * methodology.spread_bps / 20_000, 10)
    slippage_cost = round(reference_notional * methodology.slippage_bps / 10_000, 10)
    commission = compute_commission(
        notional=reference_notional,
        quantity=quantity,
        methodology=methodology,
    )
    total_transaction_cost = compute_total_transaction_cost(
        reference_notional=reference_notional,
        effective_notional=effective_notional,
        commission=commission,
    )

    from app.services.portfolio_accounting.phase16f_models import ExecutionCostAssessment

    return ExecutionCostAssessment.create(
        paper_fill_id=paper_fill.paper_fill_id,
        paper_order_id=paper_order_id,
        replay_specification_id=replay_specification_id,
        replay_run_id=replay_run_id,
        symbol=paper_fill.symbol,
        side=side,
        quantity=quantity,
        reference_execution_price=reference_price,
        effective_execution_price=effective_price,
        reference_notional=reference_notional,
        effective_notional=effective_notional,
        commission=commission,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        total_transaction_cost=total_transaction_cost,
        methodology_id=methodology.methodology_id,
        simulated_at=simulated_at,
        producer_version=producer_version,
    )


__all__ = [
    "build_execution_cost_assessment",
    "compute_commission",
    "compute_effective_price",
    "compute_side_cost_bps",
    "compute_total_transaction_cost",
]
