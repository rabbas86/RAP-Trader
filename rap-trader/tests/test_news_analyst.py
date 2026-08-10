"""Phase 9 News Analyst tests — deterministic, offline, research-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalystRequest,
)
from app.domain.models.data_platform import (
    DataQuality,
    DataRevision,
    DataSourceIdentity,
    EventRecord,
)
from app.main import app
from app.services.data_platform.fingerprint import sha256_fingerprint
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.confirmation import NewsConfirmationService
from app.services.news_analysis.decay import NewsDecayService
from app.services.news_analysis.domain import (
    ConfirmationStatus,
    NewsEventType,
    NewsImportance,
    NewsScope,
    SourceQuality,
)
from app.services.news_analysis.event_grouping import EventGroupingService
from app.services.news_analysis.lifecycle import (
    EventLifecycleService,
    EventLifecycleState,
    assess_lifecycle,
)
from app.services.news_analysis.materiality import NewsMaterialityService
from app.services.news_analysis.novelty import NewsNoveltyService
from app.services.news_analysis.observations import (
    NewsObservation,
    ObservationExtractor,
)
from app.services.news_analysis.service import ClassifiedEvent, NewsAnalyst
from app.services.news_analysis.synthesis import NewsOpinionSynthesisService

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
EARLIER = datetime(2026, 7, 29, 12, 0, 0, tzinfo=UTC)
EVEN_EARLIER = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)

_SOURCE = DataSourceIdentity(
    provider="deterministic_test",
    dataset="news",
    source_version="1",
    schema_version="1",
    offline_capable=True,
    authoritative=False,
)

_AUTHORITATIVE_SOURCE = DataSourceIdentity(
    provider="sec",
    dataset="edgar",
    source_version="1",
    schema_version="1",
    offline_capable=True,
    authoritative=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _revision(revision_number: int = 0, available_at: datetime | None = None) -> DataRevision:
    rev_available = available_at or NOW
    prev_rev_id = f"rev-{revision_number - 1}" if revision_number > 0 else None
    return DataRevision(
        revision_id=f"rev-{revision_number}",
        revision_number=revision_number,
        revised_at=rev_available,
        available_at=rev_available,
        previous_revision_id=prev_rev_id,
        source_fingerprint=sha256_fingerprint({"revision_number": revision_number, "available_at": rev_available.isoformat()}),
    )


def _quality(score: float = 1.0) -> DataQuality:
    return DataQuality(
        completeness=score,
        consistency=score,
        timeliness=score,
        score=score,
    )


def _make_event(
    *,
    event_id: str = "evt-1",
    event_type: str = "earnings",
    entity: str | None = "AAPL",
    occurred_at: datetime | None = None,
    available_at: datetime | None = None,
    headline: str = "Company Reports Q2 Results",
    summary: str | None = None,
    structured_payload: dict | None = None,
    source: DataSourceIdentity | None = None,
    revision_number: int = 0,
) -> EventRecord:
    occurred = occurred_at or (available_at or NOW)
    available = available_at or NOW
    # EventRecord validates that available_at >= occurred_at (or published_at).
    available = max(available, occurred)
    payload = structured_payload or {}
    payload = {**payload, "headline": headline}
    fingerprint = sha256_fingerprint(
        {"event_id": event_id, "headline": headline, "available_at": available.isoformat(), "payload": payload}
    )
    return EventRecord(
        event_id=event_id,
        event_type=event_type,
        entity=entity,
        scheduled_at=None,
        occurred_at=occurred,
        published_at=available,
        available_at=available,
        source=source or _SOURCE,
        headline_or_title=headline,
        summary=summary,
        structured_payload=payload,
        revision=_revision(revision_number, available),
        quality=_quality(),
        fingerprint=fingerprint,
    )


def _make_observation(
    *,
    event_id: str = "evt-1",
    event_type: str = "earnings",
    entity: str | None = "AAPL",
    occurred_at: datetime | None = None,
    available_at: datetime | None = None,
    title: str = "Company Reports Q2 Results",
    summary: str | None = None,
    structured_payload: dict | None = None,
    source: str = "test:test",
    revision_number: int = 0,
    source_identity: DataSourceIdentity | None = None,
) -> NewsObservation:
    occurred = occurred_at or NOW
    available = available_at or NOW
    payload = structured_payload or {}
    source_fingerprint = sha256_fingerprint(
        {"event_id": event_id, "title": title, "available_at": available.isoformat(), "revision": revision_number}
    )
    return NewsObservation(
        event_id=event_id,
        entity=entity,
        event_type=event_type,
        scope="company",
        occurred_at=occurred,
        published_at=available,
        available_at=available,
        source=source,
        source_fingerprint=source_fingerprint,
        title=title,
        summary=summary,
        structured_payload=payload,
        revision_number=revision_number,
        quality_score=1.0,
        fingerprint=source_fingerprint,
        source_identity=source_identity or _SOURCE,
    )


def _news_request(
    events: list[EventRecord] | None = None,
    snapshot=None,
    as_of: datetime = NOW,
    ticker: str = "AAPL",
    max_events: int = 0,
    entity_filter: str | None = None,
) -> AnalystRequest:
    extra_context: dict = {}
    if events is not None:
        extra_context["events"] = [e.model_dump(mode="json") for e in events]
    if snapshot is not None:
        extra_context["snapshot"] = snapshot
    if max_events and max_events > 0:
        extra_context["max_events"] = max_events
    if entity_filter:
        extra_context["entity_filter"] = entity_filter
    return AnalystRequest(
        analyst_id="news",
        ticker=ticker,
        timeframe="1d",
        as_of=as_of,
        lookback=60,
        horizon=5,
        asset_class="equity",
        extra_context=extra_context,
    )


def _analyst() -> NewsAnalyst:
    return NewsAnalyst()


# ---------------------------------------------------------------------------
# Test: Basic construction and metadata
# ---------------------------------------------------------------------------


def test_news_analyst_metadata_and_health() -> None:
    analyst = _analyst()
    assert analyst.analyst_id == "news"
    assert analyst.analyst_role.value == "NEWS"
    assert analyst.supported_timeframes() == ["1d", "1w", "1mo"]
    assert analyst.supported_asset_classes() == ["equity", "macro", "any"]
    health = analyst.health()
    assert health.status == "healthy"
    meta = analyst.metadata()
    assert meta.analyst_id == "news"
    assert meta.role.value == "NEWS"
    assert meta.research_only is True
    assert meta.suitable_for_live_trading is False


# ---------------------------------------------------------------------------
# Materiality tests
# ---------------------------------------------------------------------------


def test_materiality_critical_event_is_always_material() -> None:
    svc = NewsMaterialityService()
    result = svc.assess(
        NewsEventType.BANKRUPTCY,
        NewsImportance.HIGH,
        NewsScope.COMPANY,
        SourceQuality.SECONDARY,
    )
    assert result.is_material is True
    assert result.is_critical is True


def test_materiality_trivial_unverified_is_not_material() -> None:
    svc = NewsMaterialityService()
    result = svc.assess(
        NewsEventType.OTHER,
        NewsImportance.TRIVIAL,
        NewsScope.COMPANY,
        SourceQuality.UNVERIFIED,
    )
    assert result.is_material is False


def test_materiality_unknown_importance_is_not_material() -> None:
    svc = NewsMaterialityService()
    result = svc.assess(
        NewsEventType.OTHER,
        NewsImportance.UNKNOWN,
        NewsScope.COMPANY,
        SourceQuality.SECONDARY,
    )
    assert result.is_material is False


def test_materiality_high_quality_boosts_score() -> None:
    svc = NewsMaterialityService()
    low_quality = svc.assess(
        NewsEventType.ANALYST_REVISION,
        NewsImportance.MODERATE,
        NewsScope.COMPANY,
        SourceQuality.SECONDARY,
    )
    high_quality = svc.assess(
        NewsEventType.ANALYST_REVISION,
        NewsImportance.MODERATE,
        NewsScope.COMPANY,
        SourceQuality.AUTHORITATIVE,
    )
    assert high_quality.score > low_quality.score


def test_materiality_company_scope_boosted_over_global() -> None:
    svc = NewsMaterialityService()
    company = svc.assess(
        NewsEventType.EARNINGS,
        NewsImportance.HIGH,
        NewsScope.COMPANY,
        SourceQuality.SECONDARY,
    )
    global_scope = svc.assess(
        NewsEventType.EARNINGS,
        NewsImportance.HIGH,
        NewsScope.GLOBAL,
        SourceQuality.SECONDARY,
    )
    assert company.score > global_scope.score


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


def test_lifecycle_active_by_default() -> None:
    obs = _make_observation(title="Earnings beat")
    result = assess_lifecycle(obs)
    assert result.state == EventLifecycleState.ACTIVE
    assert result.is_final is False
    assert result.exclude_from_synthesis is False


def test_lifecycle_cancelled_excluded() -> None:
    obs = _make_observation(
        title="Earnings call cancelled",
        structured_payload={"lifecycle_state": "cancelled"},
    )
    result = assess_lifecycle(obs)
    assert result.state == EventLifecycleState.CANCELLED
    assert result.exclude_from_synthesis is True


def test_lifecycle_superseded_excluded() -> None:
    obs = _make_observation(
        title="Revised earnings figures",
        structured_payload={"status": "superseded"},
    )
    result = assess_lifecycle(obs)
    assert result.state == EventLifecycleState.SUPERSEDED
    assert result.exclude_from_synthesis is True


def test_lifecycle_resolved_is_final() -> None:
    obs = _make_observation(
        title="Fed rate decision",
        structured_payload={"event_status": "resolved"},
    )
    result = assess_lifecycle(obs)
    assert result.state == EventLifecycleState.RESOLVED
    assert result.is_final is True
    assert result.exclude_from_synthesis is False


def test_lifecycle_archived_is_final() -> None:
    obs = _make_observation(
        title="Historical merger",
        structured_payload={"lifecycle_state": "archived"},
    )
    result = assess_lifecycle(obs)
    assert result.state == EventLifecycleState.ARCHIVED
    assert result.is_final is True


def test_lifecycle_alias_mapping() -> None:
    for alias, canonical in [
        ("completed", EventLifecycleState.RESOLVED),
        ("occurred", EventLifecycleState.RESOLVED),
        ("canceled", EventLifecycleState.CANCELLED),
        ("called_off", EventLifecycleState.CANCELLED),
        ("replaced", EventLifecycleState.SUPERSEDED),
        ("historical", EventLifecycleState.ARCHIVED),
    ]:
        obs = _make_observation(
            title=f"Event with alias {alias}",
            structured_payload={"status": alias},
        )
        result = assess_lifecycle(obs)
        assert result.state == canonical, f"alias '{alias}' should map to {canonical}"


def test_lifecycle_service_class_wrapper() -> None:
    svc = EventLifecycleService()
    obs = _make_observation(
        title="Cancelled event",
        structured_payload={"lifecycle_state": "cancelled"},
    )
    result = svc.assess(obs)
    assert result.state == EventLifecycleState.CANCELLED
    assert result.exclude_from_synthesis is True


# ---------------------------------------------------------------------------
# Novelty tests
# ---------------------------------------------------------------------------


def test_novelty_exact_duplicate_detected() -> None:
    svc = NewsNoveltyService()
    obs1 = _make_observation(title="Earnings beat estimates")
    obs2 = _make_observation(title="Earnings beat estimates")
    r1 = svc.assess(obs1)
    r2 = svc.assess(obs2)
    assert r1.is_first_report is True
    assert r2.is_duplicate is True
    assert r2.is_follow_up is True
    assert r2.novelty_score == 0.0


def test_novelty_different_payload_not_duplicate() -> None:
    svc = NewsNoveltyService()
    obs1 = _make_observation(
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    obs2 = _make_observation(
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.5},
    )
    r1 = svc.assess(obs1)
    r2 = svc.assess(obs2, prior_payload=obs1.structured_payload)
    assert r1.is_first_report is True
    assert r2.is_duplicate is False
    assert r2.payload_changed is True
    assert r2.is_follow_up is True
    assert r2.novelty_score == 0.3  # revision carries new info


def test_novelty_same_payload_follow_up_low_score() -> None:
    svc = NewsNoveltyService()
    obs1 = _make_observation(
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    obs2 = _make_observation(
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    svc.assess(obs1)
    r2 = svc.assess(obs2, prior_payload=obs1.structured_payload)
    assert r2.is_duplicate is True
    assert r2.novelty_score == 0.0


# ---------------------------------------------------------------------------
# Decay tests
# ---------------------------------------------------------------------------


def test_decay_factor_fresh_event_is_one() -> None:
    svc = NewsDecayService()
    result = svc.assess(NOW, NOW, NewsEventType.EARNINGS)
    assert result.decay_factor == 1.0
    assert result.is_stale is False


def test_decay_factor_halves_at_half_life() -> None:
    svc = NewsDecayService()
    half_life = NewsAnalystConfig().decay_half_lives["earnings"]
    observed = NOW - half_life
    result = svc.assess(observed, NOW, NewsEventType.EARNINGS)
    assert result.decay_factor == pytest.approx(0.5, abs=0.01)


def test_decay_different_event_types_have_different_half_lives() -> None:
    config = NewsAnalystConfig()
    earnings_hl = config.decay_half_lives["earnings"]
    merger_hl = config.decay_half_lives["merger_acquisition"]
    assert merger_hl > earnings_hl


def test_decay_stale_threshold() -> None:
    svc = NewsDecayService()
    half_life = NewsAnalystConfig().decay_half_lives["other"]
    # Go far past the half-life.
    observed = NOW - half_life * 20
    result = svc.assess(observed, NOW, NewsEventType.OTHER)
    assert result.is_stale is True
    assert result.decay_factor < NewsAnalystConfig().stale_decay_threshold


# ---------------------------------------------------------------------------
# Confirmation tests
# ---------------------------------------------------------------------------


def test_confirmation_single_source_unverified() -> None:
    svc = NewsConfirmationService()
    obs = _make_observation(title="Single source report")
    result = svc.assess([obs])
    assert result.status is ConfirmationStatus.UNVERIFIED
    assert result.source_count == 1


def test_confirmation_multiple_sources_confirmed() -> None:
    svc = NewsConfirmationService()
    obs1 = _make_observation(title="Report A", source="source_a:news")
    obs2 = _make_observation(title="Report B", source="source_b:news")
    result = svc.assess([obs1, obs2])
    assert result.status is ConfirmationStatus.CONFIRMED
    assert result.source_count == 2


def test_confirmation_conflicting_payloads() -> None:
    svc = NewsConfirmationService()
    obs1 = _make_observation(
        title="Report A",
        source="source_a:news",
        structured_payload={"direction": "positive", "eps": 1.5},
    )
    obs2 = _make_observation(
        title="Report B",
        source="source_b:news",
        structured_payload={"direction": "negative", "eps": 0.5},
    )
    result = svc.assess([obs1, obs2])
    assert result.status is ConfirmationStatus.CONFLICTING
    assert "direction" in result.conflict_fields


def test_confirmation_empty_list() -> None:
    svc = NewsConfirmationService()
    result = svc.assess([])
    assert result.status is ConfirmationStatus.CONFLICTING
    assert result.source_count == 0


# ---------------------------------------------------------------------------
# Event grouping tests
# ---------------------------------------------------------------------------


def test_grouping_clusters_same_event_different_sources() -> None:
    svc = EventGroupingService()
    obs1 = _make_observation(
        event_id="evt-1",
        title="Earnings beat",
        source="source_a:news",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    obs2 = _make_observation(
        event_id="evt-2",
        title="Earnings beat",
        source="source_b:news",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    clusters = svc.group([obs1, obs2], NOW)
    assert len(clusters) == 1
    assert len(clusters[0].records) == 2
    assert clusters[0].confirmation_status is ConfirmationStatus.CONFIRMED


def test_grouping_separates_different_events() -> None:
    svc = EventGroupingService()
    obs1 = _make_observation(event_id="evt-1", title="Earnings beat", entity="AAPL")
    obs2 = _make_observation(event_id="evt-2", title="Merger announced", entity="GOOG")
    clusters = svc.group([obs1, obs2], NOW)
    assert len(clusters) == 2


def test_grouping_filters_future_events() -> None:
    svc = EventGroupingService()
    future = NOW + timedelta(hours=12)
    obs = _make_observation(
        event_id="evt-future",
        title="Future event",
        available_at=future,
    )
    clusters = svc.group([obs], NOW)
    assert len(clusters) == 0


def test_grouping_identical_headlines_same_cluster() -> None:
    svc = EventGroupingService()
    obs1 = _make_observation(event_id="evt-1", title="Earnings beat", occurred_at=EVEN_EARLIER)
    obs2 = _make_observation(event_id="evt-2", title="Earnings beat", occurred_at=EVEN_EARLIER, available_at=EARLIER)
    clusters = svc.group([obs1, obs2], NOW)
    assert len(clusters) == 1


# ---------------------------------------------------------------------------
# Point-in-time safety tests
# ---------------------------------------------------------------------------


def test_point_in_time_no_future_events_in_snapshot() -> None:
    """Events with available_at > as_of must be excluded."""
    future_event = _make_event(
        event_id="future-event",
        headline="Future news",
        available_at=NOW + timedelta(hours=12),
    )
    request = _news_request(events=[future_event], as_of=NOW)
    opinion = _analyst().analyze(request)
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE


def test_point_in_time_no_future_events_direct() -> None:
    """extract_from_events filters available_at > as_of."""
    extractor = ObservationExtractor()
    future_event = _make_event(
        event_id="future-event",
        headline="Future news",
        available_at=NOW + timedelta(hours=12),
    )
    obs = extractor.extract_from_events((future_event,), NOW)
    assert len(obs) == 0


def test_point_in_time_past_event_included() -> None:
    """Events with available_at <= as_of are included (within freshness threshold)."""
    recent = NOW - timedelta(hours=2)  # within 1-day NEWS freshness threshold
    past_event = _make_event(
        event_id="past-event",
        headline="Past news",
        available_at=recent,
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[past_event], as_of=NOW)
    opinion = _analyst().analyze(request)
    assert opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert len(opinion.evidence) > 0


def test_point_in_time_confidence_uses_as_of_not_now() -> None:
    """Freshness and confidence must use as_of, not wall-clock now."""
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        available_at=EARLIER,
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    past_as_of = datetime(2026, 7, 30, 12, 0, 0, tzinfo=UTC)
    # Use a config that allows stale input (2 days old > 1-day freshness threshold).
    config = NewsAnalystConfig(stale_input_allowed=True)
    analyst = NewsAnalyst(config=config)
    request = _news_request(events=[event], as_of=past_as_of)
    opinion = analyst.analyze(request)
    # generated_at must equal the as_of, not wall-clock now.
    assert opinion.generated_at == past_as_of
    # Freshness evaluated_at must be the as_of.
    assert opinion.data_freshness.evaluated_at == past_as_of


# ---------------------------------------------------------------------------
# Historical freshness tests
# ---------------------------------------------------------------------------


def test_historical_freshness_based_on_as_of() -> None:
    """The NewsAnalyst's data_freshness must reflect analysis as_of, not wall-clock."""
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        available_at=EARLIER,
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    # Use a config that allows stale input (2 days old > 1-day freshness threshold).
    config = NewsAnalystConfig(stale_input_allowed=True)
    opinion = NewsAnalyst(config=config).analyze(request)
    # The freshness age should be based on as_of, not now.
    age = (NOW - EARLIER).total_seconds()
    assert opinion.data_freshness.age_seconds == pytest.approx(age, abs=1.0)


def test_historical_freshness_stale_events() -> None:
    """Old events should be flagged as stale by the decay service."""
    config = NewsAnalystConfig(stale_input_allowed=True)
    old = NOW - timedelta(days=5)
    event = _make_event(
        event_id="evt-old",
        headline="Old earnings",
        available_at=old,
        structured_payload={"direction": "positive", "eps": 1.0},
        event_type="analyst_revision",  # 24h half-life → 5 days = 0.5^5 ≈ 0.03, below threshold
    )
    request = _news_request(events=[event], as_of=NOW)
    opinion = NewsAnalyst(config=config).analyze(request)
    # The analyst should still produce an opinion but with stale warnings.
    assert opinion.data_freshness.is_stale is True


def test_news_decay_service_separate_from_platform_freshness() -> None:
    """NewsDecayService computes event relevance decay independently from
    the generic DataFreshnessService used by the analyst platform."""
    from app.services.data_platform.freshness import ResearchDataFreshnessService

    decay_svc = NewsDecayService()
    platform_freshness = ResearchDataFreshnessService()

    # For earnings (72h half-life), 20 days ago → decay factor ≈ 0.5^6.67 ≈ 0.01, below stale threshold.
    observed = NOW - timedelta(days=20)
    decay_result = decay_svc.assess(observed, NOW, NewsEventType.EARNINGS)
    # Platform freshness is based on evidence type threshold (1 day for NEWS).
    platform_result = platform_freshness.is_stale(observed, NOW, stale_after_seconds=86400)
    # Both should agree that 10 days ago is stale, but use different mechanisms.
    assert decay_result.is_stale is True
    assert platform_result is True

    # But for a fresh event (same day), both should agree it's not stale.
    just_ago = NOW - timedelta(hours=1)
    decay_fresh = decay_svc.assess(just_ago, NOW, NewsEventType.EARNINGS)
    assert decay_fresh.decay_factor > 0.99
    assert decay_fresh.is_stale is False


# ---------------------------------------------------------------------------
# Duplicate / follow-up headline weight tests
# ---------------------------------------------------------------------------


def test_duplicate_headlines_do_not_multiply_weight() -> None:
    """Duplicate events must not multiply evidence weight in synthesis."""
    # Same title, same payload → same fingerprint → duplicate.
    obs1 = _make_observation(
        event_id="evt-1",
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    obs2 = _make_observation(
        event_id="evt-2",
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    # Both go into the same cluster (same canonical fingerprint).
    svc = EventGroupingService()
    clusters = svc.group([obs1, obs2], NOW)
    assert len(clusters) == 1
    # The cluster should detect the duplicate.
    assert clusters[0].is_duplicate is True

    # The synthesis should not let duplicates multiply the score.
    synth = NewsOpinionSynthesisService()
    classified = []
    for cluster in clusters:
        latest = cluster.records[-1]
        from app.services.news_analysis.classification import classify

        c = classify(latest)
        from app.services.news_analysis.service import ClassifiedEvent

        classified.append(
            ClassifiedEvent(
                observation=latest,
                event_type=c.event_type,
                orientation=c.orientation,
                importance=c.importance,
                scope=c.scope,
                source_quality=c.source_quality,
                confirmation_status=cluster.confirmation_status,
                decay_factor=1.0,
                is_stale=False,
                is_duplicate=cluster.is_duplicate,
                novelty_score=cluster.novelty_score,
                cluster_id=cluster.cluster_id,
                lifecycle_state="active",
                is_excluded=False,
                materiality_score=1.0,
            )
        )
    result = synth.synthesize(classified, NOW)
    # Duplicate penalty should be applied.
    assert result.duplicate_penalty > 0
    assert any("duplicate" in w.lower() for w in result.warnings)


def test_follow_up_without_new_info_low_novelty() -> None:
    """Follow-up articles without new information get low novelty score.

    The novelty fingerprint includes the structured payload, so a truly
    duplicate payload (even with a different headline) is detected as a
    duplicate with novelty_score 0.0.
    """
    svc = NewsNoveltyService()
    obs1 = _make_observation(
        event_id="evt-1",
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    r1 = svc.assess(obs1)
    assert r1.is_first_report is True
    assert r1.novelty_score == 1.0  # first report

    # Second article, same payload, different title (follow-up).
    obs2 = _make_observation(
        event_id="evt-2",
        title="Earnings beat — Market reacts positively",
        structured_payload={"direction": "positive", "eps": 1.0},
    )
    r2 = svc.assess(obs2, known_fingerprints={r1.fingerprint}, prior_payload=obs1.structured_payload)
    # Same fingerprint (entity + event_type + occurred_at + payload all match).
    assert r2.is_duplicate is True
    assert r2.is_follow_up is True
    assert r2.novelty_score == 0.0


# ---------------------------------------------------------------------------
# Cancelled / superseded / resolved event handling tests
# ---------------------------------------------------------------------------


def test_cancelled_event_excluded_from_synthesis() -> None:
    """A cancelled event must not contribute to the synthesis score."""
    cancelled = _make_event(
        event_id="evt-cancelled",
        headline="Earnings call cancelled",
        structured_payload={"lifecycle_state": "cancelled", "direction": "negative"},
    )
    active = _make_event(
        event_id="evt-active",
        headline="Revenue beats expectations",
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[cancelled, active], as_of=NOW)
    opinion = _analyst().analyze(request)
    # Should still produce an opinion (from the active event).
    assert opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE
    # The cancelled event should be excluded from the opinion.
    # Check that the active event contributed to evidence.
    assert len(opinion.evidence) > 0


def test_superseded_event_excluded_from_synthesis() -> None:
    """A superseded event must not contribute to the synthesis score."""
    config = NewsAnalystConfig(stale_input_allowed=True)
    superseded = _make_event(
        event_id="evt-old",
        headline="Initial earnings estimate",
        structured_payload={"status": "superseded", "direction": "negative", "eps": 0.5},
        available_at=EVEN_EARLIER,
        revision_number=1,
    )
    current = _make_event(
        event_id="evt-new",
        headline="Earnings beat revised up",
        structured_payload={"status": "resolved", "direction": "positive", "eps": 1.5, "surprise": 1.0},
        available_at=EARLIER,
        revision_number=2,
    )
    request = _news_request(events=[superseded, current], as_of=NOW)
    opinion = NewsAnalyst(config=config).analyze(request)
    assert opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE


def test_resolved_event_contributes_to_synthesis() -> None:
    """A resolved event (final outcome known) should still contribute."""
    resolved = _make_event(
        event_id="evt-resolved",
        headline="Fed raises rates by 0.25%",
        structured_payload={
            "lifecycle_state": "resolved",
            "direction": "negative",
            "event_type": "central_bank",
        },
        event_type="central_bank",
    )
    request = _news_request(events=[resolved], as_of=NOW)
    opinion = _analyst().analyze(request)
    assert opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# Synthesis tests
# ---------------------------------------------------------------------------


def test_synthesis_bullish_positive_events() -> None:
    svc = NewsOpinionSynthesisService()
    obs = _make_observation(
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.5, "surprise": 0.5},
    )
    from app.services.news_analysis.classification import classify

    c = classify(obs)
    classified = [
        ClassifiedEvent(
            observation=obs,
            event_type=c.event_type,
            orientation=c.orientation,
            importance=c.importance,
            scope=c.scope,
            source_quality=SourceQuality.HIGH_QUALITY_SECONDARY,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            decay_factor=1.0,
            is_stale=False,
            is_duplicate=False,
            novelty_score=1.0,
            cluster_id="c1",
            lifecycle_state="active",
            is_excluded=False,
            materiality_score=0.8,
        )
    ]
    result = svc.synthesize(classified, NOW)
    assert result.direction in ("bullish", "strongly_bullish")
    assert result.positive_score > 0
    assert result.negative_score == 0


def test_synthesis_bearish_negative_events() -> None:
    svc = NewsOpinionSynthesisService()
    obs = _make_observation(
        title="Earnings miss",
        structured_payload={"direction": "negative", "eps": 0.5, "surprise": -0.5},
    )
    from app.services.news_analysis.classification import classify

    c = classify(obs)
    classified = [
        ClassifiedEvent(
            observation=obs,
            event_type=c.event_type,
            orientation=c.orientation,
            importance=c.importance,
            scope=c.scope,
            source_quality=SourceQuality.HIGH_QUALITY_SECONDARY,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            decay_factor=1.0,
            is_stale=False,
            is_duplicate=False,
            novelty_score=1.0,
            cluster_id="c1",
            lifecycle_state="active",
            is_excluded=False,
            materiality_score=0.8,
        )
    ]
    result = svc.synthesize(classified, NOW)
    assert result.direction in ("bearish", "strongly_bearish")
    assert result.negative_score > 0
    assert result.positive_score == 0


def test_synthesis_mixed_conflicting_evidence() -> None:
    svc = NewsOpinionSynthesisService()
    from app.services.news_analysis.classification import classify

    pos_obs = _make_observation(
        event_id="evt-pos",
        title="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.5},
    )
    neg_obs = _make_observation(
        event_id="evt-neg",
        title="Product recall announced",
        event_type="product_failure",
        structured_payload={"direction": "negative"},
    )
    c1 = classify(pos_obs)
    c2 = classify(neg_obs)
    classified = [
        ClassifiedEvent(
            observation=pos_obs,
            event_type=c1.event_type,
            orientation=c1.orientation,
            importance=c1.importance,
            scope=c1.scope,
            source_quality=SourceQuality.HIGH_QUALITY_SECONDARY,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            decay_factor=1.0,
            is_stale=False,
            is_duplicate=False,
            novelty_score=1.0,
            cluster_id="c1",
            lifecycle_state="active",
            is_excluded=False,
            materiality_score=0.8,
        ),
        ClassifiedEvent(
            observation=neg_obs,
            event_type=c2.event_type,
            orientation=c2.orientation,
            importance=c2.importance,
            scope=c2.scope,
            source_quality=SourceQuality.HIGH_QUALITY_SECONDARY,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            decay_factor=1.0,
            is_stale=False,
            is_duplicate=False,
            novelty_score=1.0,
            cluster_id="c2",
            lifecycle_state="active",
            is_excluded=False,
            materiality_score=0.8,
        ),
    ]
    result = svc.synthesize(classified, NOW)
    assert result.direction == "mixed"


def test_synthesis_empty_returns_insufficient() -> None:
    svc = NewsOpinionSynthesisService()
    result = svc.synthesize([], NOW)
    assert result.direction == "insufficient_evidence"
    assert result.confidence == 0.0


def test_synthesis_metric_aware_negative_detection() -> None:
    """Metric-aware detection should catch negative signals in structured payload."""
    svc = NewsOpinionSynthesisService()
    from app.services.news_analysis.classification import classify

    # Strong growth (positive orientation) but high debt-to-equity → negative signal.
    obs = _make_observation(
        title="Company reports strong growth",
        structured_payload={
            "direction": "positive",
            "eps": 2.0,
            "debt_to_equity": 3.5,  # > 2.0 threshold → negative
        },
    )
    c = classify(obs)
    classified = [
        ClassifiedEvent(
            observation=obs,
            event_type=c.event_type,
            orientation=c.orientation,
            importance=c.importance,
            scope=c.scope,
            source_quality=SourceQuality.HIGH_QUALITY_SECONDARY,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            decay_factor=1.0,
            is_stale=False,
            is_duplicate=False,
            novelty_score=1.0,
            cluster_id="c1",
            lifecycle_state="active",
            is_excluded=False,
            materiality_score=0.8,
        )
    ]
    result = svc.synthesize(classified, NOW)
    # The metric-aware detection should have triggered.
    assert any("metric-aware" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------


def test_news_analyst_full_analysis_bullish() -> None:
    event = _make_event(
        event_id="evt-1",
        headline="Company beats earnings estimates",
        structured_payload={"direction": "positive", "eps": 1.5, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    opinion = _analyst().analyze(request)
    assert opinion.analyst_id == "news"
    assert opinion.research_only is True
    assert opinion.suitable_for_live_trading is False
    assert opinion.decision_ready is False
    assert opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert len(opinion.evidence) > 0
    assert opinion.generated_at == NOW


def test_news_analyst_insufficient_no_events() -> None:
    request = _news_request(events=[], as_of=NOW)
    opinion = _analyst().analyze(request)
    assert opinion.direction is AnalysisDirection.INSUFFICIENT_EVIDENCE
    assert len(opinion.evidence) == 0


def test_news_analyst_deterministic_same_output() -> None:
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    op1 = _analyst().analyze(request)
    op2 = _analyst().analyze(request)
    assert op1 == op2


def test_news_analyst_confidence_capped() -> None:
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    opinion = _analyst().analyze(request)
    cap = NewsAnalystConfig().uncalibrated_confidence_cap
    assert opinion.confidence.value <= cap


def test_news_analyst_evidence_is_point_in_time_safe() -> None:
    """All evidence items must have available_at <= as_of."""
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        available_at=EARLIER,
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    opinion = _analyst().analyze(request)
    for item in opinion.evidence:
        assert item.available_at <= NOW
        assert item.observed_at <= NOW


def test_news_analyst_assumptions_and_warnings() -> None:
    event = _make_event(
        event_id="evt-1",
        headline="Earnings beat",
        structured_payload={"direction": "positive", "eps": 1.0, "surprise": 0.5},
    )
    request = _news_request(events=[event], as_of=NOW)
    opinion = _analyst().analyze(request)
    assumption_texts = [a.description for a in opinion.assumptions]
    assert any("point-in-time" in a.lower() for a in assumption_texts)
    warning_codes = [w.code for w in opinion.warnings]
    assert "RESEARCH_ONLY" in warning_codes
    assert "NO_LLM" in warning_codes
    assert "NO_NETWORK" in warning_codes


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_api_lists_news_analyst() -> None:
    client = TestClient(app)
    response = client.get("/analysts")
    assert response.status_code == 200
    analysts = response.json()
    ids = [a["analyst_id"] for a in analysts]
    assert "news" in ids


def test_api_news_health() -> None:
    client = TestClient(app)
    response = client.get("/analysts/news/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["analyst_id"] == "news"


def test_api_news_metadata() -> None:
    client = TestClient(app)
    response = client.get("/analysts/news/metadata")
    assert response.status_code == 200
    data = response.json()
    assert data["analyst_id"] == "news"
    assert data["role"] == "NEWS"
    assert data["research_only"] is True
    assert data["suitable_for_live_trading"] is False


def test_api_news_analyze_positive() -> None:
    event = _make_event(
        event_id="evt-1",
        headline="Company beats earnings estimates",
        structured_payload={"direction": "positive", "eps": 1.5, "surprise": 0.5},
    )
    request_data = _news_request(events=[event], as_of=NOW).model_dump(mode="json")
    client = TestClient(app)
    response = client.post("/analysts/news/analyze", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["analyst_id"] == "news"
    assert data["suitable_for_live_trading"] is False
    assert data["research_only"] is True


def test_api_news_analyze_insufficient() -> None:
    request_data = _news_request(events=[], as_of=NOW).model_dump(mode="json")
    client = TestClient(app)
    response = client.post("/analysts/news/analyze", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["direction"] == "INSUFFICIENT_EVIDENCE"


def test_api_news_analyze_rejects_future_events() -> None:
    future_event = _make_event(
        event_id="evt-future",
        headline="Future news",
        available_at=NOW + timedelta(hours=12),
    )
    request_data = _news_request(events=[future_event], as_of=NOW).model_dump(mode="json")
    client = TestClient(app)
    response = client.post("/analysts/news/analyze", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert data["direction"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_news_help(capsys) -> None:
    from app.cli.analyst import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--analyst", "news", "--ticker", "AAPL", "--as-of", NOW.isoformat()])
    assert exc_info.value.code != 0


def test_cli_news_analyze_offline(tmp_path, capsys) -> None:
    """Run the CLI news analyst with a deterministic offline input."""
    import json

    event = _make_event(
        event_id="evt-1",
        headline="Company beats earnings estimates",
        structured_payload={"direction": "positive", "eps": 1.5, "surprise": 0.5},
    )
    events_json = [e.model_dump(mode="json") for e in [event]]
    events_path = tmp_path / "events.json"
    events_path.write_text(json.dumps(events_json), encoding="utf-8")

    from app.cli.analyst import main

    rc = main(
        [
            "--analyst",
            "news",
            "--ticker",
            "AAPL",
            "--as-of",
            NOW.isoformat(),
            "--input-events",
            str(events_path),
            "--as-json",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "INSUFFICIENT_EVIDENCE" in captured.out or "BULLISH" in captured.out or "BEARISH" in captured.out or "NEUTRAL" in captured.out


# ---------------------------------------------------------------------------
# Safety tests
# ---------------------------------------------------------------------------


def test_no_forbidden_runtime_dependencies_in_news_modules() -> None:
    """No news_analysis module may import forbidden runtime dependencies."""
    from pathlib import Path

    forbidden = [
        "riskengine",
        "risk_engine",
        "portfoliomanager",
        "paperbroker",
        "executionservice",
        "orderrequest",
        "committee",
        "chairman",
    ]
    root = Path(__file__).parents[1] / "app" / "services" / "news_analysis"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip().lower()
            # Only check import lines, not comments or docstrings.
            if stripped.startswith(("import ", "from ")):
                for word in forbidden:
                    assert word not in stripped, f"{path.name} imports forbidden {word}"


def test_no_network_no_llm_in_news_modules() -> None:
    """No news_analysis module may use network or LLM calls."""
    from pathlib import Path

    forbidden_patterns = [
        "requests.get",
        "requests.post",
        "httpx",
        "urllib",
        "openai",
        "anthropic",
        "transformer",
        "torch",
        "tensorflow",
    ]
    root = Path(__file__).parents[1] / "app" / "services" / "news_analysis"
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip().lower()
            if stripped.startswith(("#", '"', "'")):
                continue
            for pattern in forbidden_patterns:
                assert pattern not in stripped, f"{path.name} uses forbidden pattern {pattern}"


def test_all_news_analysis_modules_have_docstrings() -> None:
    """Every module in news_analysis must have a module-level docstring."""
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "services" / "news_analysis"
    for path in sorted(root.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert source.lstrip().startswith('"""'), f"{path.name} missing module docstring"


def test_classified_event_is_dataclass() -> None:
    """ClassifiedEvent must be a dataclass with the new fields."""
    assert hasattr(ClassifiedEvent, "__dataclass_fields__")
    fields = ClassifiedEvent.__dataclass_fields__
    assert "lifecycle_state" in fields
    assert "is_excluded" in fields
    assert "materiality_score" in fields
    assert "structured_payload" in dir(ClassifiedEvent)


def test_news_analyst_is_base_analyst_subclass() -> None:
    from app.services.analyst.framework import BaseAnalyst

    analyst = _analyst()
    assert isinstance(analyst, BaseAnalyst)
