# RAP Trader

RAP Trader is a modular foundation for an AI-assisted US-equities trading platform. Phase 1 is deliberately paper-trading only: it provides a FastAPI API, validated settings, structured JSON logging, typed domain schemas, deterministic risk controls, a mock Kronos boundary, a WAIT-only decision engine, and an in-memory paper broker.

It cannot place real trades, has no external AI or market-data calls, and stores paper orders only for the process lifetime.

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

Phase 1 predictions and decisions are placeholders, not investment advice. Live trading is disabled by default. `ExecutionService` submits an order only when it receives deterministic risk approval; no decision model may override that gate. Phase 1 exposes no order-submission API and includes no real-broker adapter. Paper trading and backtesting are required before any future live-readiness assessment. See `docs/SAFETY.md`.
