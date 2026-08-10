"""Historical growth analysis."""

from math import pow

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import growth, metric
from app.services.fundamental_analysis.config import FundamentalAnalystConfig
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class GrowthAnalysisService:
    def __init__(self, config: FundamentalAnalystConfig | None = None) -> None:
        self.config = config or FundamentalAnalystConfig()

    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        annual = [x for x in data.income_statements if x.period.period_type.value == "ANNUAL"]
        result: list[FundamentalMetric] = []
        if len(annual) >= 2:
            old, new = annual[-2], annual[-1]
            for name, current, previous in (
                ("revenue_growth_yoy", new.revenue, old.revenue),
                ("eps_growth_yoy", new.diluted_eps, old.diluted_eps),
                ("net_income_growth_yoy", new.net_income, old.net_income),
                ("operating_income_growth_yoy", new.operating_income, old.operating_income),
                ("gross_profit_growth_yoy", new.gross_profit, old.gross_profit),
            ):
                value, warning = growth(float(current or 0), float(previous or 0))
                item = metric(name, "growth", value, new.period.period_end, new.period.available_at, warnings=[warning] if warning else [])
                if item:
                    result.append(item)
        if len(annual) >= self.config.min_annual_periods and annual[0].revenue > 0 and annual[-1].revenue >= 0:
            years = annual[-1].period.fiscal_year - annual[0].period.fiscal_year
            if years > 0:
                item = metric(
                    "revenue_cagr",
                    "growth",
                    pow(annual[-1].revenue / annual[0].revenue, 1 / years) - 1,
                    annual[-1].period.period_end,
                    annual[-1].period.available_at,
                )
                if item:
                    result.append(item)
        cash = [x for x in data.cash_flow_statements if x.period.period_type.value == "ANNUAL"]
        if len(cash) >= 2:
            value, warning = growth(float(cash[-1].free_cash_flow or 0), float(cash[-2].free_cash_flow or 0))
            item = metric(
                "free_cash_flow_growth_yoy",
                "growth",
                value,
                cash[-1].period.period_end,
                cash[-1].period.available_at,
                warnings=[warning] if warning else [],
            )
            if item:
                result.append(item)
        if len(annual) >= 2:
            value, warning = growth(annual[-1].weighted_average_diluted_shares, annual[-2].weighted_average_diluted_shares)
            item = metric(
                "shares_outstanding_growth",
                "growth",
                value,
                annual[-1].period.period_end,
                annual[-1].period.available_at,
                warnings=[warning] if warning else [],
            )
            if item:
                result.append(item)
        return result, []
