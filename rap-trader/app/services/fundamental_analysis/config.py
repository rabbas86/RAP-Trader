"""Configuration for the Phase 7 Fundamental Analyst."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.models.analyst import AnalystRole

# ROIC formula assumptions documented as a module-level constant.
# NOPAT = EBIT * (1 - tax rate), where tax rate = tax_expense / pretax_income.
# Invested Capital = total_assets - current_liabilities - accounts_payable + cash_and_equivalents.
# When EBIT, tax expense, current liabilities, or balance-sheet components are missing,
# ROIC is marked unavailable rather than approximated.
ROIC_FORMULA_ASSUMPTIONS: tuple[str, ...] = (
    "NOPAT = EBIT * (1 - tax rate)",
    "tax rate = tax_expense / pretax_income",
    "Invested Capital = total_assets - current_liabilities - accounts_payable + cash_and_equivalents",
    "When EBIT, tax expense, or balance-sheet components are missing, ROIC is unavailable",
)

ROIC_INCOMPLETE_INPUTS: tuple[str, ...] = (
    "EBIT is null",
    "pretax_income <= 0 (tax rate indeterminate)",
    "current_liabilities is null",
    "accounts_payable is null",
    "cash_and_equivalents is null",
)


@dataclass(frozen=True)
class FundamentalAnalystConfig:
    """Configuration for the deterministic, research-only fundamental analyst."""

    analyst_id: str = "fundamental"
    role: AnalystRole = AnalystRole.FUNDAMENTAL
    research_only: bool = True
    suitable_for_live_trading: bool = False
    stale_input_allowed: bool = False
    min_lookahead_days: int = 0  # reject any future-dated filings

    @property
    def roic_formula_assumptions(self) -> tuple[str, ...]:
        return ROIC_FORMULA_ASSUMPTIONS

    @property
    def roic_incomplete_inputs(self) -> tuple[str, ...]:
        return ROIC_INCOMPLETE_INPUTS
