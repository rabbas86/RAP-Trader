"""Capital efficiency: ROIC, ROCE, asset turnover with documented assumptions."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements

# ROIC formula assumptions documented as a module-level constant.
# NOPAT = EBIT * (1 - tax rate), where tax rate = tax_expense / pretax_income.
# Invested Capital = total_assets - current_liabilities - accounts_payable + cash_and_equivalents.
# When EBIT, tax expense, current liabilities, or balance-sheet components are missing,
# ROIC is marked unavailable rather than approximated.
ROIC_FORMULA_ASSUMPTIONS: tuple[str, ...] = (
    "NOPAT = EBIT * (1 - tax rate)",
    "tax rate = tax_expense / pretax_income",
    "Invested Capital = total_assets - current_liabilities - accounts_payable + cash_and_equivalents",
    "When EBIT, tax expense, current liabilities, or balance-sheet components are missing, ROIC is unavailable",
)


class CapitalEfficiencyAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.income_statements or not data.balance_sheets:
            return metrics, []

        income = data.income_statements[-1]
        balance = data.balance_sheets[-1]
        warnings: list[str] = []

        # ROIC
        ebit = income.ebit
        if ebit is None:
            ebit = income.operating_income
        tax_rate = ratio(income.tax_expense, income.pretax_income) if income.pretax_income else None
        if ebit is not None and tax_rate is not None:
            nopat = ebit * (1 - tax_rate)
            invested_capital = balance.total_assets - balance.current_liabilities - balance.accounts_payable + balance.cash_and_equivalents
            if invested_capital > 0:
                roic = nopat / invested_capital
                m = metric(
                    "roic",
                    "capital_efficiency",
                    roic,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=warnings,
                    assumptions=list(ROIC_FORMULA_ASSUMPTIONS),
                )
                if m is not None:
                    metrics.append(m)
        else:
            warnings.append("roic suppressed: incomplete EBIT or tax inputs")

        # ROCE
        if income.operating_income and balance.total_assets:
            cap_employed = balance.total_assets - balance.current_liabilities
            if cap_employed > 0:
                roce = income.operating_income / cap_employed
                m = metric(
                    "roce",
                    "capital_efficiency",
                    roce,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=warnings,
                    assumptions=["ROCE = EBIT / (Total Assets - Current Liabilities)"],
                )
                if m is not None:
                    metrics.append(m)

        # Asset turnover
        if income.revenue and balance.total_assets and balance.total_assets > 0:
            m = metric(
                "asset_turnover",
                "capital_efficiency",
                income.revenue / balance.total_assets,
                income.period.period_end,
                income.period.available_at,
            )
            if m is not None:
                metrics.append(m)

        # Working capital efficiency
        if income.revenue and balance.accounts_receivable and balance.accounts_receivable > 0:
            m = metric(
                "working_capital_efficiency",
                "capital_efficiency",
                income.revenue / balance.accounts_receivable,
                income.period.period_end,
                income.period.available_at,
            )
            if m is not None:
                metrics.append(m)

        # Incremental ROIC (latest vs prior)
        if len(data.income_statements) >= 2 and len(data.balance_sheets) >= 2:
            prev_income = data.income_statements[-2]
            prev_balance = data.balance_sheets[-2]
            prev_ebit = prev_income.ebit or prev_income.operating_income
            prev_tax_rate = ratio(prev_income.tax_expense, prev_income.pretax_income) if prev_income.pretax_income else None
            if ebit is not None and prev_ebit is not None and tax_rate is not None and prev_tax_rate is not None:
                prev_invested = (
                    prev_balance.total_assets
                    - prev_balance.current_liabilities
                    - prev_balance.accounts_payable
                    + prev_balance.cash_and_equivalents
                )
                if prev_invested > 0:
                    inc_roic = (ebit * (1 - tax_rate)) / prev_invested
                    m = metric(
                        "incremental_roic",
                        "capital_efficiency",
                        inc_roic,
                        income.period.period_end,
                        income.period.available_at,
                        assumptions=list(ROIC_FORMULA_ASSUMPTIONS),
                    )
                    if m is not None:
                        metrics.append(m)

        return metrics, []
