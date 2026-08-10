"""Point-in-time input validation."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum

from app.domain.models.market_data import HistoricalBarsResult
from app.domain.models.portfolio import PortfolioProposal


class RiskErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_RISK_INPUT"
    FUTURE_DATA = "FUTURE_RISK_DATA"
    PROPOSAL_NOT_FOUND = "PROPOSAL_NOT_FOUND"


class RiskError(ValueError):
    def __init__(self, code: RiskErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class RiskInputValidationService:
    def validate(self, proposal: PortfolioProposal, history: list[HistoricalBarsResult]) -> None:
        if any(item.actual_end > proposal.as_of for item in history):
            raise RiskError(RiskErrorCode.FUTURE_DATA, "Historical data extends beyond the proposal as-of time")
        symbols = [str(item.symbol) for item in history]
        if len(symbols) != len(set(symbols)):
            raise RiskError(RiskErrorCode.INVALID_INPUT, "Historical symbols must be unique")
        for item in history:
            if any(bar.timestamp > proposal.as_of for bar in item.bars):
                raise RiskError(RiskErrorCode.FUTURE_DATA, "Future bars are forbidden")

    @staticmethod
    def validate_as_of(as_of: datetime, proposal: PortfolioProposal) -> None:
        if as_of != proposal.as_of:
            raise RiskError(RiskErrorCode.INVALID_INPUT, "Risk review as-of must equal proposal as-of")

    @staticmethod
    def validate_liquidity(inputs: dict[str, dict[str, float]]) -> None:
        if any(not math.isfinite(value) for observations in inputs.values() for value in observations.values()):
            raise RiskError(RiskErrorCode.INVALID_INPUT, "Liquidity inputs must be finite")
        if any(value < 0 for observations in inputs.values() for value in observations.values()):
            raise RiskError(RiskErrorCode.INVALID_INPUT, "Liquidity inputs cannot be negative")
