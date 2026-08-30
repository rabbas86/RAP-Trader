"""Deterministic observational attribution service."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from threading import RLock
from typing import Any, Literal

from app.domain.canonical import sha256_fingerprint
from app.domain.models.artifact import ArtifactEnvelope, ArtifactType
from app.domain.models.market_data import Symbol
from app.services.artifacts.base import ArtifactStore
from app.services.attribution.models import (
    AlignmentSummary,
    AttributionRecord,
    ComponentAttribution,
    ComponentKind,
    GovernanceAttribution,
    GovernanceInterventionKind,
    OutcomeAlignment,
)

ATTRIBUTION_INDEXED_FIELDS = (
    "decision_artifact_id",
    "decision_journal_entry_id",
    "outcome_evaluation_id",
    "symbol",
    "horizon",
    "period",
    "direction",
    "outcome_alignment",
    "producer_version",
    "component",
)


class AttributionValidationError(Exception):
    """Raised when attribution inputs are invalid."""


class AttributionQueryError(Exception):
    """Raised when attribution queries are invalid."""


class _ComponentBuildResult:
    __slots__ = ("artifact_id", "component", "payload")

    def __init__(self, component: ComponentKind, artifact_id: str, payload: dict[str, Any]) -> None:
        self.component = component
        self.artifact_id = artifact_id
        self.payload = payload


SUPPORTED_COMPONENT_KINDS = frozenset(
    {
        ComponentKind.TECHNICAL,
        ComponentKind.FUNDAMENTAL,
        ComponentKind.MACRO,
        ComponentKind.NEWS,
        ComponentKind.KRONOS,
        ComponentKind.FUSION,
        ComponentKind.PORTFOLIO,
        ComponentKind.RISK,
        ComponentKind.INVESTMENT_COMMITTEE,
        ComponentKind.CHAIRMAN,
    }
)


def _coerce_component(component: ComponentKind | str) -> ComponentKind:
    if isinstance(component, ComponentKind):
        normalized = component
    else:
        try:
            normalized = ComponentKind(component)
        except ValueError as exc:
            raise AttributionValidationError(f"unsupported component: {component}") from exc
    if normalized not in SUPPORTED_COMPONENT_KINDS:
        raise AttributionValidationError(f"unsupported component: {normalized.value}")
    return normalized


def _coerce_alignment(outcome_alignment: OutcomeAlignment | str | None) -> OutcomeAlignment:
    if outcome_alignment is None:
        return OutcomeAlignment.NEUTRAL
    if isinstance(outcome_alignment, OutcomeAlignment):
        return outcome_alignment
    try:
        return OutcomeAlignment(outcome_alignment)
    except ValueError as exc:
        raise AttributionValidationError(f"unsupported outcome alignment: {outcome_alignment}") from exc


def _coerce_intervention(intervention: GovernanceInterventionKind | str) -> GovernanceInterventionKind:
    if isinstance(intervention, GovernanceInterventionKind):
        return intervention
    try:
        return GovernanceInterventionKind(intervention)
    except ValueError as exc:
        raise AttributionValidationError(f"unsupported governance intervention: {intervention}") from exc


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return bool(value)


class AttributionService:
    """Observational attribution service persisted through ArtifactStore."""

    def __init__(self, store: ArtifactStore, producer_version: str = "rap-trader-attribution-1.0") -> None:
        self.store = store
        self.producer_version = producer_version
        self._index: OrderedDict[str, str] = OrderedDict()
        self._secondary_indexes: dict[str, OrderedDict[str, list[str]]] = {field: OrderedDict() for field in ATTRIBUTION_INDEXED_FIELDS}
        self._lock = RLock()
        self._rebuild_indexes()

    def attribute_decision(
        self,
        *,
        decision_artifact_id: str,
        decision_run_manifest_id: str,
        decision_journal_entry_id: str,
        outcome_evaluation_id: str,
        symbol: str,
        decision_at: datetime,
        horizon: int,
        period: str,
        direction: Literal["BUY", "SELL", "WAIT"],
        components: list[ComponentAttribution],
        governance: GovernanceAttribution | None = None,
        signed_outcome_metric: float | None = None,
        outcome_alignment: OutcomeAlignment | str | None = None,
        methodology: str = "phase15g-deterministic-observational-1.0",
        producer_version: str | None = None,
    ) -> tuple[AttributionRecord, ArtifactEnvelope]:
        if not components:
            raise AttributionValidationError("at least one component attribution is required")
        if len({item.component for item in components}) != len(components):
            raise AttributionValidationError("duplicate component kinds are not supported")
        for item in components:
            _coerce_component(item.component)
        normalized_outcome_alignment = _coerce_alignment(outcome_alignment)
        record = AttributionRecord(
            attribution_id=self._build_attribution_id(
                decision_artifact_id=decision_artifact_id,
                decision_journal_entry_id=decision_journal_entry_id,
                outcome_evaluation_id=outcome_evaluation_id,
                horizon=horizon,
                period=period,
                direction=direction,
                components=components,
                governance=governance,
                outcome_alignment=normalized_outcome_alignment,
                methodology=methodology,
            ),
            decision_artifact_id=decision_artifact_id,
            decision_run_manifest_id=decision_run_manifest_id,
            decision_journal_entry_id=decision_journal_entry_id,
            outcome_evaluation_id=outcome_evaluation_id,
            symbol=Symbol(symbol),
            decision_at=decision_at,
            horizon=horizon,
            period=period,
            direction=direction,
            components=tuple(components),
            governance=governance,
            signed_outcome_metric=signed_outcome_metric,
            outcome_alignment=normalized_outcome_alignment,
            producer_version=producer_version or self.producer_version,
            methodology=methodology,
        )
        persisted = self.store.put(record.envelope())
        with self._lock:
            self._index[record.attribution_id] = persisted.artifact_id
            for component in record.components:
                for field in ATTRIBUTION_INDEXED_FIELDS:
                    if field == "component":
                        value = component.component.value
                    else:
                        value = self._index_value(record, field)
                    index = self._secondary_indexes[field]
                    updated = list(dict.fromkeys(index.get(value, []) + [persisted.artifact_id]))
                    index[value] = updated
        return record, persisted

    def get_attribution(self, attribution_id: str) -> AttributionRecord:
        envelope = self._load_envelope(attribution_id)
        return AttributionRecord.model_validate(envelope.payload)

    def get_attribution_envelope(self, attribution_id: str) -> ArtifactEnvelope:
        return self._load_envelope(attribution_id)

    def query(self, **filters: Any) -> list[AttributionRecord]:
        unknown = sorted(set(filters) - set(ATTRIBUTION_INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise AttributionQueryError(f"unsupported attribution filters: {unknown}")
        with self._lock:
            candidates = self._candidate_ids(filters)
        records: list[AttributionRecord] = []
        seen: set[str] = set()
        for artifact_id in candidates:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            envelope = self.store.get(artifact_id)
            record = AttributionRecord.model_validate(envelope.payload)
            if self._record_matches_filters(record, filters):
                records.append(record)
        return records

    def aggregate(self, **filters: Any) -> list[AlignmentSummary]:
        unknown = sorted(set(filters) - set(ATTRIBUTION_INDEXED_FIELDS) - {"limit"})
        if unknown:
            raise AttributionQueryError(f"unsupported aggregation filters: {unknown}")
        with self._lock:
            candidates = self._candidate_ids(filters)
        seen: set[str] = set()
        buckets: dict[tuple[str, str], list[AttributionRecord]] = OrderedDict()
        for artifact_id in candidates:
            if artifact_id in seen:
                continue
            seen.add(artifact_id)
            envelope = self.store.get(artifact_id)
            record = AttributionRecord.model_validate(envelope.payload)
            if self._record_matches_filters(record, filters):
                for component in record.components:
                    key = (component.component.value, component.methodology)
                    buckets.setdefault(key, []).append(record)
        summaries = []
        for (component_value, _methodology), records in buckets.items():
            sample_count = len(records)
            aligned_records = []
            signed_returns = []
            confidences = []
            for record in records:
                for component in record.components:
                    if component.component.value == component_value:
                        if component.outcome_alignment is OutcomeAlignment.ALIGNED:
                            aligned_records.append(record)
                        if component.signed_outcome_metric is not None:
                            signed_returns.append(component.signed_outcome_metric)
                        confidences.append(component.historical_confidence)
                        break
            alignment_count = len(aligned_records)
            alignment_rate = alignment_count / sample_count if sample_count else 0.0
            average_signed_return = sum(signed_returns) / len(signed_returns) if signed_returns else None
            confidence_calibration = None
            if confidences:
                expected_alignment_rate = sum(confidences) / len(confidences)
                confidence_calibration = abs(alignment_rate - expected_alignment_rate)
            summaries.append(
                AlignmentSummary(
                    component=ComponentKind(component_value),
                    sample_count=sample_count,
                    alignment_count=alignment_count,
                    alignment_rate=round(alignment_rate, 12),
                    average_signed_return=round(average_signed_return, 12) if average_signed_return is not None else None,
                    confidence_calibration=round(confidence_calibration, 12) if confidence_calibration is not None else None,
                )
            )
        return summaries

    def _build_attribution_id(self, **material: Any) -> str:
        canonical_material = dict(material)
        components = canonical_material.get("components")
        if isinstance(components, list):
            canonical_material["components"] = [
                component.model_dump(mode="json") if hasattr(component, "model_dump") else component for component in components
            ]
        canonical_material.pop("producer_version", None)
        return sha256_fingerprint(
            {
                "schema_version": "1.0",
                **canonical_material,
            }
        )

    def _index_value(self, record: AttributionRecord, field: str) -> str:
        value: object = getattr(record, field)
        if field == "symbol":
            symbol_value = value
            if isinstance(symbol_value, Symbol):
                return symbol_value.root
            return str(symbol_value)
        if field == "outcome_alignment":
            outcome_value = value
            if isinstance(outcome_value, OutcomeAlignment):
                return outcome_value.value
            return str(outcome_value)
        return str(value)

    def _candidate_ids(self, filters: dict[str, Any]) -> list[str]:
        candidates = None
        for field, value in filters.items():
            if field == "limit":
                continue
            index = self._secondary_indexes[field]
            normalized = self._index_value_for_filter(value)
            ids = index.get(normalized, [])
            if candidates is None:
                candidates = OrderedDict.fromkeys(ids)
            else:
                candidates = OrderedDict.fromkeys(candidate for candidate in candidates if candidate in ids)
        return list(candidates or self._index.values())

    def _index_value_for_filter(self, value: Any) -> str:
        if isinstance(value, ComponentKind):
            return value.value
        if isinstance(value, OutcomeAlignment):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    def _record_matches_filters(self, record: AttributionRecord, filters: dict[str, Any]) -> bool:
        for field, value in filters.items():
            if field == "limit":
                continue
            if field == "component":
                expected = self._index_value_for_filter(value)
                if not any(component.component.value == expected for component in record.components):
                    return False
                continue
            if self._index_value(record, field) != self._index_value_for_filter(value):
                return False
        return True

    def _load_envelope(self, attribution_id: str) -> ArtifactEnvelope:
        with self._lock:
            if attribution_id not in self._index:
                raise AttributionValidationError(f"attribution record not found: {attribution_id}")
            artifact_id = self._index[attribution_id]
        return self.store.get(artifact_id)

    def _rebuild_indexes(self) -> None:
        for artifact_id in self.store.list_ids(filters={"artifact_type": ArtifactType.ATTRIBUTION_RECORD}):
            envelope = self.store.get(artifact_id)
            payload = envelope.payload if isinstance(envelope.payload, dict) else {}
            attribution_id = payload.get("attribution_id")
            if not isinstance(attribution_id, str) or not attribution_id:
                raise AttributionValidationError("attribution payload is missing attribution_id")
            self._index[attribution_id] = artifact_id
            components = payload.get("components", [])
            component_values: list[str] = []
            if isinstance(components, list):
                for component in components:
                    if isinstance(component, dict):
                        component_value = component.get("component")
                        if isinstance(component_value, str) and component_value:
                            component_values.append(component_value)
            for field in ATTRIBUTION_INDEXED_FIELDS:
                if field == "component":
                    for component_value in component_values:
                        self._secondary_indexes[field].setdefault(component_value, []).append(artifact_id)
                    continue
                value = payload.get(field)
                if value is None:
                    continue
                self._secondary_indexes[field].setdefault(str(value), []).append(artifact_id)


__all__ = [
    "AttributionQueryError",
    "AttributionService",
    "AttributionValidationError",
]
