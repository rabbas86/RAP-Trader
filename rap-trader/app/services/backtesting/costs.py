"""Deterministic transaction-cost and slippage interfaces and implementations.

This module defines abstract interfaces for transaction-cost modeling and
slippage modeling, along with two concrete implementations:

* ``ZeroCostModel`` / ``ZeroSlippageModel`` — no costs (baseline).
* ``FixedBpsCostModel`` / ``FixedBpsSlippageModel`` — fixed basis-points per
  trade, the standard research-cost model.

All models are deterministic: identical inputs produce identical outputs.
The cost result is computed alongside the research signal simulation and
never feeds into production decision-making.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """Configuration for transaction-cost and slippage models."""

    transaction_cost_bps: float = 0.0
    slippage_bps: float = 0.0
    short_selling_allowed: bool = False
    leverage: float = 1.0


class TransactionCostModel(ABC):
    """Abstract interface for transaction-cost modeling."""

    @abstractmethod
    def compute(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        """Return the total transaction cost (in currency units) for a round-trip.

        Parameters
        ----------
        entry_price:
            Price at which the position was entered.
        exit_price:
            Price at which the position was exited.
        position_size:
            Number of units (shares) traded.  Negative = short.
        config:
            Cost configuration including basis-points and short-selling flag.
        """

    @abstractmethod
    def name(self) -> str:
        """Return a stable identifier for this cost model."""


class SlippageModel(ABC):
    """Abstract interface for slippage modeling."""

    @abstractmethod
    def compute(
        self,
        trade_value: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        """Return the total slippage cost (in currency units) for a trade.

        Parameters
        ----------
        trade_value:
            ``price * quantity`` for the trade (entry or exit).
        position_size:
            Number of units traded.  Negative = short.
        config:
            Cost configuration including basis-points.
        """

    @abstractmethod
    def name(self) -> str:
        """Return a stable identifier for this slippage model."""


class ZeroCostModel(TransactionCostModel):
    """No transaction costs (research baseline)."""

    def compute(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        return 0.0

    def name(self) -> str:
        return "zero-cost"


class ZeroSlippageModel(SlippageModel):
    """No slippage (research baseline)."""

    def compute(
        self,
        trade_value: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        return 0.0

    def name(self) -> str:
        return "zero-slippage"


class FixedBpsCostModel(TransactionCostModel):
    """Fixed basis-points transaction cost applied to both legs (entry + exit).

    Total cost = ``bps / 10_000 * (entry_value + exit_value)``

    where ``entry_value = entry_price * abs(position_size) * leverage`` and
    ``exit_value = exit_price * abs(position_size) * leverage``.

    Short-selling is blocked (position_size clamped to 0) unless
    ``config.short_selling_allowed`` is ``True``.
    """

    def __init__(self, bps: float = 0.0) -> None:
        if bps < 0:
            raise ValueError("bps must be non-negative")
        self.bps = bps

    def compute(
        self,
        entry_price: float,
        exit_price: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        # Short selling disabled by default
        if position_size < 0 and not config.short_selling_allowed:
            return 0.0
        if position_size == 0:
            return 0.0

        leverage = config.leverage
        entry_value = entry_price * abs(position_size) * leverage
        exit_value = exit_price * abs(position_size) * leverage
        cost = (self.bps / 10_000.0) * (entry_value + exit_value)
        return round(cost, 10)

    def name(self) -> str:
        return f"fixed-bps-cost({self.bps})"


class FixedBpsSlippageModel(SlippageModel):
    """Fixed basis-points slippage applied to each trade's notional value.

    Slippage = ``bps / 10_000 * trade_value * leverage``

    where ``trade_value = price * abs(position_size)``.
    """

    def __init__(self, bps: float = 0.0) -> None:
        if bps < 0:
            raise ValueError("bps must be non-negative")
        self.bps = bps

    def compute(
        self,
        trade_value: float,
        position_size: float,
        config: CostConfig,
    ) -> float:
        if position_size == 0:
            return 0.0
        leverage = config.leverage
        slippage = (self.bps / 10_000.0) * trade_value * leverage
        return round(abs(slippage), 10)

    def name(self) -> str:
        return f"fixed-bps-slippage({self.bps})"
