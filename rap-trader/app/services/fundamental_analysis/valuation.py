"""Market valuation ratios; missing market inputs are never fabricated."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import CompanyFundamentals, FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ValuationAnalysisService:
    def analyze(
        self, data: NormalizedFinancialStatements, fundamentals: CompanyFundamentals
    ) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.income_statements:
            return [], []
        income = data.income_statements[-1]
        balance = data.balance_sheets[-1] if data.balance_sheets else None
        cash = data.cash_flow_statements[-1] if data.cash_flow_statements else None
        cap, ev = fundamentals.market_cap, fundamentals.enterprise_value
        forward_earnings = fundamentals.source_metadata.get("explicit_forward_net_income")
        growth_assumption = fundamentals.source_metadata.get("explicit_valid_eps_growth")
        annual_dividends = fundamentals.source_metadata.get("explicit_annual_dividends")
        forward_pe = ratio(cap, float(forward_earnings)) if isinstance(forward_earnings, (int, float)) else None
        pe = ratio(cap, income.net_income)
        values = [
            ("pe", pe),
            ("forward_pe", forward_pe),
            ("pb", ratio(cap, balance.shareholders_equity if balance else None)),
            ("ps", ratio(cap, income.revenue)),
            ("ev_revenue", ratio(ev, income.revenue)),
            ("ev_ebitda", ratio(ev, income.ebitda)),
            ("ev_ebit", ratio(ev, income.ebit)),
            ("fcf_yield", ratio(cash.free_cash_flow if cash else None, cap)),
            ("earnings_yield", ratio(income.net_income, cap)),
            ("dividend_yield", ratio(float(annual_dividends), cap) if isinstance(annual_dividends, (int, float)) else None),
            ("peg", ratio(pe, float(growth_assumption) * 100) if isinstance(growth_assumption, (int, float)) else None),
        ]
        result = [metric(name, "valuation", value, income.period.period_end, income.period.available_at) for name, value in values]
        return [x for x in result if x], []
