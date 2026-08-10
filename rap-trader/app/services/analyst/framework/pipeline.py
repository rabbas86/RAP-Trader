"""Shared lifecycle base for deterministic research analysts."""

from abc import ABC, abstractmethod
from threading import RLock
from typing import Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisLimitation,
    AnalysisTrace,
    AnalysisWarning,
    AnalystError,
    AnalystErrorCodes,
    AnalystHealth,
    AnalystMetadata,
    AnalystOpinion,
    AnalystRequest,
    AnalystRole,
    Assumption,
    EvidenceItem,
)
from app.services.analyst.framework.confidence import ConfidenceAssessmentService
from app.services.analyst.framework.freshness import DataFreshnessService
from app.services.analyst.framework.health import build_health
from app.services.analyst.framework.metadata import build_metadata
from app.services.analyst.framework.output import insufficient_opinion
from app.services.analyst.framework.trace import build_analysis_trace
from app.services.analyst.framework.validation import EvidenceValidationService


class AnalystFrameworkConfig(Protocol):
    @property
    def analyst_id(self) -> str: ...

    @property
    def role(self) -> AnalystRole: ...


class BaseAnalyst(ABC):
    """Canonical validation, service wiring, health, metadata, output, and trace lifecycle."""

    display_name = "Analyst"
    description = "Deterministic research-only analyst"
    health_detail = "deterministic offline analyst"

    def _initialize_framework(self, confidence_cap: float = 0.65) -> None:
        self.freshness = DataFreshnessService()
        self.confidence = ConfidenceAssessmentService(confidence_cap)
        self.validator = EvidenceValidationService(self.freshness)
        self._traces: dict[str, AnalysisTrace] = {}
        self._trace_lock = RLock()

    @property
    def analyst_id(self) -> str:
        config = cast(AnalystFrameworkConfig, self.__dict__["config"])
        return config.analyst_id

    @property
    def analyst_role(self) -> AnalystRole:
        config = cast(AnalystFrameworkConfig, self.__dict__["config"])
        return config.role

    @abstractmethod
    def analyze(self, request: AnalystRequest) -> AnalystOpinion: ...

    @abstractmethod
    def supported_timeframes(self) -> list[str]: ...

    @abstractmethod
    def supported_asset_classes(self) -> list[str]: ...

    def validate_input(self, request: AnalystRequest) -> None:
        if request.analyst_id != self.analyst_id:
            raise AnalystError(AnalystErrorCodes.UNSUPPORTED_ANALYST, "Analyst is not available")
        if request.timeframe not in self.supported_timeframes() or request.asset_class not in self.supported_asset_classes():
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Unsupported timeframe or asset class")

    def health(self) -> AnalystHealth:
        return build_health(self.analyst_id, self.health_detail)

    def metadata(self) -> AnalystMetadata:
        return build_metadata(
            self.analyst_id,
            self.display_name,
            self.analyst_role,
            self.supported_timeframes(),
            self.supported_asset_classes(),
            self.description,
        )

    def _build_trace(self, opinion_id: str, evidence: list[EvidenceItem], request: AnalystRequest, source: str) -> AnalysisTrace:
        return build_analysis_trace(self.analyst_id, opinion_id, evidence, request, source)

    def _record_trace(self, opinion: AnalystOpinion, request: AnalystRequest, source: str) -> AnalystOpinion:
        trace = self._build_trace(opinion.opinion_id, opinion.evidence, request, source)
        with self._trace_lock:
            self._traces[opinion.opinion_id] = trace
        return opinion

    def trace_for(self, opinion_id: str) -> AnalysisTrace | None:
        with self._trace_lock:
            return self._traces.get(opinion_id)

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
        opinion_id = str(uuid5(NAMESPACE_URL, f"{self.analyst_id}|{request.ticker}|{request.as_of.isoformat()}|insufficient|{reason}"))
        opinion = insufficient_opinion(
            opinion_id,
            self.analyst_id,
            self.analyst_role,
            request,
            reason,
            self.freshness,
            self.confidence,
            warnings=warnings,
            limitations=limitations,
            assumptions=assumptions,
            evidence=evidence,
            source=source or self.analyst_id,
        )
        return self._record_trace(opinion, request, source or self.analyst_id)

    @staticmethod
    def translate_error(exc: Exception, safe_message: str = "Analyst processing failed") -> AnalystError:
        if isinstance(exc, AnalystError):
            return exc
        return AnalystError(AnalystErrorCodes.INSUFFICIENT_DATA, safe_message, internal_detail=str(exc))
