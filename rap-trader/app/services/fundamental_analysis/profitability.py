"""Profitability: margins, ROA, ROE, and margin trend."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric, PeriodType
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ProfitabilityAnalysisService:
    _TREND_THRESHOLD = 0.05

    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.income_statements:
            return metrics, []

        income = data.income_statements[-1]
        rev = income.revenue
        warnings: list[str] = []
        if rev <= 0:
            warnings.append("zero or negative revenue; margin metrics suppressed")

        # Margins
        margin_pairs = [
            ("gross_margin", income.gross_profit),
            ("operating_margin", income.operating_income),
            ("ebitda_margin", income.ebitda),
            ("ebit_margin", income.ebit),
            ("net_margin", income.net_income),
        ]
        for name, numerator in margin_pairs:
            if numerator is not None and rev > 0:
                m = metric(
                    name, "profitability", numerator / rev, income.period.period_end, income.period.available_at, warnings=warnings.copy()
                )
                if m is not None:
                    metrics.append(m)

        # FCF margin
        if data.cash_flow_statements:
            cf = data.cash_flow_statements[-1]
            fcf = cf.free_cash_flow or (cf.operating_cash_flow + cf.capital_expenditure)
            if fcf is not None and rev > 0:
                m = metric(
                    "fcf_margin", "profitability", fcf / rev, income.period.period_end, income.period.available_at, warnings=warnings.copy()
                )
                if m is not None:
                    metrics.append(m)

        # ROA and ROE (need balance sheet)
        if data.balance_sheets:
            bs = data.balance_sheets[-1]
            if bs.total_assets > 0:
                m = metric(
                    "roa",
                    "profitability",
                    income.net_income / bs.total_assets,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=warnings.copy(),
                )
                if m is not None:
                    metrics.append(m)
            if bs.shareholders_equity > 0:
                m = metric(
                    "roe",
                    "profitability",
                    income.net_income / bs.shareholders_equity,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=warnings.copy(),
                )
                if m is not None:
                    metrics.append(m)

        # Margin trend (annual periods)
        annual = [s for s in data.income_statements if s.period.period_type is PeriodType.ANNUAL]
        if len(annual) >= 2:
            latest_margin = ratio(annual[-1].operating_income, annual[-1].revenue)
            prior_margin = ratio(annual[0].operating_income, annual[0].revenue)
            if latest_margin is not None and prior_margin is not None:
                trend = latest_margin - prior_margin
                trend_warnings: list[str] = []
                trend_assumptions: list[str] = []
                if abs(trend) > self._TREND_THRESHOLD:
                    if trend < 0:
                        trend_warnings.append(f"operating margin declined by {abs(trend) * 100:.1f} percentage points")
                    else:
                        trend_warnings.append(f"operating margin improved by {trend * 100:.1f} percentage points")
                else:
                    trend_assumptions.append("operating margin is stable across annual periods")
                m = metric(
                    "operating_margin_trend",
                    "profitability",
                    trend,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=trend_warnings,
                    assumptions=trend_assumptions,
                )
                if m is not None:
                    metrics.append(m)

        return metrics, []
