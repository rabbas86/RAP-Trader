# Technical Analyst

The Phase 6 technical analyst is deterministic, offline, and research-only. Registered as `technical`, it consumes normalized OHLCV bars available at an explicit `as_of` time and returns only an `AnalysisDirection`.

Its snapshot contains SMA/EMA values and slopes, crossover state and age, ROC, RSI, MACD, True Range, Wilder ATR, Bollinger Bands and bandwidth, OBV, rolling volume average, relative volume, and typical-price VWAP. Five-bar swing fractals are confirmed only at pivot index + 2. Structure, break-of-structure, change-of-character, and clustered support/resistance use confirmed points only.

Evidence groups cover trend, momentum, volatility, volume, structure, and levels. Optional serialized `kronos_forecast` and `backtest_result` entries in `extra_context` become forecast and calibrated backtest evidence. Strength, confidence, freshness, coverage, agreement, contradiction, calibration, and missing data determine direction and confidence deterministically.

Use `POST /analysts/technical/analyze` for an opinion and `GET /analysts/technical/snapshot?ticker=AAPL&timeframe=1d&lookback=60&as_of=...` for a snapshot. The CLI accepts `--analyst-id technical` or `--analyst technical-analyst`, `--input-json`, and `--as-json`.

Every opinion has a validated acyclic trace from request and source nodes through evidence to the opinion. Future bars are excluded. The analyst never emits orders, quantities, allocations, stops, or approvals.
