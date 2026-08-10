# RAP Trader

Phase 7.5 consolidates Mock, Technical, and Fundamental analysts on one deterministic, research-only lifecycle and trace framework. Phase 7 adds the `fundamental` analyst for caller-supplied point-in-time financial statements. Phase 6 provides the `technical` analyst, consuming features from the Phase 6.5 Market Intelligence Feature Platform (MIFP). See [Analyst Platform](docs/ANALYST_PLATFORM.md), [Fundamental Analyst](docs/FUNDAMENTAL_ANALYST.md), [Technical Analyst](docs/TECHNICAL_ANALYST.md), and [Feature Platform](docs/FEATURE_PLATFORM.md).

Phase 3 adds deterministic offline Kronos forecasting over the Phase 2 market-data boundary. Live trading remains disabled, no real-broker adapter or order API exists, and forecasts are not investment advice.

RAP Trader is a modular foundation for an AI-assisted US-equities paper-trading platform. Phase 3 adds deterministic offline Kronos forecasting on top of the Phase 1 safety foundation and Phase 2 validated market data. Live trading remains disabled, no real-broker adapter or order API exists, and paper orders/cache entries remain process-local.

The default market-data provider is deterministic, synthetic, and offline. The isolated yfinance adapter is opt-in and uses no paid service. The default Kronos service is an offline simple-moving-average (SMA) crossover model that consumes normalized historical bars; it is deterministic, offline, and not suitable for live trading.

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

## Feature platform (Phase 6.5)

The Market Intelligence Feature Platform (MIFP) is the canonical, deterministic source of engineered technical features. `FeatureService` consumes normalized bars from the Phase 2 `MarketDataProvider`, runs registered feature generators in topological (dependency) order, and produces an immutable `FeatureSnapshot` with full provenance. See [Feature Platform docs](docs/FEATURE_PLATFORM.md).

Read-only endpoints are `GET /features/health`, `GET /features/categories`, and `POST /features/snapshot`. Snapshots carry `schema_version`, `platform_version`, `bars_analyzed`, per-feature provenance, `available_at`, `generated_at`, and `source_fingerprint`. No future feature may enter a historical snapshot.

## Analyst framework (Phase 5)

Phase 5 provides strict, research-only analyst opinions through `GET /analysts`, analyst health and metadata routes, `POST /analysts/{analyst_id}/analyze`, descriptive aggregation, and stored-opinion retrieval. The default analyst is deterministic and offline. Analysts cannot create trades; all outputs are unsuitable for live trading and not decision-ready. Confidence is not certainty.

The shared framework remains descriptive and research-only. Technical analysis is implemented in Phase 6 and point-in-time fundamental analysis in Phase 7; later decision roles remain outside this phase. See [the analyst framework](docs/ANALYST_FRAMEWORK.md).

## Backtesting (Phase 4)

Read-only endpoints are `POST /backtests/run`, `GET /backtests/providers`, `GET /backtests/{id}`, and `GET /backtests/{id}/summary`. Backtesting is **research-only**: it does not submit orders, execute trades, or invoke any broker, execution, risk, or portfolio service. Every result carries `research_only=True` and `suitable_for_live_trading=False`.

The backtest runner performs deterministic walk-forward evaluation: it splits historical data into non-overlapping evaluation windows, each with a context period (history available to the forecast provider) and a target period (future bars compared against the forecast). The engine enforces hard no-lookahead runtime guards — forecast timestamps cannot appear in the context, must match expected target timestamps exactly, and no bar beyond `context_end` is ever returned by the market-data provider.

Four deterministic benchmark providers are available by default: MockKronosProvider, SMAForecastProvider, LastValueForecastProvider, and DriftForecastProvider. The CLI (`python -m app.cli.backtest`) runs a bounded offline mock backtest with no server, no network, and no model download. See `docs/BACKTESTING.md` and `docs/phases/PHASE_04_BACKTESTING.md`.

## Kronos offline forecasting

Read-only endpoints are `GET /kronos/health` and `GET /kronos/prediction`. The prediction endpoint accepts `ticker`, `timeframe`, timezone-aware ISO 8601 `start`, `end`, and an optional `limit`. It returns a `KronosPrediction` with direction (UP/DOWN/FLAT), confidence (0-1), expected_return, time_horizon, generated_at, model_version, and bar provenance (timeframe, source_provider, data_start, data_end).

The default `OfflineKronosService` fetches historical bars from the configured `MarketDataProvider` (mock by default) and applies a deterministic SMA crossover strategy: a 5-period short SMA versus a 20-period long SMA. The prediction is UP when the short SMA exceeds the long SMA, DOWN when below, and FLAT when equal or insufficient data. All computations are offline and deterministic for identical inputs.

Predictions are advisory only and are not investment advice. The `WaitDecisionEngine` remains the default decision path and always produces WAIT. Risk controls are unchanged.

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

Predictions and decisions are placeholders, not investment advice. Market data may be delayed, incomplete, synthetic, or adjusted and is never an execution quote. Kronos predictions are deterministic offline signals and are not suitable for live trading. Deterministic risk controls remain mandatory and no decision model can override them. The Market Intelligence Feature Platform and Technical Analyst are research-only with no broker, execution, or live-trading integration; all opinions carry `research_only=True`, `suitable_for_live_trading=False`, and `decision_ready=False`. See `docs/SAFETY.md`.

## Data platform (Phase 8A)

The Unified Research Data Platform is a deterministic, offline, read-only data layer
that normalizes, versions, and serves research data from market, fundamental, macro,
calendar, and news/event domains behind a single point-in-time-safe contract. All data
platform outputs carry `research_only=True` and `suitable_for_live_trading=False`.

Read-only endpoints are `GET /data-platform/health`, `GET /data-platform/sources`,
`GET /data-platform/domains`, `GET /data-platform/series`,
`GET /data-platform/calendar`, and `POST /data-platform/snapshot`. The CLI is
`python -m app.cli.data_platform`. See [Data Platform docs](docs/DATA_PLATFORM.md) and
[Phase 8A plan](docs/phases/PHASE_08A_UNIFIED_DATA_PLATFORM.md).

## Macro Economist (Phase 8B)

The Macro Economist is a deterministic, offline, research-only specialist analyst that
classifies the macro-economic regime from a `ResearchDataSnapshot`. It runs 8 deterministic
domain services (inflation, growth, employment, liquidity, monetary policy, yield curve,
credit, business cycle), fuses them into a `MacroRegime`, and synthesizes an `AnalysisDirection`.
All outputs carry `research_only=true`, `suitable_for_live_trading=false`, and
`decision_ready=false`.

Endpoints: `GET /analysts/macro/health`, `GET /analysts/macro/metadata`,
`POST /analysts/macro/analyze`. CLI: `python -m app.cli.analyst --analyst macro`.
See [Macro Economist docs](docs/MACRO_ECONOMIST.md) and
[Phase 8B plan](docs/phases/PHASE_08B_MACRO_ECONOMIST.md).
