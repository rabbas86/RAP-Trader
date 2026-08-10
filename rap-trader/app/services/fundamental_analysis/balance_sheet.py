"""Balance sheet strength: liquidity, leverage, and solvency ratios."""

from __future__ import annotations

from app.domain.models.analyst import EvidenceItem
from app.domain.models.fundamental import FundamentalMetric
from app.services.fundamental_analysis.common import metric
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class BalanceSheetAnalysisService:
    def analyze(self, data: NormalizedFinancialStatements) -> tuple[list[FundamentalMetric], list[EvidenceItem]]:
        metrics: list[FundamentalMetric] = []
        if not data.balance_sheets:
            return metrics, []

        bs = data.balance_sheets[-1]
        warnings: list[str] = []

        if bs.shareholders_equity < 0:
            warnings.append("negative shareholders' equity")
        if bs.total_liabilities > bs.total_assets:
            warnings.append("liabilities exceed assets")

        # Liquidity ratios
        if bs.current_liabilities > 0:
            current_ratio = bs.current_assets / bs.current_liabilities
            if m := metric(
                "current_ratio", "balance_sheet", current_ratio, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
            ):
                metrics.append(m)
        else:
            warnings.append("zero current liabilities; liquidity ratios suppressed")

        liquid_assets = bs.cash_and_equivalents + bs.short_term_investments + bs.accounts_receivable
        if bs.current_liabilities > 0:
            quick_ratio = liquid_assets / bs.current_liabilities
            if m := metric(
                "quick_ratio", "balance_sheet", quick_ratio, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
            ):
                metrics.append(m)
            cash_ratio = bs.cash_and_equivalents / bs.current_liabilities
            if m := metric(
                "cash_ratio", "balance_sheet", cash_ratio, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
            ):
                metrics.append(m)

        # Leverage ratios
        if bs.shareholders_equity > 0:
            debt_to_equity = bs.total_debt / bs.shareholders_equity
            if m := metric(
                "debt_to_equity", "balance_sheet", debt_to_equity, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
            ):
                metrics.append(m)
        else:
            warnings.append("zero or negative shareholders' equity; debt/equity suppressed")

        if bs.total_assets > 0:
            debt_to_assets = bs.total_debt / bs.total_assets
            if m := metric(
                "debt_to_assets", "balance_sheet", debt_to_assets, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
            ):
                metrics.append(m)

        # Net debt
        net_debt = bs.total_debt - bs.cash_and_equivalents
        if m := metric(
            "net_debt", "balance_sheet", net_debt, bs.period.period_end, bs.period.available_at, units="currency", warnings=warnings.copy()
        ):
            metrics.append(m)

        # Net debt / EBITDA
        if net_debt >= 0 and len(data.income_statements) > 0:
            income = data.income_statements[-1]
            if income.ebitda and income.ebitda > 0:
                ndebitda = net_debt / income.ebitda
                if m := metric(
                    "net_debt_to_ebitda", "balance_sheet", ndebitda, bs.period.period_end, bs.period.available_at, warnings=warnings.copy()
                ):
                    metrics.append(m)

        # Intangible asset concentration
        if bs.total_assets > 0:
            intangibles = bs.goodwill + bs.intangible_assets
            concentration = intangibles / bs.total_assets
            if m := metric(
                "goodwill_intangibles_concentration",
                "balance_sheet",
                concentration,
                bs.period.period_end,
                bs.period.available_at,
                warnings=warnings.copy(),
            ):
                metrics.append(m)

        # Working capital
        working_capital = bs.current_assets - bs.current_liabilities
        if m := metric(
            "working_capital",
            "balance_sheet",
            working_capital,
            bs.period.period_end,
            bs.period.available_at,
            units="currency",
            warnings=warnings.copy(),
        ):
            metrics.append(m)

        # Working capital trend
        if len(data.balance_sheets) >= 2:
            prev = data.balance_sheets[-2]
            prev_wc = prev.current_assets - prev.current_liabilities
            wc_trend = working_capital - prev_wc
            if m := metric(
                "working_capital_trend",
                "balance_sheet",
                wc_trend,
                bs.period.period_end,
                bs.period.available_at,
                units="currency",
                warnings=warnings.copy(),
            ):
                metrics.append(m)

        return metrics, []
