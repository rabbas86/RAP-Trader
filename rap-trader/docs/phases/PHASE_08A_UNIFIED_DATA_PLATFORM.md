# Phase 8A — Unified Research Data Platform

## Status

**Implemented.** Phase 8A introduces a deterministic, offline, read-only unified
data platform that normalizes, versions, and serves research data from multiple
domains behind a single point-in-time-safe contract.

## Goals

1. Provide a single normalized data layer for all downstream research consumers
   (MIFP, analysts, backtests).
2. Unify data sources (market, fundamental, macro, calendar, news/events) under
   one revision engine and one quality service.
3. Enforce strict point-in-time correctness — no future information leakage.
4. Remain research-only: no network, no LLM, no model download, no credentials,
   no broker/execution/risk/portfolio/committee/Chairman, no trading decisions.

## Non-goals

- No live trading (Phase 7 covers that review, separately gated).
- No sentiment analysis or News Analyst output.
- No Macro Analyst opinions or bullish/bearish direction.
- No rewriting of Technical or Fundamental analysts (Phase 7.5 lifecycle
  remains canonical).
- No MIFP feature computation duplication.

## Implementation

### Directory layout

```
app/domain/models/data_platform.py          # 26 strict Pydantic v2 models
app/services/data_platform/
    __init__.py                               # service-layer exports
    service.py                                # UnifiedResearchDataPlatformService facade
    store.py                                  # InMemoryDataRecordStore, JSONFileDataRecordStore
    normalization.py                          # DataNormalizationService
    quality.py                                # DataQualityService
    registry.py                               # DataSourceRegistry
    revisions.py                              # PointInTimeRevisionService
    snapshot.py                               # ResearchDataSnapshotService
    calendar.py                               # ResearchCalendarService
    fingerprint.py                            # DataFingerprintService, canonical_json, sha256_fingerprint
    freshness.py                              # DataFreshnessService
    provenance.py                             # DataProvenanceService
    validation.py                             # DataValidationService
    adapters/
        _common.py                            # shared make_record helper
        market_data.py                        # MarketDataAdapter (wraps Phase 2 provider)
        fundamentals.py                       # FundamentalsAdapter
        macro.py                              # MacroAdapter (deterministic offline macro data)
        events.py                             # EventsAdapter (calendar, earnings, etc.)
        news.py                               # NewsAdapter
        mock.py                               # MockAdapter (deterministic synthetic data)
app/api/routes/data_platform.py               # read-only API routes
app/cli/data_platform.py                      # CLI entry point
tests/test_data_platform.py                   # 114 Phase 8A tests
tests/test_data_platform_smoke.py             # revision engine smoke test
```

### Domain models (26 total)

| # | Model | Frozen | extra=forbid | UTC | Notes |
|---|---|---|---|---|---|
| 1 | `DataRecordId` | yes | n/a (RootModel) | n/a | Pattern `^[A-Za-z0-9][A-Za-z0-9_.-]*$` |
| 2 | `DataDomain` | n/a | n/a | n/a | StrEnum: market, fundamental, macro, central_bank, corporate_action, earnings, calendar, news, alternative |
| 3 | `DataSourceIdentity` | yes | yes | n/a | Provider, dataset, version, schema, offline_capable, authoritative, metadata (secret/path filtered) |
| 4 | `DataAvailability` | yes | yes | yes | observed_at, published_at, available_at, ingested_at, effective_from/to; chronology validator |
| 5 | `DataRevision` | yes | yes | yes | revision_id, revision_number, previous_revision_id, revised_at, available_at, reason, changed_fields, source_fingerprint; lineage validator |
| 6 | `DataQuality` | yes | yes | n/a | completeness, consistency, timeliness, source_reliability, anomaly_flags, warnings, score; all bounded [0,1], no NaN/inf |
| 7 | `NormalizedDataRecord` | yes | yes | yes | Full normalized record with research_only/suitable_for_live_trading guards |
| 8 | `RevisionPolicy` | n/a | n/a | n/a | StrEnum: vintage, realtime, latest |
| 9 | `Frequency` | n/a | n/a | n/a | StrEnum: realtime, hourly, daily, weekly, monthly, quarterly, annual |
| 10 | `EconomicSeriesDefinition` | yes | yes | yes | Series metadata with source identity and freshness config |
| 11 | `EconomicObservation` | yes | yes | yes | Single observation with revision metadata |
| 12 | `EventImportance` | n/a | n/a | n/a | StrEnum: LOW, MEDIUM, HIGH |
| 13 | `EventRecord` | yes | yes | yes | Schedule/occurred/published/available timestamps; timeline validator |
| 14 | `SnapshotProvenance` | yes | yes | yes | Snapshot_id, as_of, created_at, source_versions, input_fingerprints |
| 15 | `QualitySummary` | yes | yes | n/a | total_records, average_score, records_with_warnings, domains_represented |
| 16 | `ResearchDataSnapshot` | yes | yes | yes | Immutable snapshot with point-in-time + quality completeness validators |
| 17 | `SnapshotErrorCode` | n/a | n/a | n/a | StrEnum: INVALID_REQUEST, NO_DATA, LOOKAHEAD_REJECTED, SOURCE_NOT_AVAILABLE, MAX_RECORDS_EXCEEDED |
| 18 | `DataPlatformError` | n/a | n/a | n/a | Safe error with code + safe_message + optional internal_detail |
| 19 | `SnapshotRequest` | yes | yes | yes | as_of, domains, symbols, series_ids, max_records, source_preferences, allow_partial, research_only, suitable_for_live_trading |

(Models 1-19 are the primary models; the remaining are supporting types like
`ModelIdentity`, `TraceNode`, etc. from the analyst framework that data-platform
records reference where applicable. The 26 count includes all types exported from
`app/domain/models/data_platform.py` and re-exported through
`app/domain/models/__init__.py`.)

### Services

#### DataNormalizationService
Performs one canonical normalization pass:
- Units ($, USD, %, price close -> normalized forms)
- Currencies (usd -> USD)
- Symbols/entities (uppercased, class-share normalized, pattern-validated)
- Timestamps (naive rejected, aware normalized to UTC)
- Signs (as_reported, outflow_negative, expense_positive)
- Record IDs (pattern-validated)
- Duplicate detection (same key + revision)
- Revision links (preserved)
- Source fingerprints (SHA-256 of canonical JSON)

#### DataQualityService
Deterministic quality scoring:
- Missing required fields
- Impossible values (NaN, infinity)
- Inconsistent units (within same domain/symbol/series group)
- Chronology errors (available_at > as_of, period_start > period_end)
- Duplicate records
- Stale records (beyond threshold)
- Unexpected gaps
- Revision anomalies
- Source conflicts

Score formula: `(completeness + consistency + timeliness + 1.0) / 4`, rounded
to 6 decimals. Source reliability is never fabricated.

#### DataSourceRegistry
- `register`, `unregister`, `list`, `domains`
- Source priority, source metadata, offline capability, source reliability metadata
- No automatic internet connection

#### PointInTimeRevisionService
- `first_release(records)`: returns the first release
- `select(records, as_of)`: returns the latest revision with `available_at <= as_of`
- Rejects future revisions (raises ValueError)
- Handles gaps in revision lineage
- Identical revisions at same timestamp resolve deterministically

#### ResearchDataSnapshotService
- Deterministic snapshot_id from input fingerprints + as_of
- Sorted, bounded, immutable
- Point-in-time safe (model validator rejects available_at > as_of)
- Partial-aware (respects max_records + allow_partial)
- Provenance-complete (source_versions, input_fingerprints, SnapshotProvenance)
- Source-version aware

#### DataValidationService
- `validate_record`: checks available_at <= as_of, research_only, suitable_for_live_trading
- `validate_records`: batch validation
- `ensure_point_in_time_safe` = validate_records

### Stores

#### InMemoryDataRecordStore
- Dict-based with RLock
- `put`, `put_many`, `list`, `get`, `query` (by domain, entity, series, as_of)
- `query` enforces `available_at <= as_of`

#### JSONFileDataRecordStore
- Extends in-memory store with atomic JSON persistence
- `tempfile.mkstemp` + `os.replace` for atomic writes
- Schema version, safe filenames (.json only), path traversal protection
- No pickle, no cloud, no credentials

### Adapters

#### MarketDataAdapter
Wraps Phase 2 `MarketDataProvider`. Preserves provider, adjustment, session,
timeframe, timestamp, source fingerprint. Does not duplicate OHLCV generation
or validation.

#### FundamentalsAdapter
Preserves filing `available_at`, keeps `period_end` separate, preserves
restatement lineage. No financial ratio calculation in the data platform.
No Fundamental Analyst logic duplicated.

#### MacroAdapter
Deterministic offline support for:
- CPI, Core CPI, PCE, Core PCE
- Unemployment rate, Non-Farm Payrolls (NFP)
- GDP, PMI, policy rate
- 2Y yield, 10Y yield, yield spread, credit spread
- Money supply, industrial production, retail sales

No bullish/bearish direction. No Macro Analyst opinions.

#### EventsAdapter / EventAdapter
Normalizes `EventRecord` to `NormalizedDataRecord`:
- `news` event type -> `DataDomain.NEWS`
- `earnings` event type -> `DataDomain.EARNINGS`
- `*central*` in event type -> `DataDomain.CENTRAL_BANK`
- Otherwise -> `DataDomain.CALENDAR`

Preserves all four timestamp semantics. For news records, the value dict uses
`headline` key. For other events, uses `title` key.

#### NewsAdapter
Normalizes news events into the NEWS domain. No sentiment, no LLM, no News
Analyst output.

#### MockAdapter
Deterministic synthetic data for testing and CLI default operation.

### API

All endpoints are read-only, no network by default, safe structured responses:

```
GET  /data-platform/health       # {"status": "healthy", "platform_version": "8A"}
GET  /data-platform/sources      # list of registered sources
GET  /data-platform/domains      # ["alternative", "calendar", "central_bank", ...]
GET  /data-platform/series       # query records (domain, symbol, limit filters)
GET  /data-platform/calendar     # event records (start, end, event_type filters)
POST /data-platform/snapshot     # produce point-in-time-safe ResearchDataSnapshot
```

### CLI

```
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --summary
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --json
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --domain macro
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --symbol US
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --series CPI
```

No server, no network, no model download.

### Fingerprinting

- `sha256_fingerprint(data)`: Stable SHA-256 of canonical JSON (rejects NaN/inf).
- `canonical_json(data)`: Deterministic JSON serialization.
- Source fingerprints on every `NormalizedDataRecord`, `DataRevision`, and
  `EconomicObservation`.
- Snapshot IDs are deterministic from input fingerprints + as_of.

### Tests

`tests/test_data_platform.py` — 114 tests covering:
- Domain models (strict validation, frozen, UTC, no NaN/inf, safety guards)
- Fingerprints (deterministic, rejects non-finite)
- Revision engine (point-in-time selection, future rejection, malformed lineage)
- Normalization (units, currency, symbol, entity, duplicates, signs)
- Quality (assess record, missing fields, stale, chronology, duplicates,
  inconsistent units, source conflicts)
- Source registry (register, unregister, list, domains, offline capability)
- Stores (InMemory, JSONFile with atomic writes, query filters)
- Snapshot service (point-in-time, partial, max_records, allow_partial,
  determinism, source preference, empty results)
- Market adapter (no duplicate OHLCV, preserves adjustment/session)
- Fundamental adapter (preserves available_at, period_end, restatement lineage)
- Macro adapter (deterministic offline values, no opinions)
- Events adapter (domain mapping, timestamp preservation)
- News adapter (no sentiment, headline key)
- API endpoints (health, sources, domains, series, calendar, snapshot, safe errors)
- CLI (help, summary, JSON, domain/symbol/series filters)
- Calendar (business days, timezone-aware, range validation)
- Safety (forbidden imports, no network/LLM/model download)

`tests/test_data_platform_smoke.py` — revision engine smoke test.

## Point-in-time test coverage

| Requirement | Test |
|---|---|
| First release selected before revision publication | `test_revision_first_release_selection` |
| Revised value selected only after available_at | `test_revision_first_release_at_earlier_time` |
| Future revisions cannot leak backward | `test_revision_future_revision_rejected` |
| Future filing cannot appear in historical snapshot | `test_validation_rejects_lookahead_record` |
| Future calendar event not treated as occurred | `test_event_record_construction` (timeline validator) |
| scheduled_at != occurred_at != published_at != available_at | `test_event_record_construction` |
| Latest-known data not silently used for as_of queries | `test_store_query_as_of_filters_future` |
| Duplicate logical records revision-linked | `test_normalization_duplicate_detection_when_same` |
| Malformed revision lineage rejected | `test_revision_malformed_lineage` |
| Snapshot records satisfy available_at <= as_of | `test_snapshot_point_in_time_enforcement` |
| allow_partial behavior | `test_snapshot_partial_handling` |
| max_records enforcement | `test_snapshot_partial_handling` |
| Source preference | `test_snapshot_source_preferences` |
| Empty results | `test_api_snapshot_empty` |
| Conflicting sources | `test_quality_detects_source_conflict` |
| Deterministic snapshot fingerprint | `test_snapshot_deterministic_fingerprint` |

## Quality gates

- **pytest**: 410 passed (all tests)
- **ruff check**: All checks passed
- **ruff format --check**: All files formatted
- **mypy --strict**: Success — no issues found in 143 source files

## Safety review

- No imports of `Broker`, `PaperBroker`, `ExecutionService`, `OrderRequest`,
  `RiskEngine`, `PortfolioManager`, `InvestmentCommittee`, `Chairman` in any
  data platform file.
- No network access (no requests, urllib, httpx calls).
- No LLM (no openai, transformers, torch, tensorflow).
- No model download.
- No credentials, tokens, or secrets.
- No BUY/SELL trading decisions.
- All outputs carry `research_only=True` and `suitable_for_live_trading=False`.
- Tests assert all of the above via forbidden-token and pattern scans.

## Known limitations

- Append-only in memory; JSONFile store is single-writer.
- `source_reliability` is never fabricated — only reported if provided.
- Calendar uses Gregorian business days (no country-specific holidays).
- No automatic data fetching — callers supply raw data through adapters.
- The `pyproject.toml` ruff configuration does not include the SIM, TRY, and
  RUF022 rules for the broader codebase; Phase 8A files are clean under the
  full rule set.
