"""Strict contracts for deterministic, research-only fundamental analysis."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models.analyst import DataFreshness, EvidenceItem
from app.domain.models.market_data import UtcDatetime, _require_aware_utc


class PeriodType(StrEnum):
    ANNUAL = "ANNUAL"
    QUARTERLY = "QUARTERLY"
    TTM = "TTM"


class PeriodTypeStr(StrEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    TTM = "trailing_twelve_months"


class _Model(BaseModel):
    model_config = ConfigDict(strict=True)


class FinancialStatementPeriod(_Model):
    period_end: UtcDatetime
    filing_date: UtcDatetime
    fiscal_year: int = Field(gt=0)
    fiscal_quarter: int | None = Field(default=None, ge=1, le=4)
    period_type: PeriodType
    currency: str = Field(min_length=3, max_length=3)
    source_name: str = Field(min_length=1)
    source_reference: str | None = None
    audited: bool
    restated: bool
    available_at: UtcDatetime

    @field_validator("period_end", "filing_date", "available_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def chronology(self) -> FinancialStatementPeriod:
        if self.period_end > self.available_at:
            raise ValueError("period_end cannot be after available_at")
        if self.filing_date > self.available_at:
            raise ValueError("filing_date cannot be after available_at")
        if self.period_type is PeriodType.QUARTERLY and self.fiscal_quarter is None:
            raise ValueError("quarterly periods require fiscal_quarter")
        return self


Money = float


class IncomeStatement(_Model):
    period: FinancialStatementPeriod
    revenue: Money = Field(allow_inf_nan=False)
    cost_of_revenue: Money = Field(allow_inf_nan=False)
    gross_profit: Money | None = Field(default=None, allow_inf_nan=False)
    operating_expense: Money = Field(allow_inf_nan=False)
    operating_income: Money = Field(allow_inf_nan=False)
    ebitda: Money | None = Field(default=None, allow_inf_nan=False)
    ebit: Money | None = Field(default=None, allow_inf_nan=False)
    interest_expense: Money = Field(allow_inf_nan=False)
    pretax_income: Money = Field(allow_inf_nan=False)
    tax_expense: Money = Field(allow_inf_nan=False)
    net_income: Money = Field(allow_inf_nan=False)
    diluted_eps: float = Field(allow_inf_nan=False)
    weighted_average_diluted_shares: float = Field(allow_inf_nan=False)


class BalanceSheet(_Model):
    period: FinancialStatementPeriod
    cash_and_equivalents: Money = Field(allow_inf_nan=False)
    short_term_investments: Money = Field(allow_inf_nan=False)
    current_assets: Money = Field(allow_inf_nan=False)
    total_assets: Money = Field(allow_inf_nan=False)
    inventory: Money = Field(allow_inf_nan=False)
    accounts_receivable: Money = Field(allow_inf_nan=False)
    accounts_payable: Money = Field(allow_inf_nan=False)
    current_liabilities: Money = Field(allow_inf_nan=False)
    total_liabilities: Money = Field(allow_inf_nan=False)
    short_term_debt: Money = Field(allow_inf_nan=False)
    long_term_debt: Money = Field(allow_inf_nan=False)
    total_debt: Money = Field(allow_inf_nan=False)
    shareholders_equity: Money = Field(allow_inf_nan=False)
    retained_earnings: Money = Field(allow_inf_nan=False)
    goodwill: Money = Field(allow_inf_nan=False)
    intangible_assets: Money = Field(allow_inf_nan=False)


class CashFlowStatement(_Model):
    period: FinancialStatementPeriod
    operating_cash_flow: Money = Field(allow_inf_nan=False)
    capital_expenditure: Money = Field(allow_inf_nan=False)
    free_cash_flow: Money | None = Field(default=None, allow_inf_nan=False)
    investing_cash_flow: Money = Field(allow_inf_nan=False)
    financing_cash_flow: Money = Field(allow_inf_nan=False)
    share_repurchases: Money = Field(allow_inf_nan=False)
    share_issuance: Money = Field(allow_inf_nan=False)
    dividends_paid: Money = Field(allow_inf_nan=False)
    debt_issued: Money = Field(allow_inf_nan=False)
    debt_repaid: Money = Field(allow_inf_nan=False)
    stock_based_compensation: Money = Field(allow_inf_nan=False)


class CompanyFundamentals(_Model):
    symbol: str = Field(min_length=1)
    as_of: UtcDatetime
    income_statements: list[IncomeStatement]
    balance_sheets: list[BalanceSheet]
    cash_flow_statements: list[CashFlowStatement]
    shares_outstanding: float = Field(gt=0, allow_inf_nan=False)
    market_cap: float | None = Field(default=None, allow_inf_nan=False)
    enterprise_value: float | None = Field(default=None, allow_inf_nan=False)
    current_price: float | None = Field(default=None, allow_inf_nan=False)
    sector: str | None = None
    industry: str | None = None
    reporting_currency: str = Field(min_length=3, max_length=3)
    source_metadata: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class FundamentalMetric(_Model):
    metric_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    units: str = Field(min_length=1)
    period_end: UtcDatetime | None = None
    available_at: UtcDatetime
    source_fingerprint: str = Field(min_length=1)
    formula_version: str = Field(min_length=1)
    valid: bool
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("period_end", "available_at")
    @classmethod
    def timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value)


class FundamentalSnapshot(_Model):
    symbol: str = Field(min_length=1)
    as_of: UtcDatetime
    periods_analyzed: int = Field(gt=0)
    metrics: list[FundamentalMetric]
    evidence: list[EvidenceItem]
    warnings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    data_freshness: DataFreshness
    research_only: bool = True
    suitable_for_live_trading: bool = False

    @field_validator("as_of")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)

    @model_validator(mode="after")
    def safety(self) -> FundamentalSnapshot:
        if not self.research_only or self.suitable_for_live_trading:
            raise ValueError("fundamental snapshots are research-only")
        return self
