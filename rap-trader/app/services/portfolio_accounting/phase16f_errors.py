"""Phase 16F compatibility shim for portfolio accounting errors.

All canonical definitions live in ``app.services.portfolio_accounting.errors``.
Importing from this module remains supported, but it no longer defines
independent exception classes.
"""

from __future__ import annotations

from app.services.portfolio_accounting.errors import (
    DuplicateCorporateActionError,
    DuplicateCostApplicationError,
    FutureCorporateActionError,
    IncompatiblePriceAdjustmentError,
    InvalidCorporateActionError,
    InvalidCostInputError,
    PortfolioAccountingValidationError,
    UnsupportedCorporateActionError,
)

__all__ = [
    "DuplicateCorporateActionError",
    "DuplicateCostApplicationError",
    "FutureCorporateActionError",
    "IncompatiblePriceAdjustmentError",
    "InvalidCorporateActionError",
    "InvalidCostInputError",
    "PortfolioAccountingValidationError",
    "UnsupportedCorporateActionError",
]
