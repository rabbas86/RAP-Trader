"""Shareholder capital-allocation analysis."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import CashFlowStatement, FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ShareholderAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        result: list[FundamentalMetric | None] = []
        if data.cash_flow_statements:
            cash = data.cash_flow_statements[-1]
            revenue = data.income_statements[-1].revenue if data.income_statements else None
            distributions = abs(cash.share_repurchases) + abs(cash.dividends_paid)
            debt_funded = cash.debt_issued > abs(cash.debt_repaid) and (cash.free_cash_flow or 0) < distributions
            debt_warnings = ["debt-funded distributions may weaken capital discipline"] if debt_funded else []
            values = [
                ("share_issuance", cash.share_issuance),
                ("buybacks", abs(cash.share_repurchases)),
                ("dividends", abs(cash.dividends_paid)),
                ("net_debt_issuance", cash.debt_issued - abs(cash.debt_repaid)),
                ("sbc_burden", ratio(cash.stock_based_compensation, revenue)),
                ("capital_return_coverage", ratio(cash.free_cash_flow, distributions)),
            ]
            result.extend(
                metric(
                    name,
                    "shareholder",
                    value,
                    cash.period.period_end,
                    cash.period.available_at,
                    warnings=debt_warnings,
                    units=("currency" if name in {"share_issuance", "buybacks", "dividends", "net_debt_issuance"} else "ratio"),
                )
                for name, value in values
            )

        if len(data.balance_sheets) >= 2:
            previous_balance, latest_balance = data.balance_sheets[-2:]
            result.append(
                metric(
                    "retained_earnings_trend",
                    "shareholder",
                    latest_balance.retained_earnings - previous_balance.retained_earnings,
                    latest_balance.period.period_end,
                    latest_balance.period.available_at,
                    units="currency",
                    assumptions=["positive change indicates retained earnings are building shareholder equity"],
                )
            )

        if len(data.cash_flow_statements) >= 2:
            previous_cash, latest_cash = data.cash_flow_statements[-2:]
            previous_distributions = abs(previous_cash.share_repurchases) + abs(previous_cash.dividends_paid)
            latest_distributions = abs(latest_cash.share_repurchases) + abs(latest_cash.dividends_paid)
            previous_coverage = ratio(previous_cash.free_cash_flow, previous_distributions)
            latest_coverage = ratio(latest_cash.free_cash_flow, latest_distributions)
            if previous_coverage is not None and latest_coverage is not None:
                direction = 1.0 if latest_coverage > previous_coverage else -1.0 if latest_coverage < previous_coverage else 0.0
                assessment = (
                    "improving capital discipline"
                    if direction > 0
                    else "degrading capital discipline"
                    if direction < 0
                    else "stable capital discipline"
                )
                previous_debt_funding = _debt_funded_distributions(previous_cash)
                latest_debt_funding = _debt_funded_distributions(latest_cash)
                trend_warnings = [assessment]
                if latest_debt_funding > previous_debt_funding:
                    trend_warnings.append("debt-funded distributions are increasing")
                result.append(
                    metric(
                        "capital_discipline_trend",
                        "shareholder",
                        direction,
                        latest_cash.period.period_end,
                        latest_cash.period.available_at,
                        units="direction",
                        warnings=trend_warnings,
                        assumptions=[
                            f"{assessment}: capital return coverage changed from {previous_coverage:.8f} to {latest_coverage:.8f}"
                        ],
                    )
                )
        return [x for x in result if x], []


def _debt_funded_distributions(cash: CashFlowStatement) -> float:
    """Return distributions unsupported by FCF when net debt issuance is positive."""
    net_debt_issuance = cash.debt_issued - abs(cash.debt_repaid)
    distributions = abs(cash.share_repurchases) + abs(cash.dividends_paid)
    shortfall = distributions - (cash.free_cash_flow or 0.0)
    return min(net_debt_issuance, shortfall) if net_debt_issuance > 0 and shortfall > 0 else 0.0
