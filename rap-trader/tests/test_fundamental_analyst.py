"""Focused Phase 7 contract, normalization, evidence, and safety tests."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.models.analyst import AnalystRequest
from app.domain.models.fundamental import (
    BalanceSheet,
    CashFlowStatement,
    CompanyFundamentals,
    FinancialStatementPeriod,
    IncomeStatement,
    PeriodType,
)
from app.services.fundamental_analysis import FundamentalAnalyst
from app.services.fundamental_analysis.normalization import FinancialDataNormalizationService


def period(year: int, *, restated: bool = False, available_days: int = 60) -> FinancialStatementPeriod:
    end = datetime(year, 12, 31, tzinfo=UTC)
    available = end + timedelta(days=available_days)
    return FinancialStatementPeriod(
        period_end=end,
        filing_date=available,
        fiscal_year=year,
        fiscal_quarter=None,
        period_type=PeriodType.ANNUAL,
        currency="USD",
        source_name="fixture",
        source_reference=None,
        audited=True,
        restated=restated,
        available_at=available,
    )


def fundamentals(*, duplicate: bool = False) -> CompanyFundamentals:
    incomes, balances, cashflows = [], [], []
    for offset, year in enumerate((2022, 2023, 2024)):
        p = period(year)
        revenue = 1000.0 + offset * 150
        incomes.append(
            IncomeStatement(
                period=p,
                revenue=revenue,
                cost_of_revenue=600.0 + offset * 70,
                gross_profit=None,
                operating_expense=200.0,
                operating_income=200.0 + offset * 80,
                ebitda=250.0 + offset * 80,
                ebit=220.0 + offset * 80,
                interest_expense=20.0,
                pretax_income=180.0 + offset * 80,
                tax_expense=36.0 + offset * 16,
                net_income=144.0 + offset * 64,
                diluted_eps=1.44 + offset * 0.5,
                weighted_average_diluted_shares=100.0 + offset,
            )
        )
        balances.append(
            BalanceSheet(
                period=p,
                cash_and_equivalents=200.0,
                short_term_investments=50.0,
                current_assets=600.0,
                total_assets=1800.0 + offset * 100,
                inventory=80.0,
                accounts_receivable=120.0,
                accounts_payable=100.0,
                current_liabilities=300.0,
                total_liabilities=800.0,
                short_term_debt=50.0,
                long_term_debt=250.0,
                total_debt=300.0,
                shareholders_equity=1000.0 + offset * 100,
                retained_earnings=500.0 + offset * 100,
                goodwill=50.0,
                intangible_assets=30.0,
            )
        )
        cashflows.append(
            CashFlowStatement(
                period=p,
                operating_cash_flow=220.0 + offset * 80,
                capital_expenditure=-60.0,
                free_cash_flow=None,
                investing_cash_flow=-80.0,
                financing_cash_flow=-100.0,
                share_repurchases=-50.0,
                share_issuance=5.0,
                dividends_paid=-30.0,
                debt_issued=20.0,
                debt_repaid=-40.0,
                stock_based_compensation=15.0,
            )
        )
    if duplicate:
        incomes.append(incomes[-1].model_copy(update={"period": period(2024, restated=True, available_days=70), "revenue": 1310.0}))
    return CompanyFundamentals(
        symbol="TEST",
        as_of=datetime(2025, 3, 15, tzinfo=UTC),
        income_statements=incomes,
        balance_sheets=balances,
        cash_flow_statements=cashflows,
        shares_outstanding=102.0,
        market_cap=3000.0,
        enterprise_value=3050.0,
        current_price=29.0,
        sector="Testing",
        industry="Fixtures",
        reporting_currency="USD",
        source_metadata={"source": "offline fixture"},
        warnings=[],
    )


def test_naive_and_nonfinite_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialStatementPeriod.model_validate(
            period(2024).model_dump() | {"available_at": datetime(2025, 1, 1)}  # noqa: DTZ001 - deliberately invalid input
        )
    payload = fundamentals().model_dump()
    payload["shares_outstanding"] = float("inf")
    with pytest.raises(ValidationError):
        CompanyFundamentals.model_validate(payload)


def test_normalization_derives_fields_and_selects_restatement() -> None:
    data = FinancialDataNormalizationService().normalize(fundamentals(duplicate=True), datetime(2025, 3, 15, tzinfo=UTC))
    assert data.income_statements[-1].revenue == 1310.0
    assert data.income_statements[-1].gross_profit == 570.0
    assert data.cash_flow_statements[-1].free_cash_flow == 320.0
    assert any("duplicate" in warning for warning in data.warnings)
    assert any("restatement" in warning for warning in data.warnings)


def test_lookahead_is_rejected() -> None:
    with pytest.raises(Exception, match="unavailable"):
        FinancialDataNormalizationService().normalize(fundamentals(), datetime(2024, 12, 31, tzinfo=UTC))


def test_analyst_is_deterministic_research_only() -> None:
    value = fundamentals()
    as_of = datetime(2025, 3, 15, tzinfo=UTC)
    request = AnalystRequest(
        analyst_id="fundamental",
        ticker="TEST",
        timeframe="1d",
        as_of=as_of,
        lookback=3,
        horizon=1,
        asset_class="equity",
        extra_context={"fundamentals": value.model_dump(mode="json")},
    )
    first = FundamentalAnalyst().analyze(request)
    second = FundamentalAnalyst().analyze(request)
    assert first.model_dump() == second.model_dump()
    assert first.research_only is True
    assert first.suitable_for_live_trading is False
    assert first.decision_ready is False
    serialized = first.model_dump_json().lower()
    assert '"buy"' not in serialized and '"sell"' not in serialized
    assert len({item.evidence_id for item in first.evidence}) == len(first.evidence)


def test_source_has_no_forbidden_integrations() -> None:
    root = Path(__file__).parents[1] / "app" / "services" / "fundamental_analysis"
    imports: set[str] = set()
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imports.update(alias.name.lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.lower())
    source = "\n".join(imports)
    for forbidden in (
        "risk_engine",
        "portfoliomanager",
        "broker",
        "executionservice",
        "committee",
        "chairman",
        "httpx",
        "requests",
        "torch",
    ):
        assert forbidden not in source
