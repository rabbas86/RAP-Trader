"""Deterministic research-only paper execution simulator for Phase 16D.

The simulator binds a completed historical decision step to an explicit
paper-execution methodology and produces immutable paper-order, fill, and
execution-result artifacts through ``ArtifactStore``. It never connects to a
live broker, never mutates portfolio state, and never claims live-trading
authority.

No-lookahead guarantee
----------------------
A fill uses only historical prices whose semantic availability is at or
before the fill time. For the current completed-bar model, the close/high/low
of a bar are treated as available only after the bar completes, while the
open is treated as available at the start of the bar.

Phase 16D therefore exposes only one honest execution methodology:

* ``NEXT_BAR_CLOSE``: the decision submitted at time T is filled after the
  next completed bar closes, using that bar's close.

Future phases may extend the model if intrabar availability metadata becomes
canonically available.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domain.models.historical_replay import VALID_TIMEFRAMES, HistoricalReplaySpecification
from app.services.artifacts.base import ArtifactStore
from app.services.historical.clock import TIMEFRAME_DELTAS, _bar_available_at, _normalize_timeframe
from app.services.paper_execution.contracts import (
    PaperExecutionResult,
    PaperFill,
    PaperFillStatus,
    PaperOrder,
    PaperOrderSide,
    PaperOrderStatus,
)
from app.services.paper_execution.errors import (
    InvalidPaperInputError,
    MissingCanonicalSizingError,
    ReplayLinkageError,
    UnfilledOrderError,
)


class PaperExecutionSimulator:
    """Deterministic research-only paper execution simulator.

    The simulator is immutable after construction. Calling ``simulate()``
    with the same inputs produces the same paper artifacts.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore,
        specification: HistoricalReplaySpecification,
        step: Any,
        methodology: Any,
        producer_version: str = "phase16d-1.0",
    ) -> None:
        self.store = store
        self.specification = specification
        self.step = step
        self.methodology = methodology
        self.producer_version = producer_version

        if not hasattr(step, "suitable_for_live_trading") or step.suitable_for_live_trading:
            raise InvalidPaperInputError("historical decision step must be unsuitable for live trading")
        if not hasattr(step, "research_only") or not step.research_only:
            raise InvalidPaperInputError("historical decision step must be research-only")
        if not hasattr(step, "paper_trading_only") or not step.paper_trading_only:
            raise InvalidPaperInputError("historical decision step must be paper-trading-only")
        if hasattr(step, "status") and step.status != "completed":
            raise InvalidPaperInputError("historical decision step must be completed")
        if not hasattr(step, "trade_decision_artifact_id") or not step.trade_decision_artifact_id:
            raise InvalidPaperInputError("historical decision step must reference a trade decision artifact")
        if hasattr(step, "replay_specification_id") and step.replay_specification_id != specification.specification_id:
            raise ReplayLinkageError(
                f"step replay_specification_id {step.replay_specification_id} does not match specification {specification.specification_id}"
            )

    def simulate(
        self,
        decision: Any,
        bars: Any,
        execution_available_at: datetime,
    ) -> PaperExecutionResult:
        """Simulate paper execution for a historical trade decision.

        Parameters
        ----------
        decision:
            Canonical ``TradeDecision`` persisted for the historical step.
        bars:
            ``HistoricalBarsResult`` containing eligible historical bars for
            the decision symbol and timeframe.
        execution_available_at:
            The simulated time when execution may legally occur. This must
            be at or after the decision time and at or before the replay
            end time.

        Returns
        -------
        PaperExecutionResult
            Immutable paper execution result.
        """
        from app.domain.models.decision import TradeDecision
        from app.domain.models.market_data import HistoricalBarsResult

        if not isinstance(decision, TradeDecision):
            raise InvalidPaperInputError("decision must be a TradeDecision")
        if not isinstance(bars, HistoricalBarsResult):
            raise InvalidPaperInputError("bars must be a HistoricalBarsResult")
        if execution_available_at.tzinfo is None or execution_available_at.utcoffset() is None:
            raise InvalidPaperInputError("execution_available_at must be timezone-aware")

        execution_available_at = execution_available_at.astimezone(UTC)
        decision_time = self.step.simulated_at.astimezone(UTC)
        if execution_available_at < decision_time:
            raise InvalidPaperInputError("execution_available_at must be at or after the decision time")
        if execution_available_at > self.specification.end_time.astimezone(UTC):
            raise InvalidPaperInputError("execution_available_at must be at or before the replay end time")

        if str(bars.symbol) != decision.ticker:
            raise InvalidPaperInputError(f"bars symbol {bars.symbol} does not match decision ticker {decision.ticker}")
        if bars.timeframe not in VALID_TIMEFRAMES or bars.timeframe not in TIMEFRAME_DELTAS:
            raise InvalidPaperInputError(f"unsupported timeframe for paper execution: {bars.timeframe}")
        if not bars.bars:
            raise UnfilledOrderError("no historical bars provided for paper execution")

        side = self._side_for_action(decision.action)
        quantity = self._canonical_quantity(decision)
        order = PaperOrder.create(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=str(self.specification.run_id),
            historical_decision_step_id=self.step.step_id,
            trade_decision_artifact_id=self.step.trade_decision_artifact_id,
            symbol=decision.ticker,
            side=side,
            quantity=quantity,
            submitted_at=decision_time,
            eligible_execution_at=decision_time,
            execution_methodology_id=self.methodology.methodology_id,
        )

        if decision.action == "WAIT" or quantity <= 0:
            result = PaperExecutionResult.create(
                replay_specification_id=self.specification.specification_id,
                replay_run_id=str(self.specification.run_id),
                historical_decision_step_id=self.step.step_id,
                trade_decision_artifact_id=self.step.trade_decision_artifact_id,
                paper_order_id=order.paper_order_id,
                execution_methodology_id=self.methodology.methodology_id,
                symbol=decision.ticker,
                side=side,
                requested_quantity=quantity,
                filled_quantity=0,
                remaining_quantity=quantity,
                execution_status=PaperOrderStatus.UNFILLED,
                paper_fill_ids=(),
                transaction_cost_bps=self.methodology.transaction_cost_bps,
                additional_slippage_bps=self.methodology.additional_slippage_bps,
            )
            order_envelope = order.envelope(provenance_references=self._order_provenance(order))
            result_envelope = result.envelope(provenance_references=self._result_provenance(result, order_envelope.artifact_id))
            self.store.put(order_envelope)
            self.store.put(result_envelope)
            return result

        eligible_bar = self._eligible_bar(
            bars=bars,
            decision_time=decision_time,
            execution_available_at=execution_available_at,
        )

        if eligible_bar is None:
            result = PaperExecutionResult.create(
                replay_specification_id=self.specification.specification_id,
                replay_run_id=str(self.specification.run_id),
                historical_decision_step_id=self.step.step_id,
                trade_decision_artifact_id=self.step.trade_decision_artifact_id,
                paper_order_id=order.paper_order_id,
                execution_methodology_id=self.methodology.methodology_id,
                symbol=decision.ticker,
                side=side,
                requested_quantity=quantity,
                filled_quantity=0,
                remaining_quantity=quantity,
                execution_status=PaperOrderStatus.UNFILLED,
                paper_fill_ids=(),
                transaction_cost_bps=self.methodology.transaction_cost_bps,
                additional_slippage_bps=self.methodology.additional_slippage_bps,
            )
            order_envelope = order.envelope(provenance_references=self._order_provenance(order))
            result_envelope = result.envelope(provenance_references=self._result_provenance(result, order_envelope.artifact_id))
            self.store.put(order_envelope)
            self.store.put(result_envelope)
            return result

        fill = self._build_fill(
            order=order,
            bar=eligible_bar,
            side=side,
            quantity=quantity,
        )
        fill_envelope = fill.envelope(provenance_references=self._fill_provenance(fill, order))
        result = PaperExecutionResult.create(
            replay_specification_id=self.specification.specification_id,
            replay_run_id=str(self.specification.run_id),
            historical_decision_step_id=self.step.step_id,
            trade_decision_artifact_id=self.step.trade_decision_artifact_id,
            paper_order_id=order.paper_order_id,
            execution_methodology_id=self.methodology.methodology_id,
            symbol=decision.ticker,
            side=side,
            requested_quantity=quantity,
            filled_quantity=fill.quantity,
            remaining_quantity=quantity - fill.quantity,
            execution_status=PaperOrderStatus.FILLED if fill.status == PaperFillStatus.FULL else PaperOrderStatus.PARTIALLY_FILLED,
            execution_price=fill.execution_price,
            executed_at=fill.executed_at,
            paper_fill_ids=(fill.paper_fill_id,),
            transaction_cost_bps=self.methodology.transaction_cost_bps,
            additional_slippage_bps=self.methodology.additional_slippage_bps,
        )
        order_envelope = order.envelope(provenance_references=self._order_provenance(order))
        result_envelope = result.envelope(
            provenance_references=self._result_provenance(result, order_envelope.artifact_id, fill_envelope.artifact_id)
        )
        self.store.put(order_envelope)
        self.store.put(fill_envelope)
        self.store.put(result_envelope)
        return result

    def _side_for_action(self, action: str) -> PaperOrderSide:
        if action == "BUY":
            return PaperOrderSide.BUY
        if action == "SELL":
            return PaperOrderSide.SELL
        raise InvalidPaperInputError(f"unsupported trade decision action for paper execution: {action}")

    def _canonical_quantity(self, decision: Any) -> int:
        quantity = getattr(decision, "quantity", None)
        if quantity is None:
            raise MissingCanonicalSizingError(getattr(decision, "decision_artifact_id", "unknown"))
        if not isinstance(quantity, int) or quantity <= 0:
            raise MissingCanonicalSizingError(getattr(decision, "decision_artifact_id", "unknown"))
        return quantity

    def _eligible_bar(
        self,
        *,
        bars: Any,
        decision_time: datetime,
        execution_available_at: datetime,
    ) -> Any | None:
        timeframe = bars.timeframe
        eligible_bars = []
        for bar in bars.bars:
            if bar.timestamp <= decision_time:
                continue
            bar_available_at = _bar_available_at(bar, timeframe)
            if bar_available_at > execution_available_at:
                continue
            eligible_bars.append((bar.timestamp, bar_available_at, bar))
        if not eligible_bars:
            return None
        eligible_bars.sort(key=lambda item: item[0])
        return eligible_bars[0][2]

    def _build_fill(self, order: PaperOrder, bar: Any, side: PaperOrderSide, quantity: int) -> PaperFill:
        timeframe = self.specification.timeframes[0]
        step = TIMEFRAME_DELTAS[_normalize_timeframe(timeframe)]
        execution_price = bar.close
        executed_at = bar.timestamp + step
        return PaperFill.create(
            paper_order_id=order.paper_order_id,
            replay_specification_id=self.specification.specification_id,
            symbol=str(order.symbol),
            side=side,
            quantity=quantity,
            execution_price=execution_price,
            executed_at=executed_at,
            source_bar_timestamp=bar.timestamp,
            methodology_id=self.methodology.methodology_id,
            status=PaperFillStatus.FULL,
        )

    def _order_provenance(self, order: PaperOrder) -> tuple[Any, ...]:
        from app.domain.models.artifact import ProvenanceReference, ProvenanceReferenceKind

        return (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=self.step.trade_decision_artifact_id,
                description="canonical trade decision for paper order",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
            ProvenanceReference(
                kind=ProvenanceReferenceKind.HISTORICAL_DECISION_STEP,
                identifier=self.step.step_id,
                description="historical decision step bound to this paper order",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
        )

    def _fill_provenance(self, fill: PaperFill, order: PaperOrder) -> tuple[Any, ...]:
        from app.domain.models.artifact import ProvenanceReference, ProvenanceReferenceKind

        return (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=order.paper_order_id,
                description="paper order for this fill",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
            ProvenanceReference(
                kind=ProvenanceReferenceKind.PAPER_ORDER,
                identifier=order.paper_order_id,
                description="paper order for this fill",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
        )

    def _result_provenance(
        self,
        result: PaperExecutionResult,
        order_artifact_id: str,
        fill_artifact_id: str | None = None,
    ) -> tuple[Any, ...]:
        from app.domain.models.artifact import ProvenanceReference, ProvenanceReferenceKind

        references = [
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=order_artifact_id,
                description="paper order for this execution result",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
            ProvenanceReference(
                kind=ProvenanceReferenceKind.HISTORICAL_DECISION_STEP,
                identifier=self.step.step_id,
                description="historical decision step bound to this execution result",
                producer="rap-trader-phase16d",
                producer_version=self.producer_version,
            ),
        ]
        if fill_artifact_id:
            references.append(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.PAPER_FILL,
                    identifier=fill_artifact_id,
                    description="paper fill for this execution result",
                    producer="rap-trader-phase16d",
                    producer_version=self.producer_version,
                )
            )
        return tuple(references)


__all__ = ["PaperExecutionSimulator"]
