"""Point-in-time financial-statement normalization without interpolation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from app.domain.models.analyst import AnalystError, AnalystErrorCodes
from app.domain.models.fundamental import (
    BalanceSheet,
    CashFlowStatement,
    CompanyFundamentals,
    IncomeStatement,
    PeriodType,
)
from app.domain.models.market_data import _require_aware_utc

Statement = TypeVar("Statement", IncomeStatement, BalanceSheet, CashFlowStatement)


@dataclass(frozen=True)
class NormalizedFinancialStatements:
    annual_periods: tuple[IncomeStatement | BalanceSheet | CashFlowStatement, ...]
    quarterly_periods: tuple[IncomeStatement | BalanceSheet | CashFlowStatement, ...]
    ttm_periods: tuple[IncomeStatement | BalanceSheet | CashFlowStatement, ...]
    income_statements: tuple[IncomeStatement, ...]
    balance_sheets: tuple[BalanceSheet, ...]
    cash_flow_statements: tuple[CashFlowStatement, ...]
    warnings: tuple[str, ...]


class FinancialDataNormalizationService:
    _RELATIVE_TOLERANCE = 1e-3

    @staticmethod
    def _key(item: Statement) -> tuple[int, int | None, PeriodType]:
        return item.period.fiscal_year, item.period.fiscal_quarter, item.period.period_type

    def _deduplicate(self, values: list[Statement], as_of: datetime, warnings: list[str]) -> list[Statement]:
        selected: dict[tuple[int, int | None, PeriodType], Statement] = {}
        for item in values:
            if item.period.available_at > as_of:
                raise AnalystError(AnalystErrorCodes.LOOKAHEAD_REJECTED, "A filing was unavailable at the requested as-of time")
            key = self._key(item)
            previous = selected.get(key)
            if previous is not None:
                warnings.append(f"duplicate period detected: {key}")
                if item.period.restated:
                    warnings.append(f"restatement selected: {key}")
                if item.period.available_at >= previous.period.available_at:
                    selected[key] = item
            else:
                selected[key] = item
        return sorted(selected.values(), key=lambda x: (x.period.period_end, x.period.available_at))

    @classmethod
    def _approximately_equal(cls, left: float, right: float) -> bool:
        tolerance = cls._RELATIVE_TOLERANCE * max(abs(left), abs(right), 1.0)
        return abs(left - right) <= tolerance

    def _normalize_signs(
        self,
        incomes: list[IncomeStatement],
        balances: list[BalanceSheet],
        cashflows: list[CashFlowStatement],
        warnings: list[str],
    ) -> tuple[list[IncomeStatement], list[BalanceSheet], list[CashFlowStatement]]:
        normalized_incomes: list[IncomeStatement] = []
        for income in incomes:
            updates = {
                field: -abs(getattr(income, field)) for field in ("cost_of_revenue", "operating_expense", "interest_expense", "tax_expense")
            }
            normalized_incomes.append(income.model_copy(update=updates))

        normalized_balances: list[BalanceSheet] = []
        for balance in balances:
            updates = {
                field: abs(getattr(balance, field))
                for field in (
                    "accounts_payable",
                    "current_liabilities",
                    "total_liabilities",
                    "short_term_debt",
                    "long_term_debt",
                    "total_debt",
                    "shareholders_equity",
                )
            }
            normalized_balance = balance.model_copy(update=updates)
            period = self._key(normalized_balance)
            debt_components = normalized_balance.short_term_debt + normalized_balance.long_term_debt
            accounting_total = normalized_balance.shareholders_equity + normalized_balance.total_liabilities

            if normalized_balance.total_assets < normalized_balance.current_assets:
                warnings.append(f"impossible accounting relationship: total assets below current assets: {period}")
            if normalized_balance.total_debt < debt_components and not self._approximately_equal(
                normalized_balance.total_debt, debt_components
            ):
                warnings.append(f"impossible accounting relationship: total debt below debt components: {period}")
            elif not self._approximately_equal(normalized_balance.total_debt, debt_components):
                warnings.append(f"total debt differs from short-term plus long-term debt: {period}")
            if normalized_balance.total_liabilities < (normalized_balance.current_liabilities + normalized_balance.long_term_debt):
                warnings.append(f"impossible accounting relationship: total liabilities below components: {period}")
            if not self._approximately_equal(normalized_balance.total_assets, accounting_total):
                warnings.append(f"accounting identity violation: total assets differ from liabilities plus shareholders' equity: {period}")
            normalized_balances.append(normalized_balance)

        normalized_cashflows = [
            cashflow.model_copy(update={"capital_expenditure": -abs(cashflow.capital_expenditure)}) for cashflow in cashflows
        ]
        return normalized_incomes, normalized_balances, normalized_cashflows

    @staticmethod
    def _detect_missing_periods(values: Sequence[IncomeStatement | BalanceSheet | CashFlowStatement], warnings: list[str]) -> None:
        annual_years = sorted({item.period.fiscal_year for item in values if item.period.period_type is PeriodType.ANNUAL})
        if annual_years and annual_years != list(range(annual_years[0], annual_years[-1] + 1)):
            warnings.append("missing annual periods detected")

        quarterly_ordinals = sorted(
            {
                item.period.fiscal_year * 4 + item.period.fiscal_quarter - 1
                for item in values
                if item.period.period_type is PeriodType.QUARTERLY and item.period.fiscal_quarter is not None
            }
        )
        if quarterly_ordinals and quarterly_ordinals != list(range(quarterly_ordinals[0], quarterly_ordinals[-1] + 1)):
            warnings.append("missing quarterly periods detected")

    def normalize(self, fundamentals: CompanyFundamentals, as_of: datetime) -> NormalizedFinancialStatements:
        as_of = _require_aware_utc(as_of)
        warnings = list(fundamentals.warnings)
        incomes = self._deduplicate(fundamentals.income_statements, as_of, warnings)
        balances = self._deduplicate(fundamentals.balance_sheets, as_of, warnings)
        cashflows = self._deduplicate(fundamentals.cash_flow_statements, as_of, warnings)
        incomes, balances, cashflows = self._normalize_signs(incomes, balances, cashflows, warnings)
        normalized_income = [
            item.model_copy(update={"gross_profit": item.revenue + item.cost_of_revenue}) if item.gross_profit is None else item
            for item in incomes
        ]
        normalized_cash = [
            item.model_copy(update={"free_cash_flow": item.operating_cash_flow + item.capital_expenditure})
            if item.free_cash_flow is None
            else item
            for item in cashflows
        ]
        for statements in (normalized_income, balances, normalized_cash):
            self._detect_missing_periods(statements, warnings)
        all_items: list[IncomeStatement | BalanceSheet | CashFlowStatement] = [*normalized_income, *balances, *normalized_cash]
        annual = tuple(x for x in all_items if x.period.period_type is PeriodType.ANNUAL)
        quarterly = tuple(x for x in all_items if x.period.period_type is PeriodType.QUARTERLY)
        ttm = tuple(x for x in all_items if x.period.period_type is PeriodType.TTM)
        return NormalizedFinancialStatements(
            annual, quarterly, ttm, tuple(normalized_income), tuple(balances), tuple(normalized_cash), tuple(dict.fromkeys(warnings))
        )
