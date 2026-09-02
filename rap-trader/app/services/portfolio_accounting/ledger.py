"""Deterministic portfolio ledger for Phase 16E."""

from __future__ import annotations

from typing import Any

from app.domain.models.artifact import ArtifactType, ProvenanceReference, ProvenanceReferenceKind
from app.domain.models.historical_replay import HistoricalReplaySpecification
from app.domain.models.market_data import Symbol
from app.services.artifacts.base import ArtifactStore
from app.services.paper_execution.contracts import PaperExecutionResult, PaperOrderSide, PaperOrderStatus
from app.services.portfolio_accounting.errors import (
    DuplicateCorporateActionError,
    DuplicateCostApplicationError,
    IncompatiblePriceAdjustmentError,
    InsufficientCashError,
    InvalidCorporateActionError,
    InvalidFillError,
    PortfolioAccountingValidationError,
    UnauthorizedShortError,
    UnsupportedCorporateActionError,
)
from app.services.portfolio_accounting.models import (
    PortfolioAccountingMethodology,
    PortfolioFillApplicationResult,
    PortfolioLedgerEntry,
    PortfolioSnapshot,
    PositionState,
)
from app.services.portfolio_accounting.phase16f_models import (
    CorporateActionEvent,
    CorporateActionType,
    DividendEntitlement,
    ExecutionCostAssessment,
    PortfolioAdjustmentLedgerEntry,
    PortfolioAdjustmentType,
)

LedgerEntry = PortfolioLedgerEntry | PortfolioAdjustmentLedgerEntry


class PortfolioLedger:
    """Immutable, append-only portfolio ledger.

    The ledger consumes ordered ``PaperExecutionResult`` artifacts and
    produces deterministic ``PortfolioSnapshot`` and
    ``PortfolioLedgerEntry`` artifacts. It never mutates prior history.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore,
        specification: HistoricalReplaySpecification,
        methodology: PortfolioAccountingMethodology,
        producer_version: str = "phase16e-1.0",
        price_adjustment_convention: str = "unadjusted",
    ) -> None:
        self.store = store
        self.specification = specification
        self.methodology = methodology
        self.producer_version = producer_version
        self.price_adjustment_convention = price_adjustment_convention
        self._applied_execution_result_ids: set[str] = set()
        self._applied_assessment_ids: set[str] = set()
        self._applied_corporate_action_ids: set[str] = set()
        self._applied_entitlement_ids: set[str] = set()
        self._sequence = 0
        self._latest_snapshot_id: str | None = None
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        latest_snapshot: tuple[str, dict[str, Any]] | None = None
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            if envelope.artifact_type != ArtifactType.PORTFOLIO_SNAPSHOT:
                continue
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            simulated_at = payload.get("simulated_at")
            if not isinstance(simulated_at, str):
                continue
            if latest_snapshot is None or simulated_at > latest_snapshot[0]:
                latest_snapshot = (simulated_at, payload)
        if latest_snapshot is not None:
            self._latest_snapshot_id = latest_snapshot[1].get("portfolio_snapshot_id")
            applied_fill_ids = latest_snapshot[1].get("applied_fill_ids", ()) or ()
            self._applied_execution_result_ids.update(applied_fill_ids)
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            if envelope.artifact_type != ArtifactType.PORTFOLIO_LEDGER_ENTRY:
                continue
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            sequence = payload.get("sequence")
            if isinstance(sequence, int) and sequence >= self._sequence:
                self._sequence = sequence + 1
            result_id = payload.get("paper_execution_result_id")
            if isinstance(result_id, str) and result_id:
                self._applied_execution_result_ids.add(result_id)
            assessment_id = payload.get("assessment_id")
            if isinstance(assessment_id, str) and assessment_id:
                self._applied_assessment_ids.add(assessment_id)
            corporate_action_id = payload.get("corporate_action_id")
            if isinstance(corporate_action_id, str) and corporate_action_id:
                self._applied_corporate_action_ids.add(corporate_action_id)
            entitlement_id = payload.get("entitlement_id")
            if isinstance(entitlement_id, str) and entitlement_id:
                self._applied_entitlement_ids.add(entitlement_id)

    def initial_snapshot(self) -> tuple[PortfolioSnapshot, PortfolioLedgerEntry]:
        """Create the deterministic initial portfolio snapshot."""
        if self._latest_snapshot_id is not None:
            raise PortfolioAccountingValidationError("initial snapshot can only be created once")
        snapshot = PortfolioSnapshot.create_initial(self.specification, producer_version=self.producer_version)
        ledger_entry = PortfolioLedgerEntry.create_initial(
            snapshot=snapshot,
            methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        return snapshot, ledger_entry

    def apply_fill(
        self,
        prior_snapshot: PortfolioSnapshot,
        fill_result: PaperExecutionResult,
        mark_price: float | None = None,
    ) -> PortfolioFillApplicationResult:
        """Apply a filled execution result to the prior snapshot."""
        self._ensure_fill_idempotent(fill_result)
        self._ensure_latest_snapshot(prior_snapshot)
        self._ensure_fill_applicable(fill_result)

        symbol = fill_result.symbol
        side = fill_result.side
        quantity = fill_result.filled_quantity
        execution_price = fill_result.execution_price
        executed_at = fill_result.executed_at
        assert execution_price is not None
        assert executed_at is not None

        positions = {position.symbol_str: position for position in prior_snapshot.positions}
        cash = round(prior_snapshot.cash, 10)
        realized_pnl = prior_snapshot.realized_pnl

        if side == PaperOrderSide.BUY:
            cash, positions = self._apply_buy(
                symbol=symbol,
                quantity=quantity,
                execution_price=execution_price,
                cash=cash,
                positions=positions,
            )
        elif side == PaperOrderSide.SELL:
            existing_position = positions.get(str(symbol))
            if existing_position is not None:
                realized_pnl = round(
                    realized_pnl + (execution_price - existing_position.average_cost) * quantity,
                    10,
                )
            cash, positions = self._apply_sell(
                symbol=symbol,
                quantity=quantity,
                execution_price=execution_price,
                cash=cash,
                positions=positions,
            )
        else:
            raise InvalidFillError(f"unsupported order side: {side}")

        accounted_positions = tuple(positions[key] for key in sorted(positions.keys()))
        new_positions = tuple(position for position in accounted_positions if not position.is_flat)
        new_positions, unrealized_pnl, market_value = self._apply_mark_price(new_positions, mark_price)
        total_cost_basis = round(sum(position.cost_basis for position in new_positions), 10)

        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=prior_snapshot.replay_specification_id,
            replay_run_id=prior_snapshot.replay_run_id,
            simulated_at=executed_at,
            base_currency=prior_snapshot.base_currency,
            cash=round(cash, 10),
            positions=new_positions,
            total_cost_basis=total_cost_basis,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            market_value=market_value,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            applied_fill_ids=tuple(sorted(set(prior_snapshot.applied_fill_ids) | set(fill_result.paper_fill_ids))),
            accounting_methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})

        ledger_entry = PortfolioLedgerEntry.create_fill(
            snapshot=snapshot,
            result=fill_result,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            methodology_id=self.methodology.methodology_id,
            sequence=self._sequence,
            producer_version=self.producer_version,
        )

        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        self._sequence += 1
        for fill_id in fill_result.paper_fill_ids:
            self._applied_execution_result_ids.add(fill_id)
        self._applied_execution_result_ids.add(fill_result.paper_execution_result_id)
        return PortfolioFillApplicationResult(snapshot=snapshot, ledger_entry=ledger_entry, applied=True)

    def record_noop(
        self,
        prior_snapshot: PortfolioSnapshot,
        execution_result: PaperExecutionResult,
        mark_price: float | None = None,
    ) -> PortfolioFillApplicationResult:
        """Record an auditable no-op for an unfilled execution result."""
        self._ensure_latest_snapshot(prior_snapshot)
        if execution_result.filled_quantity > 0:
            raise InvalidFillError("noop entries cannot reference filled execution results")
        executed_at = execution_result.executed_at or prior_snapshot.simulated_at
        new_positions = tuple(prior_snapshot.positions)
        new_positions, unrealized_pnl, market_value = self._apply_mark_price(new_positions, mark_price)
        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=prior_snapshot.replay_specification_id,
            replay_run_id=prior_snapshot.replay_run_id,
            simulated_at=executed_at,
            base_currency=prior_snapshot.base_currency,
            cash=prior_snapshot.cash,
            positions=new_positions,
            total_cost_basis=prior_snapshot.total_cost_basis,
            realized_pnl=prior_snapshot.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            market_value=market_value,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            applied_fill_ids=prior_snapshot.applied_fill_ids,
            accounting_methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})

        provisional = PortfolioLedgerEntry(
            portfolio_ledger_entry_id="0" * 64,
            portfolio_snapshot_id=snapshot.portfolio_snapshot_id,
            replay_specification_id=snapshot.replay_specification_id,
            replay_run_id=snapshot.replay_run_id,
            simulated_at=snapshot.simulated_at,
            executed_at=executed_at,
            paper_execution_result_id=execution_result.paper_execution_result_id,
            paper_fill_ids=(),
            prior_portfolio_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            methodology_id=self.methodology.methodology_id,
            sequence=self._sequence,
            event_type="noop",
            producer_version=self.producer_version,
        )
        canonical_id = provisional._canonical_ledger_entry_id()
        ledger_entry = PortfolioLedgerEntry.model_validate(
            {**provisional.model_dump(mode="json"), "portfolio_ledger_entry_id": canonical_id}
        )

        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        self._sequence += 1
        self._applied_execution_result_ids.add(execution_result.paper_execution_result_id)
        return PortfolioFillApplicationResult(snapshot=snapshot, ledger_entry=ledger_entry, applied=True)

    def _apply_buy(
        self,
        *,
        symbol: Symbol,
        quantity: int,
        execution_price: float,
        cash: float,
        positions: dict[str, PositionState],
    ) -> tuple[float, dict[str, PositionState]]:
        required_cash = round(quantity * execution_price, 10)
        if cash < required_cash - 1e-9:
            raise InsufficientCashError(available_cash=cash, required_cash=required_cash, symbol=str(symbol))
        cash = round(cash - required_cash, 10)
        existing = positions.get(str(symbol))
        if existing is None:
            positions[str(symbol)] = PositionState(
                symbol=symbol,
                quantity=quantity,
                average_cost=execution_price,
                cost_basis=round(quantity * execution_price, 10),
                realized_pnl=0.0,
            )
        else:
            old_quantity = existing.quantity + quantity
            old_cost_basis = round(existing.cost_basis + round(quantity * execution_price, 10), 10)
            new_average_cost = round(old_cost_basis / old_quantity, 10) if old_quantity > 0 else 0.0
            positions[str(symbol)] = PositionState(
                symbol=symbol,
                quantity=old_quantity,
                average_cost=new_average_cost,
                cost_basis=round(old_quantity * new_average_cost, 10),
                realized_pnl=existing.realized_pnl,
            )
        return cash, positions

    def _apply_sell(
        self,
        *,
        symbol: Symbol,
        quantity: int,
        execution_price: float,
        cash: float,
        positions: dict[str, PositionState],
    ) -> tuple[float, dict[str, PositionState]]:
        existing = positions.get(str(symbol))
        long_quantity = existing.quantity if existing is not None else 0
        if long_quantity < quantity:
            raise UnauthorizedShortError(symbol=str(symbol), requested_quantity=quantity, long_quantity=long_quantity)
        assert existing is not None
        realized_pnl = round((execution_price - existing.average_cost) * quantity, 10)
        cash = round(cash + round(quantity * execution_price, 10), 10)
        remaining_quantity = long_quantity - quantity
        if remaining_quantity == 0:
            positions[str(symbol)] = PositionState(
                symbol=symbol,
                quantity=0,
                average_cost=0.0,
                cost_basis=0.0,
                realized_pnl=round(existing.realized_pnl + realized_pnl, 10),
            )
        else:
            positions[str(symbol)] = PositionState(
                symbol=symbol,
                quantity=remaining_quantity,
                average_cost=existing.average_cost,
                cost_basis=round(remaining_quantity * existing.average_cost, 10),
                realized_pnl=round(existing.realized_pnl + realized_pnl, 10),
            )
        return cash, positions

    def _apply_mark_price(
        self,
        positions: tuple[PositionState, ...],
        mark_price: float | None,
    ) -> tuple[tuple[PositionState, ...], float | None, float | None]:
        if mark_price is None:
            return positions, None, None
        if mark_price <= 0:
            raise PortfolioAccountingValidationError("mark_price must be positive")
        marked: list[PositionState] = []
        total_unrealized_pnl = 0.0
        total_market_value = 0.0
        for position in positions:
            if position.quantity <= 0:
                marked.append(position)
                continue
            unrealized_pnl = round((mark_price - position.average_cost) * position.quantity, 10)
            market_value = round(mark_price * position.quantity, 10)
            total_unrealized_pnl = round(total_unrealized_pnl + unrealized_pnl, 10)
            total_market_value = round(total_market_value + market_value, 10)
            marked.append(
                PositionState(
                    symbol=position.symbol,
                    quantity=position.quantity,
                    average_cost=position.average_cost,
                    cost_basis=position.cost_basis,
                    realized_pnl=position.realized_pnl,
                    last_mark_price=mark_price,
                    unrealized_pnl=unrealized_pnl,
                    market_value=market_value,
                )
            )
        return tuple(marked), round(total_unrealized_pnl, 10), round(total_market_value, 10)

    def _ensure_latest_snapshot(self, prior_snapshot: PortfolioSnapshot) -> None:
        if prior_snapshot.portfolio_snapshot_id != self._latest_snapshot_id:
            raise PortfolioAccountingValidationError(
                "prior snapshot is not the latest ledger snapshot; out-of-order application is forbidden"
            )

    def _ensure_fill_applicable(self, fill_result: PaperExecutionResult) -> None:
        if fill_result.filled_quantity <= 0:
            raise InvalidFillError("filled execution results require positive filled_quantity")
        if fill_result.execution_status not in {PaperOrderStatus.FILLED, PaperOrderStatus.PARTIALLY_FILLED}:
            raise InvalidFillError("only filled or partially filled results can be applied")
        if fill_result.execution_price is None or fill_result.executed_at is None:
            raise InvalidFillError("filled execution results require execution_price and executed_at")
        if fill_result.executed_at < fill_result.executed_at:
            raise InvalidFillError("execution timestamp is invalid")
        if fill_result.side not in {PaperOrderSide.BUY, PaperOrderSide.SELL}:
            raise InvalidFillError(f"unsupported order side: {fill_result.side}")

    def _ensure_fill_idempotent(self, fill_result: PaperExecutionResult) -> None:
        if fill_result.paper_execution_result_id in self._applied_execution_result_ids:
            raise InvalidFillError(f"execution result {fill_result.paper_execution_result_id} has already been applied")
        for fill_id in fill_result.paper_fill_ids:
            if fill_id in self._applied_execution_result_ids:
                raise InvalidFillError(f"fill {fill_id} has already been applied")

    def _persist(self, snapshot: PortfolioSnapshot, ledger_entry: LedgerEntry) -> None:
        snapshot_envelope = snapshot.envelope(provenance_references=self._snapshot_provenance(snapshot, ledger_entry))
        ledger_envelope = ledger_entry.envelope(provenance_references=self._ledger_provenance(ledger_entry, snapshot_envelope.artifact_id))
        self.store.put(snapshot_envelope)
        self.store.put(ledger_envelope)

    def _snapshot_provenance(self, snapshot: PortfolioSnapshot, ledger_entry: LedgerEntry) -> tuple[ProvenanceReference, ...]:
        references: list[ProvenanceReference] = [
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=self.specification.specification_id,
                description="historical replay specification",
                producer="rap-trader-phase16a",
                producer_version="1.0",
            ),
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=ledger_entry.adjustment_id if hasattr(ledger_entry, "adjustment_id") else ledger_entry.portfolio_ledger_entry_id,
                description="ledger entry identifier for this snapshot",
                producer="rap-trader-phase16e",
                producer_version=self.producer_version,
            ),
        ]
        if snapshot.prior_snapshot_id:
            references.append(
                ProvenanceReference(
                    kind=ProvenanceReferenceKind.ARTIFACT,
                    identifier=snapshot.prior_snapshot_id,
                    description="prior portfolio snapshot",
                    producer="rap-trader-phase16e",
                    producer_version=self.producer_version,
                )
            )
        return tuple(references)

    def _latest_execution_cost_ledger_entry(self) -> PortfolioAdjustmentLedgerEntry:
        return self._latest_adjustment_ledger_entry(PortfolioAdjustmentType.EXECUTION_COST.value)

    def _latest_corporate_action_ledger_entry(self) -> PortfolioAdjustmentLedgerEntry:
        return self._latest_adjustment_ledger_entry(PortfolioAdjustmentType.CORPORATE_ACTION_SPLIT.value)

    def _latest_dividend_payment_ledger_entry(self) -> PortfolioAdjustmentLedgerEntry:
        return self._latest_adjustment_ledger_entry(PortfolioAdjustmentType.DIVIDEND_PAYMENT.value)

    def _latest_adjustment_ledger_entry(self, event_type: str) -> PortfolioAdjustmentLedgerEntry:
        latest: PortfolioAdjustmentLedgerEntry | None = None
        for artifact_id in self.store.list_ids():
            envelope = self.store.get(artifact_id)
            if envelope.artifact_type != ArtifactType.PORTFOLIO_LEDGER_ENTRY:
                continue
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            if payload.get("event_type") != event_type:
                continue
            if latest is None or payload.get("sequence", -1) > latest.sequence:
                latest = PortfolioAdjustmentLedgerEntry.model_validate(payload)
        if latest is None:
            raise PortfolioAccountingValidationError(f"no ledger entry found for {event_type}")
        return latest

    def apply_execution_cost(self, prior_snapshot: PortfolioSnapshot, assessment: ExecutionCostAssessment) -> PortfolioSnapshot:
        self._ensure_latest_snapshot(prior_snapshot)
        if assessment.assessment_id in self._applied_assessment_ids:
            raise DuplicateCostApplicationError(assessment_id=assessment.assessment_id)
        if self.price_adjustment_convention != "unadjusted":
            raise IncompatiblePriceAdjustmentError(price_adjustment=self.price_adjustment_convention)
        cash = round(prior_snapshot.cash - assessment.total_transaction_cost, 10)
        if cash < -1e-9:
            raise InsufficientCashError(available_cash=prior_snapshot.cash, required_cash=assessment.total_transaction_cost, symbol="")
        positions = tuple(prior_snapshot.positions)
        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=prior_snapshot.replay_specification_id,
            replay_run_id=prior_snapshot.replay_run_id,
            simulated_at=assessment.simulated_at,
            base_currency=prior_snapshot.base_currency,
            cash=cash,
            positions=positions,
            total_cost_basis=prior_snapshot.total_cost_basis,
            realized_pnl=prior_snapshot.realized_pnl,
            unrealized_pnl=prior_snapshot.unrealized_pnl,
            market_value=prior_snapshot.market_value,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            applied_fill_ids=prior_snapshot.applied_fill_ids,
            accounting_methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})
        ledger_entry = PortfolioAdjustmentLedgerEntry.create_execution_cost(
            snapshot=snapshot,
            assessment=assessment,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            sequence=self._sequence,
            producer_version=self.producer_version,
        )
        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        self._sequence += 1
        self._applied_assessment_ids.add(assessment.assessment_id)
        return snapshot

    def apply_corporate_action(self, prior_snapshot: PortfolioSnapshot, corporate_action: CorporateActionEvent) -> PortfolioSnapshot:
        self._ensure_latest_snapshot(prior_snapshot)
        if corporate_action.corporate_action_id in self._applied_corporate_action_ids:
            raise DuplicateCorporateActionError(action_id=corporate_action.corporate_action_id)
        if corporate_action.action_type not in {
            CorporateActionType.STOCK_SPLIT.value,
            CorporateActionType.CASH_DIVIDEND.value,
        }:
            raise UnsupportedCorporateActionError(action_type=corporate_action.action_type)
        if self.price_adjustment_convention != "unadjusted":
            raise IncompatiblePriceAdjustmentError(price_adjustment=self.price_adjustment_convention)
        if corporate_action.action_type == CorporateActionType.STOCK_SPLIT.value:
            return self._apply_stock_split(prior_snapshot, corporate_action)
        raise InvalidCorporateActionError(f"unsupported corporate action type: {corporate_action.action_type}")

    def _apply_stock_split(self, prior_snapshot: PortfolioSnapshot, corporate_action: CorporateActionEvent) -> PortfolioSnapshot:
        split_ratio = corporate_action.split_ratio
        if split_ratio is None:
            raise InvalidCorporateActionError("stock_split requires split_ratio")
        numerator, denominator = split_ratio
        if denominator <= 0:
            raise InvalidCorporateActionError("split_ratio denominator must be positive")
        new_positions = []
        for position in prior_snapshot.positions:
            if position.quantity % denominator != 0:
                raise InvalidCorporateActionError("fractional share splits are not supported")
            new_quantity = position.quantity * numerator // denominator
            new_average_cost = round(position.average_cost * denominator / numerator, 10) if numerator else 0.0
            new_cost_basis = round(new_quantity * new_average_cost, 10)
            new_positions.append(
                PositionState(
                    symbol=position.symbol,
                    quantity=new_quantity,
                    average_cost=new_average_cost,
                    cost_basis=new_cost_basis,
                    realized_pnl=position.realized_pnl,
                )
            )
        positions = tuple(new_positions)
        total_cost_basis = round(sum(position.cost_basis for position in positions), 10)
        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=prior_snapshot.replay_specification_id,
            replay_run_id=prior_snapshot.replay_run_id,
            simulated_at=corporate_action.effective_at or prior_snapshot.simulated_at,
            base_currency=prior_snapshot.base_currency,
            cash=prior_snapshot.cash,
            positions=positions,
            total_cost_basis=total_cost_basis,
            realized_pnl=prior_snapshot.realized_pnl,
            unrealized_pnl=prior_snapshot.unrealized_pnl,
            market_value=prior_snapshot.market_value,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            applied_fill_ids=prior_snapshot.applied_fill_ids,
            accounting_methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})
        ledger_entry = PortfolioAdjustmentLedgerEntry.create_corporate_action_split(
            snapshot=snapshot,
            corporate_action=corporate_action,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            sequence=self._sequence,
            producer_version=self.producer_version,
        )
        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        self._sequence += 1
        self._applied_corporate_action_ids.add(corporate_action.corporate_action_id)
        return snapshot

    def apply_dividend_payment(self, prior_snapshot: PortfolioSnapshot, entitlement: DividendEntitlement) -> PortfolioSnapshot:
        self._ensure_latest_snapshot(prior_snapshot)
        if entitlement.entitlement_id in self._applied_entitlement_ids:
            raise DuplicateCostApplicationError(assessment_id=entitlement.entitlement_id)
        cash = round(prior_snapshot.cash + entitlement.gross_cash_amount, 10)
        snapshot = PortfolioSnapshot(
            portfolio_snapshot_id="0" * 64,
            replay_specification_id=prior_snapshot.replay_specification_id,
            replay_run_id=prior_snapshot.replay_run_id,
            simulated_at=entitlement.payment_at,
            base_currency=prior_snapshot.base_currency,
            cash=cash,
            positions=prior_snapshot.positions,
            total_cost_basis=prior_snapshot.total_cost_basis,
            realized_pnl=prior_snapshot.realized_pnl,
            unrealized_pnl=prior_snapshot.unrealized_pnl,
            market_value=prior_snapshot.market_value,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            applied_fill_ids=prior_snapshot.applied_fill_ids,
            accounting_methodology_id=self.methodology.methodology_id,
            producer_version=self.producer_version,
        )
        snapshot_id = snapshot._canonical_snapshot_id()
        snapshot = PortfolioSnapshot.model_validate({**snapshot.model_dump(mode="json"), "portfolio_snapshot_id": snapshot_id})
        ledger_entry = PortfolioAdjustmentLedgerEntry.create_dividend_payment(
            snapshot=snapshot,
            entitlement=entitlement,
            prior_snapshot_id=prior_snapshot.portfolio_snapshot_id,
            sequence=self._sequence,
            producer_version=self.producer_version,
        )
        self._persist(snapshot, ledger_entry)
        self._latest_snapshot_id = snapshot.portfolio_snapshot_id
        self._sequence += 1
        self._applied_entitlement_ids.add(entitlement.entitlement_id)
        return snapshot

    def _ledger_provenance(self, ledger_entry: LedgerEntry, snapshot_artifact_id: str) -> tuple[ProvenanceReference, ...]:
        return (
            ProvenanceReference(
                kind=ProvenanceReferenceKind.ARTIFACT,
                identifier=snapshot_artifact_id,
                description="portfolio snapshot produced by this ledger entry",
                producer="rap-trader-phase16e",
                producer_version=self.producer_version,
            ),
        )


__all__ = ["PortfolioLedger"]
