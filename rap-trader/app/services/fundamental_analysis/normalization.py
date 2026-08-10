"""Point-in-time financial-statement normalization without interpolation."""

from __future__ import annotations

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

    def normalize(self, fundamentals: CompanyFundamentals, as_of: datetime) -> NormalizedFinancialStatements:
        as_of = _require_aware_utc(as_of)
        warnings = list(fundamentals.warnings)
        incomes = self._deduplicate(fundamentals.income_statements, as_of, warnings)
        balances = self._deduplicate(fundamentals.balance_sheets, as_of, warnings)
        cashflows = self._deduplicate(fundamentals.cash_flow_statements, as_of, warnings)
        normalized_income = [
            item.model_copy(update={"gross_profit": item.revenue - item.cost_of_revenue}) if item.gross_profit is None else item
            for item in incomes
        ]
        normalized_cash = [
            item.model_copy(update={"free_cash_flow": item.operating_cash_flow - abs(item.capital_expenditure)})
            if item.free_cash_flow is None
            else item
            for item in cashflows
        ]
        keys = {(x.period.fiscal_year, x.period.fiscal_quarter) for x in normalized_income}
        years = sorted({year for year, quarter in keys if quarter is None})
        if years and years != list(range(years[0], years[-1] + 1)):
            warnings.append("missing annual periods detected")
        if any(x.total_assets < 0 and (x.current_assets > 0 or x.cash_and_equivalents > 0) for x in balances):
            warnings.append("impossible accounting relationship: negative total assets with positive components")
        all_items: list[IncomeStatement | BalanceSheet | CashFlowStatement] = [*normalized_income, *balances, *normalized_cash]
        annual = tuple(x for x in all_items if x.period.period_type is PeriodType.ANNUAL)
        quarterly = tuple(x for x in all_items if x.period.period_type is PeriodType.QUARTERLY)
        ttm = tuple(x for x in all_items if x.period.period_type is PeriodType.TTM)
        return NormalizedFinancialStatements(
            annual, quarterly, ttm, tuple(normalized_income), tuple(balances), tuple(normalized_cash), tuple(dict.fromkeys(warnings))
        )
