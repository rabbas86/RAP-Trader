"""Point-in-time validation for normalized financial statements."""

from datetime import datetime

from app.domain.models.analyst import AnalystError, AnalystErrorCodes
from app.services.fundamental_analysis.normalization import NormalizedFinancialStatements


class FundamentalAnalysisValidationService:
    def validate(self, data: NormalizedFinancialStatements, as_of: datetime) -> None:
        available_times = [x.period.available_at for x in data.income_statements]
        available_times.extend(x.period.available_at for x in data.balance_sheets)
        available_times.extend(x.period.available_at for x in data.cash_flow_statements)
        if any(value > as_of for value in available_times):
            raise AnalystError(AnalystErrorCodes.LOOKAHEAD_REJECTED, "Future financial data is forbidden")
        if any(x.total_assets < 0 and x.current_assets > 0 for x in data.balance_sheets):
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Financial statements contain an impossible accounting relationship")
        if not available_times:
            raise AnalystError(AnalystErrorCodes.INSUFFICIENT_DATA, "No financial statements were supplied")
