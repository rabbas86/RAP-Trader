"""Shareholder capital-allocation analysis."""

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric, ratio
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class ShareholderAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        if not data.cash_flow_statements:
            return [], []
        cash = data.cash_flow_statements[-1]
        revenue = data.income_statements[-1].revenue if data.income_statements else None
        distributions = abs(cash.share_repurchases) + abs(cash.dividends_paid)
        values = [
            ("share_issuance", cash.share_issuance),
            ("buybacks", abs(cash.share_repurchases)),
            ("dividends", abs(cash.dividends_paid)),
            ("net_debt_issuance", cash.debt_issued - abs(cash.debt_repaid)),
            ("sbc_burden", ratio(cash.stock_based_compensation, revenue)),
            ("capital_return_coverage", ratio(cash.free_cash_flow, distributions)),
        ]
        warnings = (
            ["debt-funded distributions may weaken capital discipline"]
            if cash.debt_issued > abs(cash.debt_repaid) and (cash.free_cash_flow or 0) < distributions
            else []
        )
        result = [
            metric(
                name,
                "shareholder",
                value,
                cash.period.period_end,
                cash.period.available_at,
                warnings=warnings,
                units="currency" if name in {"share_issuance", "buybacks", "dividends", "net_debt_issuance"} else "ratio",
            )
            for name, value in values
        ]
        return [x for x in result if x], []
