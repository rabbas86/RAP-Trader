"""Profitability and margin analysis."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ProfitabilityAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.income_statements:
            return [], []
        income = data.income_statements[-1]
        balance = data.balance_sheets[-1] if data.balance_sheets else None
        pairs = [
            ("gross_margin", income.gross_profit, income.revenue),
            ("operating_margin", income.operating_income, income.revenue),
            ("ebitda_margin", income.ebitda, income.revenue),
            ("ebit_margin", income.ebit, income.revenue),
            ("net_margin", income.net_income, income.revenue),
            ("roa", income.net_income, balance.total_assets if balance else None),
            ("roe", income.net_income, balance.shareholders_equity if balance else None),
        ]
        if data.cash_flow_statements:
            pairs.append(("fcf_margin", data.cash_flow_statements[-1].free_cash_flow, income.revenue))
        result = [metric(name, "profitability", ratio(a, b), income.period.period_end, income.period.available_at) for name, a, b in pairs]
        clean = [x for x in result if x]
        margins = [ratio(x.operating_income, x.revenue) for x in data.income_statements[-3:]]
        valid_margins = [x for x in margins if x is not None]
        if len(valid_margins) == len(margins) and len(valid_margins) >= 2:
            trend = metric(
                "operating_margin_trend",
                "profitability",
                valid_margins[-1] - valid_margins[0],
                income.period.period_end,
                income.period.available_at,
            )
            if trend:
                clean.append(trend)
        return clean, []
