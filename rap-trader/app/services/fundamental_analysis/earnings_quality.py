"""Earnings-quality signals without fraud scoring or allegations."""

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
        margins: list[float] = []
        for income in incomes:
            numerator = getattr(income, field)
            margin = ratio(numerator, income.revenue)
            if margin is not None:
                margins.append(margin)
        return pstdev(margins) if len(margins) >= 2 else None

    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.income_statements:
            return [], []

        income = data.income_statements[-1]
        cash = data.cash_flow_statements[-1] if data.cash_flow_statements else None
        cfo_income = None if cash is None else ratio(cash.operating_cash_flow, income.net_income)
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

        annual_incomes = tuple(statement for statement in data.income_statements if statement.period.period_type is PeriodType.ANNUAL)
        margin_std_devs = {
            "gross_margin_consistency": self._margin_std_dev(annual_incomes, "gross_profit"),
            "operating_margin_consistency": self._margin_std_dev(annual_incomes, "operating_income"),
            "net_margin_consistency": self._margin_std_dev(annual_incomes, "net_income"),
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
            result.append(
                metric(
                    name,
                    "earnings_quality",
                    value,
                    income.period.period_end,
                    income.period.available_at,
                    warnings=(*warnings, *margin_warnings),
                    assumptions=margin_assumptions,
                )
            )

        spike_detected: bool | None = None
        if len(annual_incomes) >= 3:
            prior_net_incomes = [statement.net_income for statement in annual_incomes[:-1]]
            prior_average = sum(prior_net_incomes) / len(prior_net_incomes)
            spike_detected = prior_average > 0 and annual_incomes[-1].net_income > 2 * prior_average
            if spike_detected:
                warnings.append("latest annual net income is more than twice the average of prior annual periods")

        divergence_detected = None if cfo_income is None else cfo_income < 0.5 or cfo_income > 2.0
        if divergence_detected:
            warnings.append("unusual divergence between profit and operating cash flow")

        margins_assessable = all(value is not None for value in margin_std_devs.values())
        if cfo_income is None or spike_detected is None or not margins_assessable:
            rating, language = 0.0, "insufficient evidence"
        else:
            concerns = inconsistent_margins + int(cfo_income <= 0.8) + int(spike_detected is True) + int(divergence_detected is True)
            if concerns == 0:
                rating, language = 3.0, "high-quality earnings"
            elif concerns == 1:
                rating, language = 2.0, "moderate-quality earnings"
            else:
                rating, language = 1.0, "low-quality earnings"
        result.append(
            metric(
                "earnings_quality_rating",
                "earnings_quality",
                rating,
                income.period.period_end,
                income.period.available_at,
                units="quality_score",
                warnings=warnings,
                assumptions=[language],
            )
        )
        return [item for item in result if item is not None], []
