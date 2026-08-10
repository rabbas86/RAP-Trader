"""Configuration for the offline fundamental analyst."""

from dataclasses import dataclass
from typing import ClassVar

from app.domain.models.analyst import AnalystRole


@dataclass(frozen=True)
class FundamentalAnalystConfig:
    analyst_id: str = "fundamental"
    role: AnalystRole = AnalystRole.FUNDAMENTAL
    uncalibrated_confidence_cap: float = 0.65
    stale_input_allowed: bool = False
    base_confidence: float = 0.6
    min_quarterly_periods: int = 4
    min_annual_periods: int = 3
    ROIC_FORMULA_ASSUMPTIONS: ClassVar[str] = (
        "NOPAT = EBIT * (1 - tax_rate); invested capital = total assets - current liabilities "
        "- non-interest-bearing current liabilities + cash adjustment; accounts payable proxies "
        "non-interest-bearing current liabilities and cash adjustment is cash and equivalents."
    )

    def __post_init__(self) -> None:
        if not self.analyst_id or not 0 <= self.uncalibrated_confidence_cap <= 1 or not 0 <= self.base_confidence <= 1:
            raise ValueError("invalid fundamental analyst configuration")
        if self.min_quarterly_periods < 2 or self.min_annual_periods < 2:
            raise ValueError("minimum period counts must be at least two")
