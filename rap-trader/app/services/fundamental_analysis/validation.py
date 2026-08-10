"""Validation of fundamental analysis inputs and outputs."""

from __future__ import annotations

from datetime import datetime

from app.domain.models.analyst import AnalystError, AnalystErrorCodes
from app.domain.models.fundamental import CompanyFundamentals
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class FundamentalAnalysisValidationService:
    def validate_inputs(self, fundamentals: CompanyFundamentals, as_of: datetime) -> None:
        """Validate raw inputs against lookahead and accounting constraints."""
        if not fundamentals.income_statements:
            raise AnalystError(
                AnalystErrorCodes.INSUFFICIENT_DATA,
                "At least one income statement is required",
            )
        if not fundamentals.balance_sheets:
            raise AnalystError(
                AnalystErrorCodes.INSUFFICIENT_DATA,
                "At least one balance sheet is required",
            )
        if not fundamentals.cash_flow_statements:
            raise AnalystError(
                AnalystErrorCodes.INSUFFICIENT_DATA,
                "At least one cash flow statement is required",
            )
        for stmt_group in (
            fundamentals.income_statements,
            fundamentals.balance_sheets,
            fundamentals.cash_flow_statements,
        ):
            for stmt in stmt_group:
                if stmt.period.available_at > as_of:
                    raise AnalystError(
                        AnalystErrorCodes.LOOKAHEAD_REJECTED,
                        "A filing was unavailable at the requested as-of time",
                    )
                if stmt.period.period_end > stmt.period.available_at:
                    raise AnalystError(
                        AnalystErrorCodes.LOOKAHEAD_REJECTED,
                        "period_end cannot be after available_at",
                    )

    def validate_normalized(self, data: NormalizedFinancialStatements) -> None:
        """Post-normalization consistency checks (currently informational)."""
        # All critical normalization checks are performed during normalize();
        # this hook is reserved for future cross-statement invariants.

    def validate(self, data: NormalizedFinancialStatements, as_of: datetime) -> None:
        """Full validation pass over normalized data."""
        self.validate_normalized(data)
        # Double-check that no accepted statement has available_at > as_of
        all_periods = data.annual_periods + data.quarterly_periods + data.ttm_periods
        for period in all_periods:
            if period.period.available_at > as_of:
                raise AnalystError(
                    AnalystErrorCodes.LOOKAHEAD_REJECTED,
                    "Normalized evidence must be available before as_of",
                )
