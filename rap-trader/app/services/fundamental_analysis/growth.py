"""Growth analysis: revenue, EPS, FCF, and share-dilution growth rates."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric, PeriodType
from app.services.fundamental_analysis.common import growth, metric
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class GrowthAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if len(data.income_statements) < 2:
            return metrics, []

        latest = data.income_statements[-1]
        prior = data.income_statements[-2]

        # YoY growth metrics
        pairs = [
            ("revenue_growth_yoy", latest.revenue, prior.revenue),
            ("operating_income_growth_yoy", latest.operating_income, prior.operating_income),
            ("net_income_growth_yoy", latest.net_income, prior.net_income),
        ]
        if latest.ebitda is not None and prior.ebitda is not None:
            pairs.append(("ebitda_growth_yoy", latest.ebitda, prior.ebitda))
        if latest.gross_profit is not None and prior.gross_profit is not None:
            pairs.append(("gross_profit_growth_yoy", latest.gross_profit, prior.gross_profit))

        for name, current, old in pairs:
            value, w = growth(current, old)
            if m := metric(name, "growth", value, latest.period.period_end, latest.period.available_at, warnings=w):
                metrics.append(m)

        # EPS growth
        value, w = growth(latest.diluted_eps, prior.diluted_eps)
        if m := metric("eps_growth_yoy", "growth", value, latest.period.period_end, latest.period.available_at, warnings=w):
            metrics.append(m)

        # Free cash flow growth
        if len(data.cash_flow_statements) >= 2:
            cf_latest = data.cash_flow_statements[-1]
            cf_prior = data.cash_flow_statements[-2]
            fcf_latest = cf_latest.free_cash_flow or (cf_latest.operating_cash_flow + cf_latest.capital_expenditure)
            fcf_prior = cf_prior.free_cash_flow or (cf_prior.operating_cash_flow + cf_prior.capital_expenditure)
            value, w = growth(fcf_latest, fcf_prior)
            if m := metric("fcf_growth_yoy", "growth", value, cf_latest.period.period_end, cf_latest.period.available_at, warnings=w):
                metrics.append(m)

        # Revenue CAGR (annual periods)
        annual = [s for s in data.income_statements if s.period.period_type is PeriodType.ANNUAL]
        if len(annual) >= 2:
            revenues = [s.revenue for s in annual]
            years = [s.period.fiscal_year for s in annual]
            if revenues[0] > 0 and revenues[-1] > 0:
                n = years[-1] - years[0]
                if n > 0:
                    cagr = (revenues[-1] / revenues[0]) ** (1.0 / n) - 1.0
                    if m := metric("revenue_cagr", "growth", cagr, latest.period.period_end, latest.period.available_at):
                        metrics.append(m)

        # Share dilution
        if len(data.income_statements) >= 2:
            value, w = growth(latest.weighted_average_diluted_shares, prior.weighted_average_diluted_shares)
            if m := metric("shares_outstanding_growth", "growth", value, latest.period.period_end, latest.period.available_at, warnings=w):
                metrics.append(m)

        return metrics, []
