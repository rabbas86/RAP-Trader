# RAP Trader

RAP Trader is a modular foundation for an AI-assisted US-equities paper-trading platform. Phase 2 adds validated, read-only historical market data to the safe Phase 1 foundation. Live trading remains disabled, no real-broker adapter or order API exists, and paper orders/cache entries remain process-local.

The default market-data provider is deterministic, synthetic, and offline. The isolated yfinance adapter is opt-in and uses no paid service.

## Install and run locally

Python 3.12 or newer is required.

```shell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

## Market data

Read-only endpoints are `GET /market-data/health`, `GET /market-data/timeframes`, and `GET /market-data/bars`. Bar queries accept a symbol, timeframe, timezone-aware start/end, optional limit, adjustment, and session. Mock symbols are AAPL, MSFT, GOOG, TSLA, SPY, BRK.B, and BF.B; timeframes are 1m, 5m, 15m, 1h, 1d, and 1w. Mock output is synthetic and not exchange-calendar accurate. Limits and date-range policies bound generation.

Adjustment policies are `raw` (reported OHLC), `split_adjusted` (split-adjusted OHLC), and `total_return_adjusted` (splits plus distributions; currently rejected). Session policies are `regular`, `extended`, and `all`; `regular` is the default. All accepted timestamps and provenance times are normalized to UTC. yfinance translates class-share symbols such as BRK.B to BRK-B.

## Test and check

```shell
pytest -v
ruff check .
ruff format --check .
mypy app --strict
```

## Structure

- `app/api`: HTTP routes
- `app/domain/models`: validated contracts
- `app/services`: integration boundaries and deterministic business services
- `tests`: unit and API tests
- `docs`: API, architecture, roadmap, and safety notes

## Safety limitations

Predictions and decisions are placeholders, not investment advice. Market data may be delayed, incomplete, synthetic, or adjusted and is never an execution quote. Deterministic risk controls remain mandatory and no decision model can override them. See `docs/SAFETY.md`.
