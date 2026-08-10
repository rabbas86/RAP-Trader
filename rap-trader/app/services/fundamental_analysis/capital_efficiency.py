"""Capital-efficiency analysis with explicit ROIC assumptions."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.config import FundamentalAnalystConfig
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class CapitalEfficiencyAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.income_statements or not data.balance_sheets:
            return [], []
        income, balance = data.income_statements[-1], data.balance_sheets[-1]
        ebit = income.ebit
        tax_rate = ratio(income.tax_expense, income.pretax_income)
        invested = balance.total_assets - balance.current_liabilities - balance.accounts_payable + balance.cash_and_equivalents
        roic = ratio(ebit * (1 - tax_rate), invested) if ebit is not None and tax_rate is not None else None
        assumptions = [FundamentalAnalystConfig.ROIC_FORMULA_ASSUMPTIONS]
        values = [
            ("roic", roic),
            ("roce", ratio(ebit, balance.total_assets - balance.current_liabilities) if ebit is not None else None),
            ("asset_turnover", ratio(income.revenue, balance.total_assets)),
            ("working_capital_efficiency", ratio(income.revenue, balance.current_assets - balance.current_liabilities)),
        ]
        result = [
            metric(
                name,
                "capital_efficiency",
                value,
                income.period.period_end,
                income.period.available_at,
                assumptions=assumptions if name == "roic" else [],
            )
            for name, value in values
        ]
        clean = [x for x in result if x]
        if len(data.income_statements) >= 2 and len(data.balance_sheets) >= 2:
            old_i, old_b = data.income_statements[-2], data.balance_sheets[-2]
            delta_capital = invested - (
                old_b.total_assets - old_b.current_liabilities - old_b.accounts_payable + old_b.cash_and_equivalents
            )
            incremental = ratio(income.operating_income - old_i.operating_income, delta_capital)
            item = metric(
                "incremental_roic",
                "capital_efficiency",
                incremental,
                income.period.period_end,
                income.period.available_at,
                assumptions=["Operating income change proxies incremental NOPAT before tax precision"],
            )
            if item:
                clean.append(item)
        return clean, []
