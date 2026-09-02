"""Portfolio accounting errors for Phase 16E."""

from __future__ import annotations


class PortfolioAccountingError(Exception):
    """Base portfolio accounting error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class PortfolioLedgerError(PortfolioAccountingError):
    """Base ledger error."""


class InsufficientCashError(PortfolioLedgerError):
    def __init__(self, available_cash: float, required_cash: float, symbol: str) -> None:
        super().__init__(
            code="INSUFFICIENT_CASH",
            message="Insufficient cash for requested BUY.",
        )
        self.available_cash = available_cash
        self.required_cash = required_cash
        self.symbol = symbol


class UnauthorizedShortError(PortfolioLedgerError):
    def __init__(self, symbol: str, requested_quantity: int, long_quantity: int) -> None:
        super().__init__(
            code="UNAUTHORIZED_SHORT",
            message="SELL exceeds current long position; shorting is not authorized.",
        )
        self.symbol = symbol
        self.requested_quantity = requested_quantity
        self.long_quantity = long_quantity


class InvalidFillError(PortfolioLedgerError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_FILL", message=message)


class InvalidMethodologyError(PortfolioLedgerError):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_METHODOLOGY", message=message)


class PortfolioAccountingValidationError(PortfolioLedgerError):
    def __init__(self, message: str, *, code: str = "VALIDATION_ERROR") -> None:
        super().__init__(code=code, message=message)


class InvalidCostInputError(PortfolioAccountingValidationError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message)


class InvalidCorporateActionError(PortfolioAccountingValidationError):
    def __init__(self, message: str) -> None:
        super().__init__(message=message)


class FutureCorporateActionError(PortfolioAccountingValidationError):
    def __init__(self, action_id: str, simulated_at: str) -> None:
        super().__init__(
            code="FUTURE_CORPORATE_ACTION",
            message=f"corporate action {action_id} is not applicable at simulated time {simulated_at}",
        )
        self.action_id = action_id
        self.simulated_at = simulated_at


class DuplicateCostApplicationError(PortfolioAccountingValidationError):
    def __init__(self, assessment_id: str) -> None:
        super().__init__(code="DUPLICATE_COST_APPLICATION", message="cost assessment has already been applied")
        self.assessment_id = assessment_id


class DuplicateCorporateActionError(PortfolioAccountingValidationError):
    def __init__(self, action_id: str) -> None:
        super().__init__(code="DUPLICATE_CORPORATE_ACTION", message="corporate action has already been applied")
        self.action_id = action_id


class UnsupportedCorporateActionError(PortfolioAccountingValidationError):
    def __init__(self, action_type: str) -> None:
        super().__init__(code="UNSUPPORTED_CORPORATE_ACTION", message=f"corporate action type {action_type} is not supported")
        self.action_type = action_type


class IncompatiblePriceAdjustmentError(PortfolioAccountingValidationError):
    def __init__(self, price_adjustment: str) -> None:
        super().__init__(
            code="INCOMPATIBLE_PRICE_ADJUSTMENT",
            message=f"incompatible price adjustment convention: {price_adjustment}",
        )
        self.price_adjustment = price_adjustment


__all__ = [
    "DuplicateCorporateActionError",
    "DuplicateCostApplicationError",
    "FutureCorporateActionError",
    "IncompatiblePriceAdjustmentError",
    "InsufficientCashError",
    "InvalidCorporateActionError",
    "InvalidCostInputError",
    "InvalidFillError",
    "InvalidMethodologyError",
    "PortfolioAccountingError",
    "PortfolioAccountingValidationError",
    "PortfolioLedgerError",
    "UnauthorizedShortError",
    "UnsupportedCorporateActionError",
]
