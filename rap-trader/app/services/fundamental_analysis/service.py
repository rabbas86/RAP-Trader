"""Deterministic, offline, research-only fundamental analyst."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisTrace,
    AnalysisWarning,
    AnalystError,
    AnalystErrorCodes,
    AnalystHealth,
    AnalystMetadata,
    AnalystOpinion,
    AnalystRequest,
    Assumption,
    EvidenceType,
    TraceEdge,
    TraceNode,
    validate_trace,
)
from app.domain.models.fundamental import CompanyFundamentals, FundamentalMetric
from app.services.analyst.service import Analyst, ConfidenceAssessmentService, DataFreshnessService, EvidenceValidationService
from app.services.fundamental_analysis.balance_sheet import BalanceSheetAnalysisService
from app.services.fundamental_analysis.capital_efficiency import CapitalEfficiencyAnalysisService
from app.services.fundamental_analysis.cash_flow import CashFlowAnalysisService
from app.services.fundamental_analysis.config import FundamentalAnalystConfig
from app.services.fundamental_analysis.earnings_quality import EarningsQualityService
from app.services.fundamental_analysis.evidence import FundamentalEvidenceFactory
from app.services.fundamental_analysis.growth import GrowthAnalysisService
from app.services.fundamental_analysis.normalization import FinancialDataNormalizationService, NormalizedFinancialStatements
from app.services.fundamental_analysis.profitability import ProfitabilityAnalysisService
from app.services.fundamental_analysis.shareholder import ShareholderAnalysisService
from app.services.fundamental_analysis.synthesis import FundamentalOpinionSynthesisService
from app.services.fundamental_analysis.validation import FundamentalAnalysisValidationService
from app.services.fundamental_analysis.valuation import ValuationAnalysisService


class FundamentalAnalyst(Analyst):
    def __init__(self, config: FundamentalAnalystConfig | None = None) -> None:
        self.config = config or FundamentalAnalystConfig()
        self.normalizer = FinancialDataNormalizationService()
        self.statement_validator = FundamentalAnalysisValidationService()
        self.freshness = DataFreshnessService()
        self.evidence_validator = EvidenceValidationService(self.freshness)
        self.confidence = ConfidenceAssessmentService(self.config.uncalibrated_confidence_cap)
        self.factory = FundamentalEvidenceFactory()
        self.synthesizer = FundamentalOpinionSynthesisService()
        self._traces: dict[str, AnalysisTrace] = {}
        self._lock = threading.RLock()

    def supported_timeframes(self) -> list[str]:
        return ["1d"]

    def supported_asset_classes(self) -> list[str]:
        return ["equity"]

    def validate_input(self, request: AnalystRequest) -> None:
        if request.analyst_id != self.config.analyst_id:
            raise AnalystError(AnalystErrorCodes.UNSUPPORTED_ANALYST, "Analyst is not available")
        if request.timeframe not in self.supported_timeframes() or request.asset_class not in self.supported_asset_classes():
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Fundamental analysis supports daily equities only")
        if "fundamentals" not in request.extra_context:
            raise AnalystError(AnalystErrorCodes.INSUFFICIENT_DATA, "Fundamental statement data is required")

    def health(self) -> AnalystHealth:
        return AnalystHealth(
            analyst_id=self.config.analyst_id,
            configured=True,
            reachable=True,
            checked_at=datetime.now(UTC),
            status="healthy",
            detail="deterministic offline fundamental formulas",
        )

    def metadata(self) -> AnalystMetadata:
        return AnalystMetadata(
            analyst_id=self.config.analyst_id,
            display_name="Fundamental Analyst",
            role=self.config.role,
            supported_timeframes=self.supported_timeframes(),
            supported_asset_classes=self.supported_asset_classes(),
            description="Offline point-in-time financial statement research; never a trading decision",
        )

    def _fundamentals(self, request: AnalystRequest) -> CompanyFundamentals:
        try:
            raw = request.extra_context["fundamentals"]
            value = raw if isinstance(raw, CompanyFundamentals) else CompanyFundamentals.model_validate_json(json.dumps(raw))
        except (KeyError, ValidationError, TypeError) as exc:
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Invalid fundamental statement data") from exc
        if value.symbol.upper() != request.ticker:
            raise AnalystError(AnalystErrorCodes.INVALID_REQUEST, "Fundamental data symbol does not match the request")
        return value

    def _trace(self, opinion_id: str, request: AnalystRequest, evidence_ids: list[str]) -> AnalysisTrace:
        request_id, source_id, opinion_node = f"request:{opinion_id}", f"financials:{opinion_id}", f"opinion:{opinion_id}"
        nodes = [
            TraceNode(node_id=request_id, node_type="analyst_request", created_at=request.as_of),
            TraceNode(node_id=source_id, node_type="financial_statements", created_at=request.as_of),
        ]
        edges = [TraceEdge(source_node_id=request_id, target_node_id=source_id, edge_type="requests")]
        for evidence_id in evidence_ids:
            nodes.append(TraceNode(node_id=evidence_id, node_type="evidence", created_at=request.as_of))
            edges.append(TraceEdge(source_node_id=source_id, target_node_id=evidence_id, edge_type="produces"))
        nodes.append(TraceNode(node_id=opinion_node, node_type="analyst_opinion", created_at=request.as_of))
        edges.extend(TraceEdge(source_node_id=x, target_node_id=opinion_node, edge_type="supports") for x in evidence_ids)
        return validate_trace(
            AnalysisTrace(
                trace_id=str(uuid5(NAMESPACE_URL, f"fundamental-trace|{opinion_id}")), nodes=nodes, edges=edges, created_at=request.as_of
            )
        )

    def trace_for(self, opinion_id: str) -> AnalysisTrace | None:
        with self._lock:
            return self._traces.get(opinion_id)

    def _insufficient(self, request: AnalystRequest, message: str) -> AnalystOpinion:
        material = f"{request.model_dump_json()}|insufficient|{message}"
        return AnalystOpinion(
            opinion_id=str(uuid5(NAMESPACE_URL, material)),
            analyst_id=self.config.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
            confidence=self.confidence.assess(0),
            evidence=[],
            warnings=[AnalysisWarning(code="INSUFFICIENT_DATA", message=message)],
            limitations=[AnalysisLimitation(code="NO_CONCLUSION", message="No fundamental conclusion was produced")],
            generated_at=request.as_of,
            data_freshness=self.freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER),
        )

    @staticmethod
    def _data_quality_metrics(data: NormalizedFinancialStatements, as_of: datetime) -> list[FundamentalMetric]:
        latest_income = max(
            data.income_statements,
            key=lambda statement: (statement.period.period_end, statement.period.available_at),
            default=None,
        )
        period_end = latest_income.period.period_end if latest_income is not None else as_of
        available_at = latest_income.period.available_at if latest_income is not None else as_of
        metrics: list[FundamentalMetric] = []
        for warning in data.warnings:
            fingerprint = sha256(warning.encode()).hexdigest()
            metrics.append(
                FundamentalMetric(
                    metric_id=f"data_quality.normalization_warning.{fingerprint}",
                    name=warning,
                    category="data_quality",
                    value=1.0,
                    units="warning",
                    period_end=period_end,
                    available_at=available_at,
                    source_fingerprint=fingerprint,
                    formula_version="1.0",
                    valid=True,
                    warnings=[warning],
                )
            )
        return metrics

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        fundamentals = self._fundamentals(request)
        normalized = self.normalizer.normalize(fundamentals, request.as_of)
        self.statement_validator.validate(normalized, request.as_of)
        metrics: list[FundamentalMetric] = []
        analyses = (
            GrowthAnalysisService(self.config).analyze(normalized),
            ProfitabilityAnalysisService().analyze(normalized),
            CashFlowAnalysisService().analyze(normalized),
            BalanceSheetAnalysisService().analyze(normalized),
            CapitalEfficiencyAnalysisService().analyze(normalized),
            EarningsQualityService().analyze(normalized),
            ShareholderAnalysisService().analyze(normalized),
        )
        for found, _ in analyses:
            metrics.extend(found)
        valuation, _ = ValuationAnalysisService().analyze(normalized, fundamentals)
        metrics.extend(valuation)
        metrics.extend(self._data_quality_metrics(normalized, request.as_of))
        if not metrics:
            return self._insufficient(request, "Insufficient compatible financial inputs")
        source = str(fundamentals.source_metadata.get("source", "supplied financial statements"))
        evidence = self.factory.build(metrics, request.as_of, source)
        try:
            self.evidence_validator.validate(evidence, request.as_of, allow_stale=self.config.stale_input_allowed)
        except AnalystError as exc:
            return self._insufficient(request, exc.safe_message)
        synthesis = self.synthesizer.synthesize(evidence, request.as_of)
        material = f"{request.model_dump_json()}|{','.join(x.source_fingerprint for x in metrics)}|{synthesis.direction.value}"
        opinion_id = str(uuid5(NAMESPACE_URL, material))
        observed = max(x.observed_at for x in evidence)
        opinion = AnalystOpinion(
            opinion_id=opinion_id,
            analyst_id=self.config.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=synthesis.direction,
            confidence=self.confidence.assess(
                synthesis.confidence, stale_fraction=synthesis.stale_fraction, conflict_fraction=synthesis.conflict_fraction
            ),
            evidence=evidence,
            assumptions=[Assumption(description="Financial statements are interpreted using deterministic historical formulas")],
            warnings=[
                AnalysisWarning(code="RESEARCH_ONLY", message="This output is research only and is not a trading decision"),
                *[AnalysisWarning(code="DATA_QUALITY", message=x) for x in normalized.warnings],
            ],
            limitations=[
                AnalysisLimitation(code="HISTORICAL_DATA", message="Financial history and valuation ratios do not predict future returns")
            ],
            generated_at=request.as_of,
            data_freshness=self.freshness.assess(
                observed, max(x.available_at for x in evidence), request.as_of, EvidenceType.FINANCIAL_STATEMENT
            ),
            research_only=True,
            suitable_for_live_trading=False,
        )
        with self._lock:
            self._traces[opinion_id] = self._trace(opinion_id, request, [x.evidence_id for x in evidence])
        return opinion
