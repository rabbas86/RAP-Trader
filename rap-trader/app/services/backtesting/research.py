"""Research-only signal simulation.

This module implements a *research-only* signal simulator that generates
LONG / SHORT / FLAT trading signals from forecast data and computes
performance attribution (PnL, drawdown, turnover) both before and after
transaction costs and slippage.

**Critical safety constraints:**

* Signals are derived from forecast data only — they are NOT based on
  any production ``TradeDecision`` or ``OrderRequest``.
* The simulator does NOT import or call ``Broker``, ``PaperBroker``,
  ``ExecutionService``, ``RiskEngine``, ``PortfolioManager``, ``Chairman``,
  or any Investment Committee agent.
* Every result carries ``research_only=True`` and
  ``suitable_for_live_trading=False``.
* Short-selling is disabled by default; leverage is 1.0 by default.
* Position sizing is flat (1 unit per window) unless research simulation
  specifies otherwise — this is a research abstraction, not a production
  position allocator.

No broker, execution, order, risk, or portfolio components are used.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.models.backtesting import CostResult, ResearchSignal, ResearchSignalRow
from app.services.backtesting.costs import (
    CostConfig,
    FixedBpsCostModel,
    FixedBpsSlippageModel,
    SlippageModel,
    TransactionCostModel,
    ZeroCostModel,
    ZeroSlippageModel,
)


@dataclass
class SignalSimulationConfig:
    """Configuration for the research signal simulator.

    Attributes
    ----------
    short_selling:
        If ``True``, SHORT signals generate negative positions.  If
        ``False`` (default), SHORT signals are converted to FLAT.
    leverage:
        Position-leverage multiplier (default 1.0, max configurable).
    transaction_cost_bps:
        Basis-points cost per round-trip trade (default 0.0 = none).
    slippage_bps:
        Basis-points slippage per trade (default 0.0 = none).
    position_size:
        Number of units per position (default 1 for research).
    """

    short_selling: bool = False
    leverage: float = 1.0
    transaction_cost_bps: float = 0.0
    slippage_bps: float = 0.0
    position_size: float = 1.0


@dataclass
class SignalSimulationResult:
    """Result of a research signal simulation for a single window."""

    signals: list[ResearchSignalRow]
    cost_result: CostResult
    positions: list[float]
    returns_before_cost: list[float]
    returns_after_cost: list[float]
    research_only: bool = True
    suitable_for_live_trading: bool = False


class ResearchSignalSimulator:
    """Simulate research-only LONG/SHORT/FLAT signals from forecast data.

    The simulator uses a simple directional rule:

    * If the forecast's final close is above the context's last close by
      more than a threshold (default 1%) -> LONG.
    * If below by more than the threshold -> SHORT (unless short-selling
      is disabled -> FLAT).
    * Otherwise -> FLAT.

    PnL is computed as the percentage change in price over the horizon,
    multiplied by the position direction.  Transaction costs and slippage
    are deducted from gross PnL to produce net PnL.
    """

    DEFAULT_THRESHOLD: float = 0.01  # 1% minimum move to generate a signal

    def __init__(self, config: SignalSimulationConfig | None = None) -> None:
        self.config = config or SignalSimulationConfig()

    def simulate(
        self,
        forecast_closes: list[float],
        actual_closes: list[float],
        window: Any,
        context_last_close: float | None = None,
    ) -> dict[str, Any]:
        """Run signal simulation for a single evaluation window.

        Parameters
        ----------
        forecast_closes:
            Forecast close prices for the target horizon.
        actual_closes:
            Actual close prices for the target horizon.
        window:
            The ``EvaluationWindow`` (used for timestamp alignment).
        context_last_close:
            The last close price of the context period (used to determine
            signal direction).  If ``None``, the first forecast close is
            used as the reference.

        Returns
        -------
        dict[str, Any]
            Contains ``signals``, ``cost_result``, ``positions``,
            ``returns_before_cost``, ``returns_after_cost``,
            ``research_only``, and ``suitable_for_live_trading``.
        """
        n = len(actual_closes)
        if n == 0:
            return {
                "signals": [],
                "cost_result": CostResult(
                    gross_pnl=0.0,
                    total_costs=0.0,
                    net_pnl=0.0,
                    turnover=0.0,
                    commission_cost=0.0,
                    slippage_cost=0.0,
                    max_drawdown=0.0,
                    short_selling_allowed=False,
                    leverage=1.0,
                ),
                "positions": [],
                "returns_before_cost": [],
                "returns_after_cost": [],
                "research_only": True,
                "suitable_for_live_trading": False,
            }

        cfg = self.config
        threshold = self.DEFAULT_THRESHOLD

        # --- Determine reference price ---
        if context_last_close is not None and context_last_close > 0:
            reference = context_last_close
        elif forecast_closes and forecast_closes[0] > 0:
            reference = forecast_closes[0]
        else:
            reference = 100.0

        # --- Determine signal direction from forecast ---
        forecast_final = forecast_closes[-1] if forecast_closes else reference
        forecast_move = (forecast_final - reference) / abs(reference) if reference != 0 else 0.0

        if forecast_move > threshold:
            signal: ResearchSignal = ResearchSignal.LONG
        elif forecast_move < -threshold:
            if not cfg.short_selling:
                signal = ResearchSignal.FLAT
            else:
                signal = ResearchSignal.SHORT
        else:
            signal = ResearchSignal.FLAT

        # --- Position size ---
        if signal == ResearchSignal.SHORT:
            position = -abs(cfg.position_size)
        elif signal == ResearchSignal.FLAT:
            position = 0.0
        else:
            position = abs(cfg.position_size)

        # --- Timestamp alignment ---
        if hasattr(window, "target_start") and hasattr(window, "timeframe"):
            from app.services.backtesting.engine import _timeframe_step

            step = _timeframe_step(window.timeframe)
            base_ts = window.target_start
        else:
            step = None
            base_ts = None

        # --- Cost models ---
        cost_config = CostConfig(
            transaction_cost_bps=cfg.transaction_cost_bps,
            slippage_bps=cfg.slippage_bps,
            short_selling_allowed=cfg.short_selling,
            leverage=cfg.leverage,
        )
        cost_model: TransactionCostModel = FixedBpsCostModel(cfg.transaction_cost_bps) if cfg.transaction_cost_bps > 0 else ZeroCostModel()
        slippage_model: SlippageModel = FixedBpsSlippageModel(cfg.slippage_bps) if cfg.slippage_bps > 0 else ZeroSlippageModel()

        # --- Compute per-period returns ---
        returns_before: list[float] = []
        positions: list[float] = []
        signals: list[ResearchSignalRow] = []

        for i in range(n):
            if base_ts is not None and step is not None:
                ts = base_ts + step * i
            else:
                ts = datetime.now(UTC)

            positions.append(position)
            price = max(actual_closes[i], 0.01)

            signals.append(
                ResearchSignalRow(
                    timestamp=ts,
                    signal=signal,
                    position_size=abs(position) if position != 0 else 0.0,
                    price=price,
                )
            )

            if position == 0 or i == 0:
                returns_before.append(0.0)
            else:
                if actual_closes[i - 1] > 0:
                    period_return = position * (actual_closes[i] - actual_closes[i - 1]) / abs(actual_closes[i - 1])
                else:
                    period_return = 0.0
                returns_before.append(period_return)

        # --- Compute round-trip costs ---
        total_commission = 0.0
        total_slippage = 0.0
        turnover = 0.0
        gross_pnl = sum(returns_before) if returns_before else 0.0

        if position != 0 and n > 0:
            entry_price = reference
            exit_price = actual_closes[-1]

            commission = cost_model.compute(entry_price, exit_price, position, cost_config)
            trade_value = abs(entry_price * abs(position))
            slippage = slippage_model.compute(trade_value, position, cost_config)
            total_commission = commission
            total_slippage = slippage
            turnover = abs(position) * (entry_price + exit_price) * cfg.leverage

        total_cost = total_commission + total_slippage
        # Convert cost to a percentage of notional for per-period deduction
        notional = abs(reference * abs(position) * cfg.leverage) if position != 0 else 1.0
        if notional <= 0:
            notional = 1.0
        cost_pct = total_cost / notional

        returns_after = [r - cost_pct / n for r in returns_before] if n > 0 else []
        net_pnl = sum(returns_after) if returns_after else 0.0

        # --- Max drawdown on cumulative net value ---
        cumulative = 1.0
        peak = 1.0
        max_drawdown = 0.0
        for r in returns_after:
            cumulative *= 1 + r
            peak = max(peak, cumulative)
            if peak > 0:
                max_drawdown = max(max_drawdown, (peak - cumulative) / peak)

        cost_result = CostResult(
            gross_pnl=round(gross_pnl, 10),
            total_costs=round(total_cost, 10),
            net_pnl=round(net_pnl, 10),
            turnover=round(turnover, 10),
            commission_cost=round(total_commission, 10),
            slippage_cost=round(total_slippage, 10),
            max_drawdown=round(max_drawdown, 10),
            short_selling_allowed=cfg.short_selling,
            leverage=cfg.leverage,
        )

        return {
            "signals": signals,
            "cost_result": cost_result,
            "positions": positions,
            "returns_before_cost": [round(r, 10) for r in returns_before],
            "returns_after_cost": [round(r, 10) for r in returns_after],
            "research_only": True,
            "suitable_for_live_trading": False,
        }
