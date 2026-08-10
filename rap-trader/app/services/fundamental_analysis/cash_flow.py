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
        warning_by_metric: dict[str, list[str]] = {}

        if len(data.income_statements) >= 2 and len(data.cash_flow_statements) >= 2:
            previous_income = data.income_statements[-2]
            previous_cash = data.cash_flow_statements[-2]
            current_conversion = ratio(cash.operating_cash_flow, income.net_income if income else None)
            previous_conversion = ratio(previous_cash.operating_cash_flow, previous_income.net_income)
            if (
                income is not None
                and income.net_income > previous_income.net_income
                and current_conversion is not None
                and previous_conversion is not None
                and current_conversion < previous_conversion
            ):
                warning_by_metric.setdefault("cfo_net_income", []).append("earnings rose while cash conversion deteriorated")

        if len(data.cash_flow_statements) >= 2 and all(
            statement.free_cash_flow is not None and statement.free_cash_flow < 0 for statement in data.cash_flow_statements[-2:]
        ):
            warning_by_metric.setdefault("free_cash_flow", []).append("free cash flow was negative for two consecutive periods")

        if cash.stock_based_compensation >= abs(cash.share_repurchases):
            warning_by_metric.setdefault("buyback_coverage", []).append("stock compensation offsets share repurchases")

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
            warnings = list(warning_by_metric.get(name, ()))
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
