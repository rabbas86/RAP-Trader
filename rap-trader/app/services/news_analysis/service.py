"""Phase 9 News Analyst — deterministic, research-only, offline.

Consumes normalized point-in-time event/news records from the Phase 8A Unified
Research Data Platform (provided via ``AnalystRequest.extra_context``) and
produces a deterministic ``AnalystOpinion`` through the Phase 5 / 7.5 lifecycle.

The News Analyst never fetches external data, never generates trades, never
allocates capital, and never calls RiskEngine, PortfolioManager, or
InvestmentCommittee.

Point-in-time safety is enforced: only events where ``available_at <=
analysis as_of`` are used.  No later corrections, confirmations, revisions, or
regulatory filings leaked from the future are ever consumed in historical
analysis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisWarning,
    AnalystError,
    AnalystOpinion,
    AnalystRequest,
    Assumption,
    EvidenceItem,
)
from app.domain.models.data_platform import EventRecord, ResearchDataSnapshot
from app.services.analyst.service import Analyst
from app.services.news_analysis.classification import (
    classify_event_type,
    classify_importance,
    classify_orientation,
    classify_scope,
)
from app.services.news_analysis.config import NewsAnalystConfig
from app.services.news_analysis.confirmation import NewsConfirmationService
from app.services.news_analysis.decay import NewsDecayService
from app.services.news_analysis.domain import (
    ConfirmationStatus,
    NewsEventType,
    NewsImportance,
    NewsOrientation,
    NewsScope,
    SourceQuality,
)
from app.services.news_analysis.event_grouping import EventCluster, EventGroupingService
from app.services.news_analysis.evidence import NewsEvidenceFactory
from app.services.news_analysis.lifecycle import EventLifecycleService
from app.services.news_analysis.materiality import NewsMaterialityService
from app.services.news_analysis.novelty import NewsNoveltyService
from app.services.news_analysis.observations import NewsObservation, ObservationExtractor
from app.services.news_analysis.source_quality import SourceQualityService
from app.services.news_analysis.synthesis import NewsOpinionSynthesisService


@dataclass(frozen=True)
class ClassifiedEvent:
    """A news observation with its full classification and computed scores."""

    observation: NewsObservation
    event_type: NewsEventType
    orientation: NewsOrientation
    importance: NewsImportance
    scope: NewsScope
    source_quality: SourceQuality
    confirmation_status: ConfirmationStatus
    decay_factor: float
    is_stale: bool
    is_duplicate: bool
    novelty_score: float
    cluster_id: str
    lifecycle_state: str
    is_excluded: bool
    materiality_score: float

    @property
    def structured_payload(self) -> dict[str, Any]:
        return self.observation.structured_payload


class NewsAnalyst(Analyst):
    """Deterministic, offline, research-only news event analyst."""

    display_name = "News Analyst"
    description = "Deterministic, offline news analyst using the Phase 8A Research Data Platform"
    health_detail = "deterministic offline news rules over ResearchDataSnapshot"

    def __init__(self, config: NewsAnalystConfig | None = None) -> None:
        self.config = config or NewsAnalystConfig()
        self._initialize_framework(self.config.uncalibrated_confidence_cap)
        self.extractor = ObservationExtractor(self.config)
        self.source_quality_service = SourceQualityService(self.config)
        self.novelty_service = NewsNoveltyService(self.config)
        self.decay_service = NewsDecayService(self.config)
        self.confirmation_service = NewsConfirmationService()
        self.grouping_service = EventGroupingService(self.config)
        self.evidence_factory = NewsEvidenceFactory(self.config)
        self.synthesizer = NewsOpinionSynthesisService(self.config)
        self.materiality_service = NewsMaterialityService(self.config)
        self.lifecycle_service = EventLifecycleService()

    def supported_timeframes(self) -> list[str]:
        return ["1d", "1w", "1mo"]

    def supported_asset_classes(self) -> list[str]:
        return ["equity", "macro", "any"]

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        as_of = request.as_of

        # --- Extract observations with point-in-time safety ----------------
        observations = self._extract(request)

        # --- Set source quality on each observation for cluster aggregation ---
        for obs in observations:
            if obs.source_identity is not None:
                object.__setattr__(obs, "source_quality", self.source_quality_service.assess(obs.source_identity))

        # --- Group into event clusters (point-in-time filtered) ------------
        clusters = self.grouping_service.group(observations, as_of)

        # --- If no events, return insufficient ----------------------------
        if not clusters:
            return self._insufficient(
                request,
                "Insufficient news events in snapshot for analysis",
                warnings=[AnalysisWarning(code="INSUFFICIENT_DATA", message="No news events available")],
                limitations=[
                    AnalysisLimitation(
                        code="NO_NEWS_DATA",
                        message="News analyst requires news events in a ResearchDataSnapshot",
                    ),
                    AnalysisLimitation(
                        code="NO_TRADING",
                        message="This opinion does not generate trades or allocate capital",
                    ),
                ],
                source="news",
            )

        # --- Classify each cluster ----------------------------------------
        classified = self._classify(clusters, as_of)

        # --- Build evidence from classified clusters ----------------------
        evidence = self._build_evidence(clusters, classified, as_of)

        # --- Validate evidence (point-in-time safety) -------------------
        try:
            self.validator.validate(evidence, as_of, allow_stale=self.config.stale_input_allowed)
        except AnalystError as exc:
            return self._insufficient(request, exc.safe_message)

        # --- Synthesize direction -----------------------------------------
        synthesis = self.synthesizer.synthesize(classified, as_of)

        # --- Build opinion ------------------------------------------------
        material = f"news|{request.ticker}|{as_of.isoformat()}|{synthesis.direction}|{','.join(e.evidence_id for e in evidence)}"
        opinion_id = str(uuid5(NAMESPACE_URL, material))
        observed = max(e.observed_at for e in evidence)
        available = max(e.available_at for e in evidence)
        confidence = self.confidence.assess(
            synthesis.confidence,
            stale_fraction=synthesis.stale_fraction,
            conflict_fraction=synthesis.conflict_fraction,
        )

        opinion = AnalystOpinion(
            opinion_id=opinion_id,
            analyst_id=request.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=_parse_direction(synthesis.direction),
            confidence=confidence,
            evidence=evidence,
            assumptions=[
                Assumption(description="News events are point-in-time deterministic records from the Research Data Platform"),
                Assumption(description="Event orientation is derived deterministically from structured payload fields and keyword rules"),
                Assumption(description="Only events with available_at <= as_of are consumed (point-in-time safe)"),
                Assumption(description="Duplicate events do not multiply evidence weight"),
            ],
            warnings=[
                AnalysisWarning(code="RESEARCH_ONLY", message="News analyst output is research-only, not trading advice"),
                AnalysisWarning(code="NO_LLM", message="No LLM or model inference is used"),
                AnalysisWarning(code="NO_NETWORK", message="No network access in default path"),
            ],
            limitations=[
                AnalysisLimitation(
                    code="NEWS_LIMITATION",
                    message="News orientation is based on deterministic rules; may not capture nuance",
                ),
                AnalysisLimitation(
                    code="NO_TRADING",
                    message="This opinion does not generate trades or allocate capital",
                ),
                AnalysisLimitation(
                    code="DECAY_MODEL",
                    message="Event relevance decay uses fixed half-lives by event type",
                ),
            ],
            generated_at=as_of,
            model_identity=None,
            data_freshness=self.freshness.assess(
                observed,
                available,
                as_of,
                evidence[0].evidence_type,
            ),
            decision_ready=False,
            suitable_for_live_trading=False,
            research_only=True,
        )
        return self._record_trace(opinion, request, "news")

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract(self, request: AnalystRequest) -> list[NewsObservation]:
        """Extract news observations from the snapshot or events in extra_context."""
        raw = request.extra_context.get("snapshot")
        snapshot: ResearchDataSnapshot | None = None

        if raw is not None:
            if isinstance(raw, str):
                snapshot = ResearchDataSnapshot.model_validate_json(raw)
            elif isinstance(raw, dict):
                snapshot = ResearchDataSnapshot.model_validate_json(json.dumps(raw))
            elif isinstance(raw, ResearchDataSnapshot):
                snapshot = raw

        observations: list[NewsObservation] = []
        if snapshot is not None:
            observations.extend(self.extractor.extract(snapshot))

        # Also support direct EventRecord input via extra_context['events'].
        events_raw = request.extra_context.get("events")
        if events_raw is not None:
            events = self._normalize_events(events_raw)
            observations.extend(self.extractor.extract_from_events(events, request.as_of))

        # Deduplicate by fingerprint.
        seen: set[str] = set()
        unique: list[NewsObservation] = []
        for obs in observations:
            if obs.fingerprint in seen:
                continue
            seen.add(obs.fingerprint)
            unique.append(obs)

        return unique

    @staticmethod
    def _normalize_events(events_raw: Any) -> tuple[EventRecord, ...]:
        if isinstance(events_raw, tuple):
            return tuple(NewsAnalyst._coerce_event(item) for item in events_raw)
        if isinstance(events_raw, list):
            return tuple(NewsAnalyst._coerce_event(item) for item in events_raw)
        if isinstance(events_raw, str):
            items = json.loads(events_raw)
            if isinstance(items, list):
                return tuple(NewsAnalyst._coerce_event(item) for item in items)
            return (NewsAnalyst._coerce_event(items),)
        if isinstance(events_raw, EventRecord):
            return (events_raw,)
        return (NewsAnalyst._coerce_event(events_raw),)

    @staticmethod
    def _coerce_event(item: Any) -> EventRecord:
        if isinstance(item, EventRecord):
            return item
        if isinstance(item, str):
            return EventRecord.model_validate_json(item)
        if isinstance(item, dict):
            # Round-trip through JSON so that string timestamps are parsed
            # properly (strict mode rejects bare strings).
            return EventRecord.model_validate_json(json.dumps(item))
        return EventRecord.model_validate_json(json.dumps(item))

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def _classify(self, clusters: list[EventCluster], as_of: datetime) -> list[ClassifiedEvent]:
        """Classify each cluster and compute decay + confirmation + novelty."""
        classified: list[ClassifiedEvent] = []
        known_fingerprints: set[str] = set()

        for cluster in clusters:
            latest = cluster.records[-1] if cluster.records else None
            if latest is None:
                continue

            event_type = classify_event_type(latest, self.config)
            orientation = classify_orientation(latest, event_type, self.config)
            importance = classify_importance(latest, event_type, self.config)
            scope = classify_scope(latest, self.config)

            # Set classification results on the observation so the evidence
            # factory's cluster-level helpers can read them.
            object.__setattr__(latest, "event_type", event_type.value)
            object.__setattr__(latest, "orientation", orientation.value)
            object.__setattr__(latest, "importance", importance)
            object.__setattr__(latest, "scope", scope)

            # Source quality — use source_identity if available, else fallback.
            if latest.source_identity is not None:
                source_quality = self.source_quality_service.assess(latest.source_identity)
            else:
                source_quality = self.source_quality_service.assess(_minimal_source_identity(latest.source))

            # Decay.
            decay = self.decay_service.assess(latest.available_at, as_of, event_type)

            # Confirmation: assess across all records in the cluster.
            confirmation = self.confirmation_service.assess(cluster.records)

            # Novelty: assess the latest observation.
            novelty_result = self.novelty_service.assess(latest, known_fingerprints=known_fingerprints)
            known_fingerprints.add(novelty_result.fingerprint)

            # Materiality: deterministic assessment of whether the event
            # carries enough signal to influence synthesis.
            materiality = self.materiality_service.assess(event_type, importance, scope, source_quality)

            # Lifecycle: check for cancelled/superseded/resolved/archived.
            lifecycle = self.lifecycle_service.assess(latest)

            # An event superseded by a later revision is also excluded.
            is_excluded = lifecycle.exclude_from_synthesis or (confirmation.status is ConfirmationStatus.SUPERSEDED)

            classified.append(
                ClassifiedEvent(
                    observation=latest,
                    event_type=event_type,
                    orientation=orientation,
                    importance=importance,
                    scope=scope,
                    source_quality=source_quality,
                    confirmation_status=confirmation.status,
                    decay_factor=decay.decay_factor,
                    is_stale=decay.is_stale,
                    is_duplicate=novelty_result.is_duplicate,
                    novelty_score=novelty_result.novelty_score,
                    cluster_id=cluster.cluster_id,
                    lifecycle_state=lifecycle.state,
                    is_excluded=is_excluded,
                    materiality_score=materiality.score,
                )
            )
        return classified

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def _build_evidence(
        self,
        clusters: list[EventCluster],
        classified: list[ClassifiedEvent],
        as_of: datetime,
    ) -> list[EvidenceItem]:
        """Build evidence items from classified clusters."""
        evidence: list[EvidenceItem] = []
        source = "news"

        for cluster in clusters:
            # Main evidence item.
            main_evidence = self.evidence_factory.create(cluster, as_of, source)
            evidence.append(main_evidence)

            # Supplementary evidence items.
            supplementary = self.evidence_factory.build_supplementary(cluster, as_of, source)
            evidence.extend(supplementary)

        # Deduplicate evidence by evidence_id.
        seen_ids: set[str] = set()
        unique_evidence: list[EvidenceItem] = []
        for item in evidence:
            if item.evidence_id in seen_ids:
                continue
            seen_ids.add(item.evidence_id)
            unique_evidence.append(item)

        return unique_evidence

    # ------------------------------------------------------------------
    # Insufficient output
    # ------------------------------------------------------------------

    def _insufficient(
        self,
        request: AnalystRequest,
        reason: str,
        *,
        warnings: list[AnalysisWarning] | None = None,
        limitations: list[AnalysisLimitation] | None = None,
        assumptions: list[Assumption] | None = None,
        evidence: list[EvidenceItem] | None = None,
        source: str | None = None,
    ) -> AnalystOpinion:
        if limitations is None:
            limitations = [
                AnalysisLimitation(
                    code="NO_NEWS_CONCLUSION",
                    message="No news events were classified from the supplied data",
                ),
                AnalysisLimitation(
                    code="NO_TRADING",
                    message="This opinion does not generate trades or allocate capital",
                ),
            ]
        return super()._insufficient(
            request,
            reason,
            warnings=warnings,
            limitations=limitations,
            assumptions=assumptions,
            evidence=evidence,
            source=source or "news",
        )


def _parse_direction(value: str) -> AnalysisDirection:
    mapping: dict[str, AnalysisDirection] = {
        "strongly_bullish": AnalysisDirection.BULLISH,
        "bullish": AnalysisDirection.BULLISH,
        "neutral": AnalysisDirection.NEUTRAL,
        "bearish": AnalysisDirection.BEARISH,
        "strongly_bearish": AnalysisDirection.BEARISH,
        "mixed": AnalysisDirection.MIXED,
        "insufficient_evidence": AnalysisDirection.INSUFFICIENT_EVIDENCE,
    }
    return mapping.get(value.lower(), AnalysisDirection.NEUTRAL)


def _minimal_source_identity(source_str: str) -> Any:
    """Create a DataSourceIdentity-like object from a source string for
    SourceQualityService.assess()."""
    from app.domain.models.data_platform import DataSourceIdentity

    provider, _, dataset = source_str.partition(":")
    return DataSourceIdentity(
        provider=provider or "unknown",
        dataset=dataset or "unknown",
        source_version="0",
        schema_version="0",
        offline_capable=True,
        authoritative=False,
        metadata={},
    )
