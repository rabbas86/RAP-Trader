"""Earnings quality: accruals, cash-flow divergence, margin consistency, quality rating."""

from __future__ import annotations

from statistics import pstdev

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric, IncomeStatement, PeriodType
from app.services.fundamental_analysis.common import growth, metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class EarningsQualityService:
    _MARGIN_STD_DEV_THRESHOLD = 0.10

    @staticmethod
    def _margin_std_dev(incomes: tuple[IncomeStatement, ...], field: str) -> float | None:
        """Population standard deviation of a margin across periods."""
        margins: list[float] = []
        for income in incomes:
            m = ratio(getattr(income, field), income.revenue)
            if m is not None:
                margins.append(m)
        return pstdev(margins) if len(margins) >= 2 else None

    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.income_statements:
            return metrics, []

        income = data.income_statements[-1]
        cash = data.cash_flow_statements[-1] if data.cash_flow_statements else None
        warnings: list[str] = []

        if any(s.period.restated for s in data.income_statements):
            warnings.append("restatements reduce historical consistency")

        # CFO net income quality
        cfo_income = None
        if cash and income.net_income > 0:
            cfo_income = ratio(cash.operating_cash_flow, income.net_income)

        values = [
            ("accrual_intensity", None if cfo_income is None else 1 - cfo_income),
            ("cfo_net_income_quality", cfo_income),
        ]

        # Share dilution (YoY)
        if len(data.income_statements) >= 2:
            latest = data.income_statements[-1]
            prior = data.income_statements[-2]
            share_growth, _w = growth(latest.weighted_average_diluted_shares, prior.weighted_average_diluted_shares)
            values.append(("share_dilution", share_growth))

        # Receivables vs revenue divergence
        if len(data.balance_sheets) >= 2 and len(data.income_statements) >= 2:
            b, old_b, old_i = data.balance_sheets[-1], data.balance_sheets[-2], data.income_statements[-2]
            rec_growth, _ = growth(b.accounts_receivable, old_b.accounts_receivable)
            inv_growth, _ = growth(b.inventory, old_b.inventory)
            rev_growth, _ = growth(income.revenue, old_i.revenue)
            values.extend(
                [
                    ("receivables_vs_revenue", None if rec_growth is None or rev_growth is None else rec_growth - rev_growth),
                    ("inventory_vs_revenue", None if inv_growth is None or rev_growth is None else inv_growth - rev_growth),
                ]
            )

        for name, value in values:
            if value is not None:
                m = metric(name, "earnings_quality", value, income.period.period_end, income.period.available_at, warnings=warnings.copy())
                if m is not None:
                    metrics.append(m)

        # Margin consistency (annual periods)
        annual = tuple(s for s in data.income_statements if s.period.period_type is PeriodType.ANNUAL)
        margin_std_devs = {
            "gross_margin_consistency": self._margin_std_dev(annual, "gross_profit"),
            "operating_margin_consistency": self._margin_std_dev(annual, "operating_income"),
            "net_margin_consistency": self._margin_std_dev(annual, "net_income"),
        }
        inconsistent_margins = 0
        for name, value in margin_std_devs.items():
            margin_warnings: list[str] = []
            margin_assumptions: list[str] = []
            if value is not None:
                if value > self._MARGIN_STD_DEV_THRESHOLD:
                    inconsistent_margins += 1
                    margin_warnings.append(
                        f"{name.removesuffix('_consistency').replace('_', ' ')} varies by more than 10 percentage points"
                    )
                else:
                    margin_assumptions.append(
                        f"{name.removesuffix('_consistency').replace('_', ' ')} is stable across available annual periods"
                    )
            combined_warnings = list(warnings) + margin_warnings
            m = metric(
                name,
                "earnings_quality",
                value,
                income.period.period_end,
                income.period.available_at,
                warnings=combined_warnings,
                assumptions=margin_assumptions,
            )
            if m is not None:
                metrics.append(m)

        # One-period spike (latest net income > 2x prior 3-year average)
        spike_detected: bool | None = None
        if len(annual) >= 3:
            prior_incomes = [s.net_income for s in annual[:-1]]
            prior_average = sum(prior_incomes) / len(prior_incomes)
            spike_detected = prior_average > 0 and annual[-1].net_income > 2 * prior_average
            if spike_detected:
                warnings.append("latest annual net income is more than twice the average of prior annual periods")

        # Unusual divergence between profit and cash flow
        divergence_detected = None if cfo_income is None else cfo_income < 0.5 or cfo_income > 2.0
        if divergence_detected:
            warnings.append("unusual divergence between profit and operating cash flow")

        # Earnings quality rating (no fraud claims)
        margins_assessable = all(v is not None for v in margin_std_devs.values())
        rating, language = 0.0, "insufficient evidence"
        if cfo_income is not None and spike_detected is not None and margins_assessable:
            concerns = inconsistent_margins
            if cfo_income <= 0.8:
                concerns += 1
            if spike_detected:
                concerns += 1
            if divergence_detected:
                concerns += 1
            if concerns == 0:
                rating, language = 3.0, "high-quality earnings"
            elif concerns == 1:
                rating, language = 2.0, "moderate-quality earnings"
            else:
                rating, language = 1.0, "low-quality earnings"

        m = metric(
            "earnings_quality_rating",
            "earnings_quality",
            rating,
            income.period.period_end,
            income.period.available_at,
            units="quality_score",
            warnings=warnings,
            assumptions=[language],
        )
        if m is not None:
            metrics.append(m)

        return metrics, []
