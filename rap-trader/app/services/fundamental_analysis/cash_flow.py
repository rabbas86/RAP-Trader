"""Cash-flow quality and distribution coverage analysis."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class CashFlowAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.cash_flow_statements:
            return [], []
        cash = data.cash_flow_statements[-1]
        income = data.income_statements[-1] if data.income_statements else None
        revenue = income.revenue if income else None
        values = [
            ("free_cash_flow", cash.free_cash_flow, 1.0),
            ("fcf_conversion", cash.free_cash_flow, income.net_income if income else None),
            ("cfo_net_income", cash.operating_cash_flow, income.net_income if income else None),
            ("capex_intensity", abs(cash.capital_expenditure), revenue),
            ("sbc_revenue", cash.stock_based_compensation, revenue),
            ("financing_dependence", max(0.0, cash.financing_cash_flow), abs(cash.operating_cash_flow)),
            ("dividend_coverage", cash.free_cash_flow, abs(cash.dividends_paid)),
            ("buyback_coverage", cash.free_cash_flow, abs(cash.share_repurchases)),
        ]
        result: list[FundamentalMetric] = []
        for name, numerator, denominator in values:
            value = float(numerator) if name == "free_cash_flow" and numerator is not None else ratio(numerator, denominator)
            warnings: list[str] = []
            if (
                name in {"dividend_coverage", "buyback_coverage"}
                and cash.debt_issued > cash.debt_repaid
                and cash.free_cash_flow is not None
                and cash.free_cash_flow < abs(cash.dividends_paid) + abs(cash.share_repurchases)
            ):
                warnings.append("potential debt-funded distributions")
            item = metric(
                name,
                "cash_flow",
                value,
                cash.period.period_end,
                cash.period.available_at,
                warnings=warnings,
                units="currency" if name == "free_cash_flow" else "ratio",
            )
            if item:
                result.append(item)
        return result, []
