"""Shareholder / capital allocation analysis."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric, PeriodType
from app.services.fundamental_analysis.common import metric
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ShareholderAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.cash_flow_statements:
            return metrics, []

        cf = data.cash_flow_statements[-1]
        income = data.income_statements[-1] if data.income_statements else None
        warnings: list[str] = []

        # Share issuance (absolute value of cash inflow)
        m = metric("share_issuance", "shareholder", cf.share_issuance, cf.period.period_end, cf.period.available_at, units="currency")
        if m is not None:
            metrics.append(m)

        # Buybacks (absolute)
        m = metric("buybacks", "shareholder", abs(cf.share_repurchases), cf.period.period_end, cf.period.available_at, units="currency")
        if m is not None:
            metrics.append(m)

        # Dividends (absolute)
        m = metric("dividends", "shareholder", abs(cf.dividends_paid), cf.period.period_end, cf.period.available_at, units="currency")
        if m is not None:
            metrics.append(m)

        # Net debt issuance
        m = metric(
            "net_debt_issuance",
            "shareholder",
            cf.debt_issued + cf.debt_repaid,
            cf.period.period_end,
            cf.period.available_at,
            units="currency",
        )
        if m is not None:
            metrics.append(m)

        # SBC burden
        if income and income.revenue > 0:
            m = metric(
                "sbc_burden",
                "shareholder",
                cf.stock_based_compensation / income.revenue,
                cf.period.period_end,
                cf.period.available_at,
                warnings=warnings.copy(),
            )
            if m is not None:
                metrics.append(m)

        # Capital return coverage by FCF
        fcf = cf.free_cash_flow or (cf.operating_cash_flow + cf.capital_expenditure)
        distributions = abs(cf.dividends_paid) + abs(cf.share_repurchases)
        if (
            fcf
            and fcf > 0
            and (
                m := metric(
                    "capital_return_coverage",
                    "shareholder",
                    fcf / distributions,
                    cf.period.period_end,
                    cf.period.available_at,
                    warnings=warnings.copy(),
                )
            )
        ):
            metrics.append(m)

        # Retained earnings trend (annual periods)
        if len(data.balance_sheets) >= 2:
            annual_bs = [s for s in data.balance_sheets if s.period.period_type is PeriodType.ANNUAL]
            if len(annual_bs) >= 2:
                current_re = annual_bs[-1].retained_earnings
                prior_re = annual_bs[0].retained_earnings
                trend = current_re - prior_re
                m = metric("retained_earnings_trend", "shareholder", trend, cf.period.period_end, cf.period.available_at, units="currency")
                if m is not None:
                    metrics.append(m)

        # Capital discipline trend (coverage change)
        if len(data.cash_flow_statements) >= 2 and income:
            cf_prior = data.cash_flow_statements[-2]
            fcf_prior = cf_prior.free_cash_flow or (cf_prior.operating_cash_flow + cf_prior.capital_expenditure)
            dist_prior = abs(cf_prior.dividends_paid) + abs(cf_prior.share_repurchases)
            if fcf is not None and fcf_prior is not None and fcf > 0 and dist_prior > 0:
                current_cov = fcf / distributions if distributions > 0 else float("inf")
                prior_cov = fcf_prior / dist_prior
                discipline = 1 if current_cov >= prior_cov else -1
                m = metric(
                    "capital_discipline_trend", "shareholder", discipline, cf.period.period_end, cf.period.available_at, units="direction"
                )
                if m is not None:
                    metrics.append(m)

        return metrics, []
