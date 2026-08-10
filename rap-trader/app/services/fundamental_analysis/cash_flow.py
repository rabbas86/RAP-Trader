"""Cash flow quality: FCF, conversion, capex, SBC, dividends, debt-funded distributions."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric, PeriodType
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class CashFlowAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.cash_flow_statements or not data.income_statements:
            return metrics, []

        cf = data.cash_flow_statements[-1]
        income = data.income_statements[-1]
        warnings: list[str] = []

        # Free cash flow (derived in normalization if missing)
        fcf = cf.free_cash_flow or (cf.operating_cash_flow + cf.capital_expenditure)
        if fcf is not None and (
            m := metric("free_cash_flow", "cash_flow", fcf, cf.period.period_end, cf.period.available_at, units="currency")
        ):
            metrics.append(m)

        # CFO / Net income conversion
        if income.net_income > 0:
            cfo_income = ratio(cf.operating_cash_flow, income.net_income)
            m = metric("cfo_net_income", "cash_flow", cfo_income, cf.period.period_end, cf.period.available_at, warnings=warnings.copy())
            if m is not None:
                metrics.append(m)

        # Capex intensity
        if income.revenue and income.revenue > 0:
            capex_intensity = abs(cf.capital_expenditure) / income.revenue
            m = metric(
                "capex_intensity", "cash_flow", capex_intensity, cf.period.period_end, cf.period.available_at, warnings=warnings.copy()
            )
            if m is not None:
                metrics.append(m)

        # SBC burden
        if income.revenue and income.revenue > 0:
            sbc_burden = cf.stock_based_compensation / income.revenue
            m = metric("sbc_burden", "cash_flow", sbc_burden, cf.period.period_end, cf.period.available_at, warnings=warnings.copy())
            if m is not None:
                metrics.append(m)

        # Financing dependence
        financing = cf.share_issuance + cf.debt_issued
        if income.net_income and abs(income.net_income) > 0:
            fdep = ratio(financing, abs(income.net_income))
            m = metric("financing_dependence", "cash_flow", fdep, cf.period.period_end, cf.period.available_at, warnings=warnings.copy())
            if m is not None:
                metrics.append(m)

        # Dividend coverage
        if income.net_income > 0:
            div_cov = ratio(income.net_income, abs(cf.dividends_paid))
            m = metric("dividend_coverage", "cash_flow", div_cov, cf.period.period_end, cf.period.available_at, warnings=warnings.copy())
            if m is not None:
                metrics.append(m)

        # Buyback coverage
        if income.net_income > 0 and cf.free_cash_flow is not None and fcf > 0:
            bk_cov = ratio(fcf, abs(cf.share_repurchases))
            m = metric("buyback_coverage", "cash_flow", bk_cov, cf.period.period_end, cf.period.available_at, warnings=warnings.copy())
            if m is not None:
                metrics.append(m)

        # Debt-funded distributions
        debt_funded = cf.debt_issued + cf.share_issuance
        distributions = abs(cf.dividends_paid) + abs(cf.share_repurchases)
        if cf.operating_cash_flow and cf.operating_cash_flow < distributions:
            warnings.append("distributions exceed operating cash flow; potential debt-funded")
            m = metric(
                "debt_funded_distributions",
                "cash_flow",
                1 if debt_funded > 0 else 0,
                cf.period.period_end,
                cf.period.available_at,
                units="flag",
                warnings=warnings.copy(),
            )
            if m is not None:
                metrics.append(m)

        # Multi-period deterioration: CFO decline over 3+ annual periods
        annual_cf = [s for s in data.cash_flow_statements if s.period.period_type is PeriodType.ANNUAL]
        if len(annual_cf) >= 3:
            cfo_values = [s.operating_cash_flow for s in annual_cf[-3:]]
            if all(v is not None for v in cfo_values) and cfo_values[-1] < cfo_values[0] * 0.7:
                deterioration = (cfo_values[-1] / cfo_values[0]) - 1.0
                m = metric(
                    "cf_deterioration_3y",
                    "cash_flow",
                    deterioration,
                    cf.period.period_end,
                    cf.period.available_at,
                    warnings=["CFO declined by more than 30% over 3 years"],
                )
                if m is not None:
                    metrics.append(m)

        return metrics, []
