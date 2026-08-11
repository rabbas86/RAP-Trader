# Phase 14 — Platform Stabilization

Goal: harden operational reliability and safety observability without changing research behavior or bypassing Phase 13 governance.

## In scope
- Config/runtime startup validation for research-only invariants.
- Standardized API error schema and global exception handling.
- `/system/readiness` endpoint returning startup-check results.
- Regression tests for startup validation, readiness, and safety invariants.
- Documentation for Phase 14 boundaries.

## Non-goals
- Live trading, broker integration, or execution paths.
- Analyst/committee/chairman business logic changes.
- Model or prompt changes.
- Dropping or weakening existing risk controls.

## Safety rules
- All changes must preserve `research_only=true`, `suitable_for_live_trading=false`, and `decision_ready=false` semantics.
- No new dependencies unless added to `pyproject.toml` explicitly.
- Keep `ruff`, `mypy --strict`, and `pytest` green.

## Implemented controls

- Startup validation retains the two-part live-mode gate and additionally requires
  `APP_ENV` to be `staging` or `production` plus `ALLOW_LIVE_TRADING=true` whenever
  live trading is requested. All live flags remain disabled by default.
- Kronos and backtesting remain offline by default. Disabling either offline-only
  flag requires its separate, explicit `ALLOW_NON_OFFLINE_*` opt-in.
- API failures use the frozen `ApiError` contract. Validation, HTTP, and unexpected
  failures are handled globally; unexpected exception details are never returned.
- `GET /system/readiness` reports only safe config-invariant and environment check
  results. A failing check produces `status: degraded`; no configuration values,
  credentials, paths, or other secrets are included.

## Operational boundary

Readiness is an observability signal, not authorization to trade. It does not enable
live execution or bypass the Risk Engine, Risk Officer, Investment Committee, or
Chairman. Research-only, unsuitable-for-live-trading, and non-decision-ready
semantics remain unchanged. Operators should keep every opt-in false unless a
separately approved deployment explicitly requires it.
