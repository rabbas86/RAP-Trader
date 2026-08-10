# Unified Research Data Platform (Phase 8A)

## Overview

The Unified Research Data Platform (Phase 8A) is a deterministic, offline,
read-only data layer that normalizes, versions, and serves research data from
multiple domains (market, fundamental, macro, calendar, news/events) behind a
single point-in-time-safe contract. It is **research-only** and
**suitable_for_live_trading = false**.

The platform never connects to the network by default, never invokes an LLM,
never downloads models, and never imports or references broker, execution,
risk, portfolio, committee, or Chairman components.

## Architecture

```
Caller-supplied raw data
       │
       ▼
  Adapters (market, fundamentals, macro, events, news, mock)
       │  — normalize, fingerprint, assign source identity
       ▼
  DataNormalizationService (one canonical pass)
       │
       ▼
  InMemoryDataRecordStore / JSONFileDataRecordStore
       │  — query by domain, entity, series, period, as_of, revision
       ▼
  DataQualityService  +  PointInTimeRevisionService
       │  — quality scoring, revision lineage, no-lookahead enforcement
       ▼
  ResearchDataSnapshotService  →  ResearchDataSnapshot
       │  — immutable, deterministic, point-in-time safe
       ▼
  MIFP FeatureSnapshot  (Phase 6.5 boundary)
```

## Domain model

All 26 data-platform models live in `app/domain/models/data_platform.py`:

| Model | Description |
|---|---|
| `DataRecordId` | Safe canonical identifier (RootModel, frozen) |
| `DataDomain` | Enum of data domains (market, fundamental, macro, central_bank, corporate_action, earnings, calendar, news, alternative) |
| `DataSourceIdentity` | Identity of the origin system (provider, dataset, version, schema, offline_capable, authoritative) |
| `DataAvailability` | Temporal provenance (observed_at, published_at, available_at, ingested_at, effective_from/to) |
| `DataRevision` | Revision lineage entry (revision_id, revision_number, previous_revision_id, revised_at, available_at, reason, changed_fields, source_fingerprint) |
| `DataQuality` | Quality metadata (completeness, consistency, timeliness, source_reliability, anomaly_flags, warnings, score) |
| `NormalizedDataRecord` | Immutable normalized record (record_id, domain, value, units, currency, availability, revision, source, quality, source_fingerprint, schema_version, metadata, research_only, suitable_for_live_trading) |
| `RevisionPolicy` | Enum (vintage, realtime, latest) |
| `Frequency` | Enum (realtime, hourly, daily, weekly, monthly, quarterly, annual) |
| `EconomicSeriesDefinition` | Metadata for an economic/market series |
| `EconomicObservation` | A single point-in-time observation with revision metadata |
| `EventImportance` | Enum (low, medium, high) |
| `EventRecord` | Normalized event (earnings, news, FOMC, etc.) with scheduled/occurred/published/available timestamps |
| `SnapshotProvenance` | Provenance metadata for a snapshot |
| `QualitySummary` | Aggregate quality for a snapshot |
| `ResearchDataSnapshot` | Immutable point-in-time-safe collection of records |
| `SnapshotErrorCode` | Enum of error codes (INVALID_REQUEST, NO_DATA, LOOKAHEAD_REJECTED, SOURCE_NOT_AVAILABLE, MAX_RECORDS_EXCEEDED) |
| `DataPlatformError` | Research-only error with safe message and code |
| `SnapshotRequest` | Request type for point-in-time-safe snapshot creation |

All models use strict Pydantic v2 (`ConfigDict(strict=True, extra="forbid")`),
frozen immutality where appropriate, timezone-aware UTC timestamps, no NaN,
no infinity, no credentials, no absolute local paths, bounded metadata, and
deterministic serialization.

## Raw vs. normalized data

**Raw/source data** enters through domain-specific adapters. Each adapter
converts its input into a `NormalizedDataRecord` with:

- A stable `source_fingerprint` (SHA-256 of the raw/source payload).
- A `DataSourceIdentity` capturing provider, dataset, version, and schema.
- A `DataRevision` with lineage (revision_number, previous_revision_id).
- A `DataAvailability` block with observed_at, available_at, ingested_at.

**Normalized data** is the canonical form consumed by all downstream systems.
Normalization performs exactly **one** canonical pass — no silent interpolation,
no silent backfill, no overwriting of old revisions, no inventing missing
values.

## Point-in-time correctness

The platform enforces strict point-in-time semantics:

- **First release selected before revision publication**: The earliest
  available `available_at` is selected for any historical `as_of` query.
- **Revised value selected only after `available_at`**: A revised value is
  only visible when the revision's `available_at` is <= the query `as_of`.
- **Future revisions cannot leak backward**: The revision engine rejects any
  revision whose `available_at` is after the `as_of` time.
- **Future filings/events cannot appear in historical snapshots**: The store
  queries filter by `available_at <= as_of`.
- **`scheduled_at != occurred_at != published_at != available_at`**: Calendar
  and event models distinguish these four timestamps explicitly.
- **Latest-known data is not silently used for historical `as_of` queries**:
  Queries without an explicit `as_of` use "now" (UTC at query time); all
  historical queries must specify `as_of`.
- **Duplicate logical records are revision-linked** rather than overwritten.
- **Malformed revision lineage is rejected** by model validators.
- **Snapshot records always satisfy** `available_at <= snapshot.as_of`.

## Timestamps and calendar semantics

Four distinct timestamps are used across the data platform:

| Timestamp | Meaning |
|---|---|
| `scheduled_at` | When an event was scheduled (e.g., an FOMC meeting) |
| `occurred_at` | When the event actually occurred |
| `published_at` | When the data/event was first published by the source |
| `available_at` | When the system could first use the data without lookahead |

The invariant: `scheduled_at` <= `occurred_at` <= `published_at` <=
`available_at` <= `ingested_at` (where applicable). The
`ResearchCalendarService` provides deterministic offline calendar support,
including business-day computation.

## Revisions

Each logical data record has a revision lineage:

- **Revision 0** (first release): no `reason`, no `previous_revision_id`.
- **Revision N** (N > 0): must have a `previous_revision_id` pointing to
  revision N-1, and a `reason` describing the change.

The `PointInTimeRevisionService` selects the correct revision for a given
`as_of` time, rejecting future revisions. Revision fingerprints change when the
source or revision changes.

## Normalization

The `DataNormalizationService` performs a single canonical normalization pass:

- **Units**: Standardized (e.g., `$`/`USD` -> `currency`, `%` -> `percent`).
- **Currencies**: Normalized to ISO 4217 uppercase (e.g., `usd` -> `USD`).
- **Symbols/entities**: Uppercased, class-share normalization (e.g., `BRK-B` ->
  `BRK.B`), validation against disallowed characters.
- **Timestamps**: All normalized to UTC; naive timestamps rejected.
- **Signs**: Configurable sign conventions (as_reported, outflow_negative,
  expense_positive).
- **Record IDs**: Validated against `^[A-Za-z0-9][A-Za-z0-9_.-]*$`.
- **Duplicate detection**: Same logical key + same revision is flagged as a
  duplicate.
- **Revision links**: Preserved through normalization.
- **Source fingerprints**: SHA-256 of canonical JSON of the source payload.

## Quality

The `DataQualityService` performs deterministic quality assessment. It checks:

- **Missing required fields** (value, symbol, period, event_time).
- **Impossible values** (NaN, infinity).
- **Inconsistent units** within the same domain/symbol/series group.
- **Chronology errors** (available_at > as_of, period_start > period_end).
- **Duplicate records** (same record_id + revision_number).
- **Stale records** (age beyond configured threshold).
- **Unexpected gaps** between consecutive observations.
- **Revision anomalies** (revision > 0 with no previous_revision_id).
- **Source conflicts** (multiple sources claiming the same observation).

Quality scoring is deterministic: `score = (completeness + consistency +
timeliness + 1.0) / 4`, rounded to 6 decimal places. Source reliability is never
fabricated — it is only reported if explicitly provided by the source.

## Sources and registry

The `DataSourceRegistry` supports:

- `register(source)` / `unregister(source)`
- `list()` — returns all registered sources
- `domains()` — returns domains covered by registered sources
- Source priority and source metadata via `DataSourceIdentity`
- Offline capability flag (`offline_capable=True` by default)
- Source reliability metadata (never fabricated)

No automatic internet connection is made. All adapters are offline-capable by
default.

## Record stores

Two store implementations are provided:

### InMemoryDataRecordStore
- In-process dict-based storage with RLock.
- Supports `put`, `put_many`, `list`, `get`, `query` (by domain, entity,
  available_at, as_of).
- `query` enforces `available_at <= as_of` for point-in-time safety.

### JSONFileDataRecordStore
- Extends `InMemoryDataRecordStore` with atomic JSON persistence.
- Uses `tempfile.mkstemp` + `os.replace` for atomic writes.
- Enforces schema version, safe filenames (must end in `.json`), and path
  traversal protection (store path must remain within configured root).
- No pickle, no cloud, no credentials.

## Fingerprinting

Stable SHA-256 fingerprints are computed for:

- **Raw/source payload**: `sha256_fingerprint(raw_dict)`
- **Normalized records**: `source_fingerprint` field on `NormalizedDataRecord`
- **Revisions**: `source_fingerprint` on `DataRevision`
- **Snapshots**: `snapshot_id` is derived deterministically from input
  fingerprints and as_of time.

Same normalized input always produces the same fingerprint. Any change to the
source or revision produces a different fingerprint. `canonical_json()` rejects
NaN and infinity.

## Snapshot service

The `ResearchDataSnapshotService` produces `ResearchDataSnapshot` objects:

- **Deterministic**: Same inputs always produce the same snapshot_id.
- **Sorted**: Records are sorted by `(record_id, revision_number,
  source_fingerprint)`.
- **Bounded**: `max_records` truncates with a `partial` flag and warning.
- **Immutable**: Frozen Pydantic model; mutation raises `ValidationError`.
- **Point-in-time safe**: Model validator rejects any record with
  `available_at > as_of`.
- **Revision-correct**: Latest available revision <= as_of is included.
- **Provenance-complete**: Full source_versions, input_fingerprints, and
  SnapshotProvenance are embedded.
- **Source-version aware**: Each snapshot records the source versions that
  contributed.
- **Partial-aware**: When `max_records` is exceeded and `allow_partial=True`,
  the snapshot is marked `partial=True` with a `max_records_limited` warning.
  When `allow_partial=False`, a `DataPlatformError` is raised.

## Adapters

### Market data adapter
Wraps Phase 2 `MarketDataProvider` and converts historical bars to
`NormalizedDataRecord` objects. Preserves provider, adjustment, session,
timeframe, timestamp, and source fingerprint. Does not duplicate OHLCV
generation or validation logic.

### Fundamental adapter
Preserves filing `available_at`, keeps `period_end` separate from
`available_at`, preserves restatement lineage through revision numbers. No
financial ratio calculation is moved into the data platform; no Fundamental
Analyst logic is duplicated.

### Macro adapter
Provides deterministic offline support for CPI, Core CPI, PCE, Core PCE,
unemployment, NFP, GDP, PMI, policy rate, 2Y/10Y yields, yield spread, credit
spread, money supply, industrial production, and retail sales. No bullish/bearish
direction is expressed.

### Events adapter
Normalizes `EventRecord` objects into `NormalizedDataRecord` objects, mapping
event types to domains (news -> NEWS, earnings -> EARNINGS, central-bank ->
CENTRAL_BANK, others -> CALENDAR). Preserves all four timestamp semantics and
revision lineage.

### News adapter
Normalizes news events into the NEWS domain. No sentiment analysis, no LLM
processing, no News Analyst output. The normalized record stores title, summary,
and structured payload as the `value` dict with `headline` key.

### Mock adapter
Provides deterministic synthetic data for testing and CLI default operation.

## API

All endpoints are read-only, require no network by default, and return safe
structured responses:

| Method | Path | Description |
|---|---|---|
| GET | `/data-platform/health` | Health status and platform version |
| GET | `/data-platform/sources` | List registered data sources |
| GET | `/data-platform/domains` | List supported data domains |
| GET | `/data-platform/series` | Query normalized records (domain, symbol, limit filters) |
| GET | `/data-platform/calendar` | Query calendar events (start, end, event_type filters) |
| POST | `/data-platform/snapshot` | Produce a point-in-time-safe research data snapshot |

All errors expose only safe codes and messages. Internal exceptions are never
exposed to callers.

## CLI

```shell
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --summary
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --json
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --domain macro
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --symbol AAPL
python -m app.cli.data_platform --as-of 2026-08-01T00:00:00+00:00 --series CPI
```

No server, no network, no model download. The CLI uses the same deterministic
offline data by default.

## MIFP integration boundary

```
Unified Data Platform  →  ResearchDataSnapshot  →  MIFP
```

The data platform serves `ResearchDataSnapshot` as the canonical input. MIFP
(Phase 6.5) consumes these snapshots. The data platform does not duplicate any
MIFP feature computation. Phase 6.5 behavior is unchanged. Compatibility tests
ensure the boundary is clean.

## Analyst integration

The data platform does **not** rewrite Technical or Fundamental analysts. It
provides lightweight helpers/adapters so future analysts can consume
`ResearchDataSnapshot`. Phase 7.5 lifecycle remains canonical.

## Safety

- **No network**: All adapters are offline-capable by default. No HTTP, urllib,
  or httpx calls in the data platform.
- **No LLM**: No OpenAI, transformers, torch, or tensorflow imports.
- **No model download**: No model fetching or loading.
- **No credentials**: No API keys, tokens, passwords, or secrets stored or
  transmitted.
- **No trading**: No Broker, PaperBroker, ExecutionService, OrderRequest,
  RiskEngine, PortfolioManager, InvestmentCommittee, or Chairman imports.
- **No trading decisions**: All outputs carry `research_only=True` and
  `suitable_for_live_trading=False`.
- **No analyst opinions**: The platform provides raw data, not analysis.

## Limitations

- The platform is append-only in memory; persistence through `JSONFileDataRecordStore`
  is single-writer.
- `source_reliability` is never fabricated — it is only reported if explicitly
  provided by the data source.
- Calendar events use a simple Gregorian business-day model (no country-specific
  holiday calendars).
- No automatic data fetching — callers must supply raw data through adapters.
