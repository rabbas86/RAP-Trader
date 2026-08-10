"""Earnings-quality signals without fraud scoring or allegations."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import growth, metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class EarningsQualityService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.income_statements or not data.cash_flow_statements:
            return [], []
        income, cash = data.income_statements[-1], data.cash_flow_statements[-1]
        cfo_income = ratio(cash.operating_cash_flow, income.net_income)
        values = [
            ("accrual_intensity", None if cfo_income is None else 1 - cfo_income),
            ("cfo_net_income_quality", cfo_income),
            ("share_dilution", None),
        ]
        if len(data.income_statements) >= 2:
            values[-1] = (
                "share_dilution",
                growth(income.weighted_average_diluted_shares, data.income_statements[-2].weighted_average_diluted_shares)[0],
            )
        if len(data.balance_sheets) >= 2 and len(data.income_statements) >= 2:
            b, old_b, old_i = data.balance_sheets[-1], data.balance_sheets[-2], data.income_statements[-2]
            rec_growth = growth(b.accounts_receivable, old_b.accounts_receivable)[0]
            inv_growth = growth(b.inventory, old_b.inventory)[0]
            rev_growth = growth(income.revenue, old_i.revenue)[0]
            values.extend(
                (
                    ("receivables_vs_revenue", None if rec_growth is None or rev_growth is None else rec_growth - rev_growth),
                    ("inventory_vs_revenue", None if inv_growth is None or rev_growth is None else inv_growth - rev_growth),
                )
            )
        warnings = ["restatements reduce historical consistency"] if any(x.period.restated for x in data.income_statements) else []
        result = [
            metric(name, "earnings_quality", value, income.period.period_end, income.period.available_at, warnings=warnings)
            for name, value in values
        ]
        return [x for x in result if x], []
