# RAP Trader — Codex Task 001

## Objective

Create Phase 1 of RAP Trader: a safe, modular foundation for an AI-assisted US equities trading platform.

This phase must not place real trades and must not contain autonomous live-trading logic.

## Current Phase Scope

Build the initial project skeleton with:

1. FastAPI application
2. Health endpoint
3. Configuration management
4. Structured logging
5. Initial service modules
6. Pydantic domain schemas
7. Unit tests
8. Docker support
9. Documentation
10. Paper-trading-only safety controls

## Required Project Structure

rap-trader/
    app/
        __init__.py
        main.py
        config.py
        logging_config.py

        api/
            __init__.py
            routes/
                __init__.py
                health.py
                system.py

        domain/
            __init__.py
            models/
                __init__.py
                decision.py
                prediction.py
                risk.py
                order.py
                portfolio.py

        services/
            __init__.py
            market_data/
                __init__.py
                service.py
            kronos/
                __init__.py
                service.py
            technical_analysis/
                __init__.py
                service.py
            fundamental_analysis/
                __init__.py
                service.py
            news_analysis/
                __init__.py
                service.py
            decision_engine/
                __init__.py
                service.py
            risk_engine/
                __init__.py
                service.py
            portfolio/
                __init__.py
                service.py
            broker/
                __init__.py
                base.py
                paper.py
            execution/
                __init__.py
                service.py

    tests/
        __init__.py
        test_health.py
        test_risk_engine.py
        test_paper_broker.py

    docs/
        ARCHITECTURE.md
        ROADMAP.md
        SAFETY.md
        API.md

    .env.example
    .gitignore
    Dockerfile
    docker-compose.yml
    pyproject.toml
    README.md

## Functional Requirements

### FastAPI

Create a FastAPI application with:

GET /health

Response:

{
  "status": "healthy",
  "service": "rap-trader",
  "trading_mode": "paper"
}

GET /system/status

Response should include:

- application name
- environment
- trading mode
- live trading enabled status
- version

### Configuration

Use pydantic-settings.

Required configuration:

- APP_NAME
- APP_ENV
- LOG_LEVEL
- TRADING_MODE
- LIVE_TRADING_ENABLED
- MAX_POSITION_PERCENT
- MAX_DAILY_LOSS_PERCENT
- MAX_PORTFOLIO_DRAWDOWN_PERCENT

Safe defaults:

- TRADING_MODE=paper
- LIVE_TRADING_ENABLED=false
- MAX_POSITION_PERCENT=5
- MAX_DAILY_LOSS_PERCENT=2
- MAX_PORTFOLIO_DRAWDOWN_PERCENT=10

The application must reject startup if:

- TRADING_MODE is live and LIVE_TRADING_ENABLED is false
- risk limits are zero, negative, or outside reasonable ranges

### Domain Schemas

Create typed Pydantic models for:

KronosPrediction:
- ticker
- direction
- confidence
- expected_return
- time_horizon
- generated_at
- model_version

AgentEvidence:
- source
- ticker
- recommendation
- confidence
- reasoning_summary
- generated_at

TradeDecision:
- decision_id
- ticker
- action
- confidence
- quantity
- order_type
- limit_price
- stop_loss
- take_profit
- rationale
- evidence
- created_at

RiskAssessment:
- approved
- rejection_reasons
- maximum_allowed_quantity
- estimated_position_percent
- estimated_daily_loss_percent

OrderRequest:
- ticker
- side
- quantity
- order_type
- limit_price
- idempotency_key

OrderResult:
- order_id
- status
- broker
- paper_trade
- message
- created_at

### Risk Engine

Implement deterministic validation only.

It must reject:

- zero or negative quantity
- unsupported order actions
- position sizes above MAX_POSITION_PERCENT
- trades exceeding MAX_DAILY_LOSS_PERCENT
- trades when portfolio drawdown exceeds the configured maximum
- live orders while live trading is disabled

The AI decision engine must never override the risk engine.

### Paper Broker

Implement an in-memory paper broker.

It should:

- submit simulated orders
- generate unique order IDs
- retain submitted orders during the process lifetime
- reject duplicate idempotency keys
- never contact a real broker
- clearly mark every result as paper_trade=true

### Kronos Service

Create only an interface and mock implementation.

Do not install or download a Kronos model yet.

The mock response must be deterministic and clearly marked:

- model_version="mock-kronos-v0"
- not suitable for live trading

### Decision Engine

Create an interface that accepts:

- Kronos prediction
- technical evidence
- fundamental evidence
- news evidence
- portfolio context

For Phase 1, return a deterministic WAIT decision.

Do not call Claude, OpenAI, Ollama, or another external model yet.

### Logging

Use structured logging.

Log:

- request ID
- decision ID
- order ID
- service
- event
- result

Do not log secrets or API keys.

## Testing Requirements

Use pytest.

Tests must verify:

1. /health returns HTTP 200
2. trading mode defaults to paper
3. live execution is rejected
4. excessive position size is rejected
5. valid paper order is accepted
6. duplicate idempotency key is rejected
7. Kronos mock output is clearly identified as mock
8. decision engine defaults to WAIT

Run all tests before completion.

## Docker Requirements

Create:

- Dockerfile
- docker-compose.yml

The API should be exposed on port 8000.

The container must run without broker credentials or external APIs.

## Documentation

README.md must explain:

- what RAP Trader is
- current Phase 1 capabilities
- that it is paper-trading only
- installation
- running locally
- running with Docker
- testing
- project structure
- safety limitations

ARCHITECTURE.md must document:

- service boundaries
- planned Kronos integration
- planned AI investment committee
- risk-before-execution design
- future broker adapter approach
- audit trail requirements

ROADMAP.md should define:

- Phase 1: foundation
- Phase 2: market data
- Phase 3: Kronos integration
- Phase 4: backtesting
- Phase 5: AI evidence fusion
- Phase 6: paper broker integration
- Phase 7: monitored live-trading readiness

SAFETY.md must state:

- live trading is disabled
- no LLM may bypass risk controls
- no secret may be committed
- paper trading and backtesting are mandatory
- strategy or prompt changes require validation

## Engineering Standards

- Python 3.12+
- Full type hints
- Clear module boundaries
- No unnecessary microservice deployment in Phase 1
- Modular monolith first
- Dependency injection where useful
- No hardcoded secrets
- No real broker integration
- No paid market-data integration
- No external AI calls
- No persistent server left running after validation

## Completion Criteria

Before reporting completion:

1. Create all required files.
2. Install or resolve dependencies as appropriate.
3. Run formatting or lint checks if configured.
4. Run pytest.
5. Fix any failing tests.
6. Show a concise summary of files created.
7. Show test results.
8. Identify any assumptions or remaining limitations.
9. Do not start a persistent web server.
10. Do not place any trade.
