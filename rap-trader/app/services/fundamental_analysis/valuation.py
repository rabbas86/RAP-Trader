"""Valuation ratios; missing market inputs are never fabricated."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import CompanyFundamentals, FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ValuationAnalysisService:
    def analyze(
        self, data: NormalizedFinancialStatements, fundamentals: CompanyFundamentals
    ) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.income_statements or not data.balance_sheets:
            return metrics, []

        income = data.income_statements[-1]
        balance = data.balance_sheets[-1]
        cap, ev = fundamentals.market_cap, fundamentals.enterprise_value
        forward_earnings = fundamentals.source_metadata.get("explicit_forward_net_income")
        growth_assumption = fundamentals.source_metadata.get("explicit_valid_eps_growth")
        annual_dividends = fundamentals.source_metadata.get("explicit_annual_dividends")

        # P/E and earnings yield only with valid market cap and positive earnings
        if cap and income.net_income > 0:
            m = metric("pe", "valuation", ratio(cap, income.net_income), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)
            m = metric("earnings_yield", "valuation", ratio(income.net_income, cap), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)

        # Forward P/E only with explicit forward earnings
        if cap and forward_earnings and forward_earnings > 0:
            m = metric("forward_pe", "valuation", ratio(cap, forward_earnings), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)

        if cap and balance.shareholders_equity and balance.shareholders_equity > 0:
            m = metric("pb", "valuation", ratio(cap, balance.shareholders_equity), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)

        if cap and income.revenue > 0:
            m = metric("ps", "valuation", ratio(cap, income.revenue), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)

        if ev and income.revenue > 0:
            m = metric("ev_revenue", "valuation", ratio(ev, income.revenue), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)
            if income.ebitda and income.ebitda > 0:
                m = metric("ev_ebitda", "valuation", ratio(ev, income.ebitda), income.period.period_end, income.period.available_at)
                if m is not None:
                    metrics.append(m)
            if income.ebit and income.ebit > 0:
                m = metric("ev_ebit", "valuation", ratio(ev, income.ebit), income.period.period_end, income.period.available_at)
                if m is not None:
                    metrics.append(m)

        if cap and data.cash_flow_statements:
            cf = data.cash_flow_statements[-1]
            fcf = cf.free_cash_flow or (cf.operating_cash_flow + cf.capital_expenditure)
            if fcf and fcf > 0:
                m = metric("fcf_yield", "valuation", ratio(fcf, cap), income.period.period_end, income.period.available_at)
                if m is not None:
                    metrics.append(m)

        # PEG only when growth assumption is valid
        if cap and income.net_income > 0 and growth_assumption:
            pe = ratio(cap, income.net_income)
            if pe and growth_assumption > 0:
                m = metric(
                    "peg",
                    "valuation",
                    ratio(pe, growth_assumption),
                    income.period.period_end,
                    income.period.available_at,
                    assumptions=["PEG requires valid growth assumption"],
                )
                if m is not None:
                    metrics.append(m)

        # Dividend yield only when explicitly supplied
        if cap and annual_dividends and cap > 0:
            m = metric("dividend_yield", "valuation", ratio(annual_dividends, cap), income.period.period_end, income.period.available_at)
            if m is not None:
                metrics.append(m)

        return metrics, []
