# Phase 9: News Analyst

## Status: Complete

## Objective

Add a deterministic news analyst to the Phase 5 analyst framework. The News
Analyst consumes event/news data from the Phase 8A Unified Research Data
Platform, classifies and groups events, assesses materiality, novelty, decay,
confirmation, and lifecycle state, and produces a research-only
`AnalystOpinion`.

## Key Design Decisions

1. **Rule-based classification only.** No ML models, no LLM, no network.
   Event orientation, importance, and scope are derived from structured
   payload fields and keyword rules.

2. **Point-in-time safety.** All events are filtered to `available_at <= as_of`
   at the extraction boundary. Evidence validation enforces this a second time.
   `generated_at` and `data_freshness.evaluated_at` use `as_of`, never wall-clock `now`.

3. **Historical freshness is based on `as_of`, not wall-clock now.**
   The `NewsDecayService` computes relevance decay using configurable
   half-lives by event type. This is separate from the generic
   `DataFreshnessService` used by the analyst platform (which checks evidence
   staleness against a per-evidence-type threshold).

4. **NewsDecayService is distinct from DataFreshnessService.** The decay service
   models event relevance decay (how quickly news fades), while the freshness
   service models data staleness (whether the evidence is too old to use).

5. **Duplicate weight suppression.** Duplicate events within the same cluster
   do not multiply evidence weight. The `EventGroupingService` detects
   duplicates using fingerprints, and the synthesis service applies a
   duplicate penalty.

6. **Lifecycle handling.** Cancelled, superseded, and archived events are
   excluded from synthesis. Resolved events contribute but are flagged as final.

7. **Metric-aware negative signal detection.** Adapts the Phase 7 pattern
   (from the Fundamental Analyst) to news: even events with positive
   orientation are checked for metric-based negative signals (e.g.,
   debt_to_equity > 2.0, pe > 30) that can override growth signals.

8. **Deterministic output.** Opinion IDs are content hashes
   (`sha256(ticker|as_of|direction|evidence_ids)`). Re-analyzing the same
   input at the same `as_of` produces the same output.

## Modules

### New modules

- `app/services/news_analysis/materiality.py` — `NewsMaterialityService`
  and `MaterialityResult`. Deterministic scoring based on event type,
  importance, scope, and source quality.

- `app/services/news_analysis/lifecycle.py` — `EventLifecycleService`,
  `assess_lifecycle()`, `extract_lifecycle_state()`, `EventLifecycleState`,
  `LifecycleResult`. Handles cancelled/superseded/resolved/archived state
  discovery from structured payload fields.

### Existing modules (unchanged from Phase 9 scaffolding)

- `config.py` — `NewsAnalystConfig` with all thresholds, half-lives, weights.
- `domain.py` — Enums: `NewsEventType`, `NewsOrientation`, `NewsImportance`,
  `NewsScope`, `ConfirmationStatus`, `SourceQuality`.
- `observations.py` — `NewsObservation`, `ObservationExtractor`.
- `classification.py` — `classify()`, `EventClassification`.
- `source_quality.py` — `NewsSourceQualityService`.
- `novelty.py` — `NewsNoveltyService`, `NoveltyResult`.
- `decay.py` — `NewsDecayService`, `DecayResult`.
- `confirmation.py` — `NewsConfirmationService`, `ConfirmationResult`.
- `event_grouping.py` — `EventGroupingService`, `EventCluster`.
- `evidence.py` — `NewsEvidenceFactory`.
- `synthesis.py` — `NewsOpinionSynthesisService`, `SynthesisResult`.
- `service.py` — `NewsAnalyst`, `ClassifiedEvent`.

### Modified modules

- `app/services/news_analysis/service.py`:
  - `ClassifiedEvent` now carries `lifecycle_state`, `is_excluded`,
    `materiality_score`, and a `structured_payload` property.
  - `_classify` integrates materiality and lifecycle assessment.
  - Events with `ConfirmationStatus.SUPERSEDED` are also excluded alongside
    lifecycle-excluded events.
  - Assumptions, warnings, and limitations updated to reflect news-specific
    policies.

- `app/services/news_analysis/event_grouping.py`:
  - `_build_cluster` now computes `confirmation_status` and
    `confidence_penalty` instead of using placeholders.
  - Added `_aggregate_confirmation()` — determines status from source count
    and agreement.
  - Added `_compute_confidence_penalty()` — applies penalties for single
    sources, duplicates, and unverified quality.

- `app/services/news_analysis/config.py`:
  - Added `materiality_threshold` (default 0.4).
  - Added `materiality_importance_weights` mapping.
  - Added `materiality_scope_multipliers` mapping.
  - Added `materiality_source_quality_weights` mapping.
  - Added `materiality_critical_event_types` set.
  - Added `lifecycle_state_fields` list (payload keys to check).
  - Added `lifecycle_state_aliases` mapping (keyword → canonical state).

- `app/services/news_analysis/synthesis.py`:
  - Added metric-aware negative signal detection via
    `_metric_value_is_negative()`.
  - Fixed duplicate weight multiplication (clusters with `is_duplicate=True`
    receive a penalty and do not multiply the synthesis score).
  - Cleaned up unused imports (`timedelta`, `NewsScope`).
  - Fixed ruff lint issues (SIM102, return simplification).

## Input Formats

### Via snapshot

```python
extra_context = {
    "snapshot": {
        "as_of": "2026-08-01T12:00:00Z",
        "records": [
            {
                "record_id": "news:evt-1",
                "domain": "NEWS",
                "value": {"headline": "...", "payload": {...}},
                "series_id": "EARNINGS",
                "symbol_or_entity": "AAPL",
                ...
            }
        ]
    }
}
```

### Via events

```python
extra_context = {
    "events": [
        {
            "event_id": "evt-1",
            "event_type": "earnings",
            "entity": "AAPL",
            "occurred_at": "2026-08-01T12:00:00Z",
            "available_at": "2026-08-01T12:00:00Z",
            "headline_or_title": "Company beats earnings estimates",
            "structured_payload": {"direction": "positive", "eps": 1.5},
            "revision": {...},
            ...
        }
    ]
}
```

## Safety Verification

- ✅ No forbidden runtime dependencies (RiskEngine, PortfolioManager, etc.)
- ✅ No network or LLM imports
- ✅ `research_only = True`
- ✅ `suitable_for_live_trading = False`
- ✅ `decision_ready = False`
- ✅ Point-in-time safe (events with `available_at > as_of` excluded)
- ✅ `generated_at = as_of` (not wall-clock)
- ✅ `data_freshness.evaluated_at = as_of` (not wall-clock)
- ✅ Deterministic opinion IDs
- ✅ All modules have docstrings

## Test Coverage

64 tests in `tests/test_news_analyst.py` covering:
- Metadata and health
- Materiality (5 tests)
- Lifecycle (7 tests)
- Novelty (3 tests)
- Decay (4 tests)
- Confirmation (4 tests)
- Event grouping (4 tests)
- Point-in-time safety (5 tests)
- Historical freshness (3 tests)
- Duplicate/follow-up weight (2 tests)
- Cancelled/superseded/resolved handling (3 tests)
- Synthesis (5 tests)
- End-to-end integration (6 tests)
- API endpoints (6 tests)
- CLI (2 tests)
- Safety (4 tests)

## Phase Completion Checklist

- [x] Core modules implemented
- [x] Materiality assessment
- [x] Event lifecycle handling
- [x] Point-in-time safety verified
- [x] Historical freshness uses `as_of`
- [x] NewsDecayService separate from DataFreshnessService
- [x] Duplicate weight suppression
- [x] Metric-aware negative signal detection
- [x] Cancelled/superseded/resolved/archived handling
- [x] API endpoints registered (`/analysts/news/*`)
- [x] CLI support (`--analyst news --input-events ...`)
- [x] Documentation (`docs/NEWS_ANALYST.md`, `docs/phases/PHASE_09_NEWS_ANALYST.md`)
- [x] 64 comprehensive tests passing
- [x] Ruff lint clean
- [x] Ruff format applied
- [x] No forbidden imports
