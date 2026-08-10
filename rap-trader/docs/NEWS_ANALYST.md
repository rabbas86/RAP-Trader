# Phase 9: News Analyst

## Overview

The News Analyst is a deterministic, offline, research-only module that consumes
normalized event/news records from the Phase 8A Unified Research Data Platform
and produces an `AnalystOpinion` through the Phase 5 / 7.5 lifecycle.

It never fetches external data, never generates trades, never allocates capital,
and never calls RiskEngine, PortfolioManager, or InvestmentCommittee.

## Architecture

```
AnalystRequest (extra_context.snapshot or extra_context.events)
        │
        ▼
┌──────────────────────────────────────────┐
│  ObservationExtractor                    │
│  Projects EventRecord → NewsObservation  │
│  Point-in-time filter: availability_at   │
│  ≤ as_of                                │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  EventGroupingService                    │
│  Groups observations into EventClusters │
│  Deterministic: (entity, event_type,    │
│  day-bucket, canonical fingerprint)      │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  NewsSourceQualityService                │
│  Maps DataSourceIdentity → SourceQuality │
└──────────────────────────────────────────┘
        │
        ▼
┌──────┬───────────┬──────────┬──────────┬──────────────┐
│ News │ News      │ News     │ News      │ Event        │
│ Materiality │ Novelty  │ Decay  │ Confirmation │ Lifecycle  │
│ Service     │ Service  │ Service │ Service     │ Service    │
└──────┴───────────┴──────────┴──────────┴──────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  _classify                                │
│  Produces ClassifiedEvent per cluster     │
│  • event_type, orientation, importance   │
│  • confirmation_status, decay_factor     │
│  • is_duplicate, novelty_score           │
│  • lifecycle_state, is_excluded          │
│  • materiality_score                     │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  NewsEvidenceFactory                     │
│  Produces EvidenceItem (main + supp)     │
│  Point-in-time clamped timestamps        │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  EvidenceValidationService               │
│  Validates timestamps ≤ as_of            │
│  Validates freshness ≤ threshold         │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  NewsOpinionSynthesisService             │
│  Deterministic score → direction         │
│  • bullish / bearish / mixed / neutral   │
│  • insufficient_evidence                  │
│  Metric-aware negative signal detection   │
│  (Phase 7 pattern adapted)               │
└──────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────┐
│  AnalystOpinion                          │
│  Direction, confidence, evidence, etc.   │
└──────────────────────────────────────────┘
```

## Data Flow

1. **Input**: `AnalystRequest` with either:
   - `extra_context.snapshot` — a `ResearchDataSnapshot` containing news-domain
     records, or
   - `extra_context.events` — a list of `EventRecord` objects (convenience
     path for programmatic use).

2. **Extraction**: `ObservationExtractor` projects records/events into
   `NewsObservation` values, filtering to `available_at <= as_of`.

3. **Grouping**: `EventGroupingService` groups observations into
   `EventCluster` objects using a deterministic key:
   `(entity, event_type, day-bucket, canonical_fingerprint)`.

4. **Scoring**: Each specialist service annotates the cluster:
   - `NewsMaterialityService` — is the event material?
   - `NewsNoveltyService` — duplicate, follow-up, or new?
   - `NewsDecayService` — relevance decay by event type half-life.
   - `NewsConfirmationService` — confirmed, partially confirmed, conflicting.
   - `EventLifecycleService` — active, cancelled, superseded, resolved, archived.

5. **Classification**: `_classify` produces a `ClassifiedEvent` per cluster.

6. **Evidence**: `NewsEvidenceFactory` builds `EvidenceItem` objects from
   the classified clusters.

7. **Validation**: `EvidenceValidationService` ensures all evidence is
   point-in-time safe (no `available_at > as_of`) and not stale (unless
   `stale_input_allowed=True`).

8. **Synthesis**: `NewsOpinionSynthesisService` combines classified events
   into a `SynthesisResult` with direction, confidence, and warnings.

9. **Opinion**: The `AnalystOpinion` is assembled with direction, confidence,
   evidence, assumptions, warnings, limitations, and data freshness.

## Safety Guarantees

### Research-Only

- `research_only = True`
- `suitable_for_live_trading = False`
- `decision_ready = False`
- No BUY/SELL decisions are generated.

### Point-in-Time Safe

- Events with `available_at > as_of` are filtered at the extraction boundary.
- The `ResearchDataSnapshot.validate_records_point_in_time()` enforces
  `available_at <= snapshot.as_of`.
- `EvidenceValidationService.validate()` rejects evidence with
  `available_at > as_of`.
- `generated_at = as_of` (never wall-clock `now`).
- `data_freshness.evaluated_at = as_of` (never wall-clock `now`).

### No LLM, No Network, No Runtime Dependencies

- No imports of `openai`, `anthropic`, `torch`, `tensorflow`, `httpx`, etc.
- No imports of `RiskEngine`, `PortfolioManager`, `PaperBroker`, etc.
- All classification is rule-based on structured payload fields.

### Durable Opinion IDs

- Opinion IDs are deterministic: `sha256(ticker|as_of|direction|evidence_ids)`.
- Re-analyzing the same input at the same `as_of` produces the same opinion.

## Configuration

All thresholds are in `NewsAnalystConfig` (`app/services/news_analysis/config.py`).
Key parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `research_only` | `True` | Always research-only |
| `suitable_for_live_trading` | `False` | Never suitable for live trading |
| `uncalibrated_confidence_cap` | `0.65` | Max confidence output |
| `stale_input_allowed` | `False` | Whether stale evidence is rejected |
| `base_evidence_confidence` | `0.7` | Base confidence per evidence item |
| `payload_change_threshold` | `0.05` | Fractional payload change for revision detection |
| `stale_decay_threshold` | `0.05` | Decay factor below which an event is stale |

### Decay Half-Lives (hours)

| Event Type | Half-Life |
|------------|-----------|
| `earnings` | 72h |
| `earnings_guidance` | 168h |
| `revenue_guidance` | 168h |
| `analyst_revision` | 24h |
| `merger_acquisition` | 336h |
| `product_failure` | 72h |
| `central_bank` | 168h |
| `bankruptcy` | 720h |
| `other` (default) | 24h |

## Event Lifecycle

The `EventLifecycleService` inspects `structured_payload` and `metadata` for
lifecycle state indicators:

| State | Keyword(s) | Final? | Excluded? |
|-------|------------|--------|-----------|
| `ACTIVE` | (none) | No | No |
| `CANCELLED` | `cancelled`, `canceled`, `called_off`, `called off`, `calledoff` | Yes | Yes |
| `RESOLVED` | `resolved`, `completed`, `occurred`, `happened`, `finalized`, `settled` | Yes | No |
| `ARCHIVED` | `archived`, `historical`, `past`, `expired` | Yes | Yes |
| `SUPERSEDED` | `superseded`, `replaced`, `overridden`, `deprecated` | Yes | Yes |

Lifecycle state is checked in `structured_payload` fields:
- `lifecycle_state`
- `status`
- `event_status`
- `state`

## Materiality Assessment

The `NewsMaterialityService` deterministically scores materiality based on:

1. **Event type** — critical events (bankruptcy, merger, etc.) are always
   material with a minimum score boost.
2. **Importance** — `HIGH` > `MODERATE` > `LOW` > `TRIVIAL`.
3. **Scope** — `COMPANY` > `INDUSTRY` > `MACRO` > `GLOBAL`.
4. **Source quality** — `AUTHORITATIVE` > `PRIMARY` > `HIGH_QUALITY_SECONDARY`
   > `SECONDARY` > `VERIFIED` > `UNVERIFIED` > `UNKNOWN`.

The composite score is a weighted sum. If the score meets the
`materiality_threshold` (default 0.4), the event is material.

## Confirmation Assessment

The `NewsConfirmationService` examines multiple sources reporting the same
event:

- **Single source** → `UNVERIFIED`
- **Multiple sources, all agree** → `CONFIRMED`
- **Multiple sources, some agree** → `PARTIALLY_CONFIRMED`
- **Multiple sources, all disagree** → `CONFLICTING`
- **Later revision supersedes earlier** → `SUPERSEEDED`

Agreement is checked on structured payload fields:
`direction`, `surprise`, `eps`, `revenue`, `guidance_low`, `guidance_high`.

## Novelty Detection

The `NewsNoveltyService` uses deterministic fingerprints:

- Fingerprint = `sha256(entity, event_type, occurred_at, stable_payload)`
- **First report** → `novelty_score = 1.0`
- **Exact duplicate** (same fingerprint) → `novelty_score = 0.0`
- **Revision** (payload changed) → `novelty_score = 0.3`
- **Follow-up** (same payload, different source) → `novelty_score = 0.1`

## API

The News Analyst is registered in the analyst registry and accessible via:

```
GET  /analysts              # List all analysts (includes "news")
GET  /analysts/news/health  # Health check
GET  /analysts/news/metadata # Analyst metadata
POST /analysts/news/analyze # Run analysis
```

### POST /analysts/news/analyze

Accepts an `AnalystRequest` JSON body:

```json
{
  "analyst_id": "news",
  "ticker": "AAPL",
  "timeframe": "1d",
  "as_of": "2026-08-01T12:00:00Z",
  "lookback": 60,
  "horizon": 5,
  "asset_class": "equity",
  "extra_context": {
    "events": [
      {
        "event_id": "evt-1",
        "event_type": "earnings",
        "entity": "AAPL",
        "occurred_at": "2026-08-01T12:00:00Z",
        "available_at": "2026-08-01T12:00:00Z",
        "headline_or_title": "Company beats earnings estimates",
        "structured_payload": {"direction": "positive", "eps": 1.5, "surprise": 0.5},
        "revision": {
          "revision_id": "rev-0",
          "revision_number": 0,
          "revised_at": "2026-08-01T12:00:00Z",
          "available_at": "2026-08-01T12:00:00Z",
          "source_fingerprint": "..."
        }
      }
    ]
  }
}
```

## CLI

```bash
python -m app.cli.analyst --analyst news --ticker AAPL \\
  --as-of 2026-08-01T12:00:00Z --input-events events.json --as-json
```

## Modules

| Module | File | Responsibility |
|--------|------|----------------|
| `config.py` | `NewsAnalystConfig` | All thresholds, half-lives, weights |
| `domain.py` | Enums | `NewsEventType`, `NewsOrientation`, `NewsImportance`, `NewsScope`, `ConfirmationStatus`, `SourceQuality` |
| `observations.py` | `NewsObservation`, `ObservationExtractor` | Project records/events into typed observations |
| `classification.py` | `classify()` | Deduce event type, orientation, importance, scope |
| `source_quality.py` | `NewsSourceQualityService` | Map source identity → quality rating |
| `novelty.py` | `NewsNoveltyService` | Duplicate and revision detection |
| `decay.py` | `NewsDecayService` | Event-type-specific relevance decay |
| `confirmation.py` | `NewsConfirmationService` | Multi-source confirmation assessment |
| `event_grouping.py` | `EventGroupingService` | Deterministic clustering |
| `evidence.py` | `NewsEvidenceFactory` | Build Phase-5 `EvidenceItem` objects |
| `materiality.py` | `NewsMaterialityService` | Deterministic materiality assessment |
| `lifecycle.py` | `EventLifecycleService` | Cancelled/superseded/resolved/archived handling |
| `synthesis.py` | `NewsOpinionSynthesisService` | Combine classified events into direction + confidence |
| `service.py` | `NewsAnalyst` | Orchestrates all services into `AnalystOpinion` |
