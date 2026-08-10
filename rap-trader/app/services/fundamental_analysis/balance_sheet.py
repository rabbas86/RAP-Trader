"""Liquidity and leverage analysis."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class BalanceSheetAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.balance_sheets:
            return [], []
        b = data.balance_sheets[-1]
        income = data.income_statements[-1] if data.income_statements else None
        ebitda = income.ebitda if income else None
        ebit = income.ebit if income else None
        values = [
            ("current_ratio", b.current_assets, b.current_liabilities),
            ("quick_ratio", b.current_assets - b.inventory, b.current_liabilities),
            ("cash_ratio", b.cash_and_equivalents + b.short_term_investments, b.current_liabilities),
            ("debt_to_equity", b.total_debt, b.shareholders_equity),
            ("debt_to_assets", b.total_debt, b.total_assets),
            ("net_debt", b.total_debt - b.cash_and_equivalents - b.short_term_investments, 1.0),
            ("net_debt_to_ebitda", b.total_debt - b.cash_and_equivalents - b.short_term_investments, ebitda),
            ("interest_coverage", ebit, income.interest_expense if income else None),
            ("goodwill_intangibles_concentration", b.goodwill + b.intangible_assets, b.total_assets),
            ("working_capital", b.current_assets - b.current_liabilities, 1.0),
        ]
        result: list[FundamentalMetric] = []
        for name, numerator, denominator in values:
            warnings = []
            if b.shareholders_equity < 0:
                warnings.append("negative shareholders equity")
            if ebitda is not None and ebitda < 0:
                warnings.append("negative EBITDA makes leverage ratios unsuitable")
            value = numerator if name in {"net_debt", "working_capital"} else ratio(numerator, denominator)
            item = metric(
                name,
                "balance_sheet",
                value,
                b.period.period_end,
                b.period.available_at,
                warnings=warnings,
                units="currency" if name in {"net_debt", "working_capital"} else "ratio",
            )
            if item:
                result.append(item)
        if len(data.balance_sheets) >= 2:
            prior = data.balance_sheets[-2]
            item = metric(
                "working_capital_trend",
                "balance_sheet",
                (b.current_assets - b.current_liabilities) - (prior.current_assets - prior.current_liabilities),
                b.period.period_end,
                b.period.available_at,
                units="currency",
            )
            if item:
                result.append(item)
        return result, []
