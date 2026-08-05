# RAP Trader

RAP Trader is a modular foundation for an AI-assisted US-equities trading platform. Phase 2 adds validated, read-only historical market data to the deliberately safe Phase 1 foundation. It provides a deterministic mock provider, an isolated free yfinance adapter, bounded TTL caching, a FastAPI API, structured JSON logging, deterministic risk controls, a mock Kronos boundary, a WAIT-only decision engine, and an in-memory paper broker.

It cannot place real trades and stores paper orders and market-data cache entries only for the process lifetime. The default market-data provider is deterministic and makes no network calls; yfinance is opt-in at the service boundary and requires no API key.

## Install and run locally

Python 3.12 or newer is required.

```shell
cd rap-trader
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Open `http://localhost:8000/health`. Copy `.env.example` to `.env` to customize safe settings.

## Market data

The read-only endpoints are `GET /market-data/health`, `GET /market-data/timeframes`, and `GET /market-data/bars`. Bars accept `symbol`, `timeframe`, `start`, `end`, and optional `limit` query parameters. Supported mock symbols are AAPL, MSFT, GOOG, TSLA, and SPY; supported timeframes are 1m, 5m, 15m, 1h, 1d, and 1w. All returned timestamps are UTC.

## Docker

```shell
docker compose up --build
```

The API is exposed on port 8000 and needs no credentials.

## Test and check

```shell
pytest
ruff check .
ruff format --check .
mypy app
```

## Structure

- `app/api`: HTTP routes
- `app/domain/models`: validated schemas
- `app/services`: integration boundaries and deterministic business services
- `tests`: unit/API tests
- `docs`: architecture, roadmap, safety, and API notes

## Safety limitations

Predictions and decisions are placeholders, not investment advice. Live trading is disabled by default. `ExecutionService` submits an order only when it receives deterministic risk approval; no decision model may override that gate. The application exposes no order-submission API and includes no real-broker adapter. Market data can be delayed, incomplete, or adjusted by its public source and must not be treated as an execution quote. See `docs/SAFETY.md`.
