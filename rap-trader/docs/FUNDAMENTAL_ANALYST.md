# Fundamental Analyst (Phase 7)

Phase 7 adds a deterministic, offline fundamental research analyst for equities. It
consumes point-in-time `CompanyFundamentals` supplied by the caller and produces the
shared Phase 5 `AnalystOpinion`. It never fetches data, invokes a model, creates an
order, sizes a position, allocates capital, or emits a trading instruction.

## Design principles

- **Offline first.** All inputs are caller-supplied JSON. No network, no LLM, no model
  download, no broker dependency.
- **Point-in-time.** Every financial period carries both `period_end` (the economic
  close) and `available_at` (when the filing became usable). The normalizer rejects
  any period whose `available_at > as_of`.
- **Strict models.** Pydantic v2 strict validation rejects non-finite values, naive
  timestamps, and invalid accounting relationships before any metric is computed.
- **Deterministic formulas.** Every ratio is a pure function of the input statements.
  No interpolation, no extrapolation beyond supplied periods, no fabrication.

## Input contract

```jsonc
{
  "symbol": "AAPL",
  "as_of": "2025-03-15T00:00:00+00:00",
  "income_statements": [
    {"period": {"period_end": "...", "filing_date": "...", "available_at": "...",
                 "fiscal_year": 2024, "fiscal_quarter": null, "period_type": "ANNUAL",
                 "currency": "USD", "source_name": "...", "audited": true, "restated": false},
     "revenue": 383285000000, "net_income": 93920000000, "diluted_eps": 5.67,
     "weighted_average_diluted_shares": 16525000000, ...}
  ],
  "balance_sheets": [
    {"period": {...}, "total_assets": 352100000000, "shareholders_equity": 62600000000, ...}
  ],
  "cash_flow_statements": [
    {"period": {...}, "operating_cash_flow": 110564000000, "capital_expenditure": -9360000000, ...}
  ],
  "shares_outstanding": 16525000000,
  "market_cap": 2800000000000,
  "enterprise_value": 2900000000000,
  "current_price": 170.0,
  "reporting_currency": "USD",
  "source_metadata": {"source": "manual", "explicit_forward_net_income": 95000000000},
  "warnings": []
}
```

Market-derived inputs (`market_cap`, `enterprise_value`, `current_price`) are
**optional**. Valuation metrics are generated only when their market inputs are
explicitly supplied. Forward P/E, dividend yield, and PEG additionally require
explicitly named values in `source_metadata`.

## Analysis dimensions

| Service | Category | Key metrics |
|---------|----------|-------------|
| `GrowthAnalysisService` | growth | revenue/eps/net-income/fcf/oi/gp growth YoY, CAGR |
| `ProfitabilityAnalysisService` | profitability | gross/operating/EBITDA/EBIT/net/FCF margins, ROA, ROE |
| `CapitalEfficiencyAnalysisService` | capital_efficiency | ROIC, ROCE, asset turnover, working-capital efficiency |
| `BalanceSheetAnalysisService` | balance_sheet | current/quick/cash ratios, debt/equity/assets, interest coverage |
| `CashFlowAnalysisService` | cash_flow | FCF, CFO/net income, capex intensity, dividend/buyback coverage |
| `EarningsQualityService` | earnings_quality | accrual intensity, CFO vs net income, receivables/inventory divergence, margin consistency, ratings |
| `ShareholderAnalysisService` | shareholder | issuance, buybacks, dividends, net debt issuance, retained earnings trend |
| `ValuationAnalysisService` | valuation | P/E, P/B, P/S, EV/Revenue, EV/EBITDA, EV/EBIT, FCF yield |

## ROIC formula

```
ROIC = NOPAT / Invested Capital
NOPAT = EBIT × (1 − tax_rate)
Invested Capital = Total Assets − Accounts Payable − (Non-interest-bearing current liabilities)
```

Accounts payable is used as a proxy for non-interest-bearing current liabilities.
The analyst records a limitation when the tax rate or invested-capital inputs are
incomplete and does not claim precision.

## Earnings-quality methodology

The `EarningsQualityService` evaluates quality on a four-level ordinal scale:

- **High-quality earnings** — low accruals, stable margins, no spikes or divergence.
- **Moderate-quality earnings** — one identified concern (e.g. mild CFO divergence).
- **Low-quality earnings** — two or more concerns present.
- **Insufficient evidence** — data too sparse for an assessment.

No fraud scores or fraud labels are ever produced.

## Interfaces

### CLI

```powershell
.venv/Scripts/python.exe -m app.cli.analyst `
  --analyst fundamental `
  --ticker AAPL `
  --as-of 2025-03-15T00:00:00+00:00 `
  --input-fundamentals fundamentals.json `
  --json
```

### API

```
GET  /analysts              # lists configured analysts (includes "fundamental")
GET  /analysts/fundamental/health
GET  /analysts/fundamental/metadata
POST /analysts/fundamental/analyze
```

## Safety

Every `AnalystOpinion` enforces `research_only=True`,
`suitable_for_live_trading=False`, and `decision_ready=False` via the Phase 5
model validator — these flags cannot be overridden. The analyst never creates
`OrderRequest`, calculates position quantities, allocates capital, or invokes
RiskEngine, PortfolioManager, InvestmentCommittee, Chairman, or any broker/execution
service.
