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
    def __init__(self, message: str) -> None:
        super().__init__(code="VALIDATION_ERROR", message=message)


__all__ = [
    "InsufficientCashError",
    "InvalidFillError",
    "InvalidMethodologyError",
    "PortfolioAccountingError",
    "PortfolioAccountingValidationError",
    "PortfolioLedgerError",
    "UnauthorizedShortError",
]
