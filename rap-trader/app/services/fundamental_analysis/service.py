"""Phase 7 Fundamental Analyst — deterministic, research-only, offline."""

from __future__ import annotations

import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalystError,
    AnalystErrorCodes,
    AnalystOpinion,
    AnalystRequest,
    ConfidenceScore,
    EvidenceType,
)
from app.domain.models.fundamental import CompanyFundamentals, FundamentalMetric
from app.services.analyst.service import Analyst
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
    """Deterministic, offline, research-only fundamental analyst."""

    display_name = "Fundamental Analyst"
    description = "Deterministic, offline fundamental analyst for equities"
    health_detail = "deterministic offline fundamental formulas"

    def __init__(self, config: FundamentalAnalystConfig | None = None) -> None:
        self.config = config or FundamentalAnalystConfig()
        self._initialize_framework()
        self.normalizer = FinancialDataNormalizationService()
        self.statement_validator = FundamentalAnalysisValidationService()
        self.growth_service = GrowthAnalysisService()
        self.profitability_service = ProfitabilityAnalysisService()
        self.cash_flow_service = CashFlowAnalysisService()
        self.balance_sheet_service = BalanceSheetAnalysisService()
        self.capital_efficiency_service = CapitalEfficiencyAnalysisService()
        self.earnings_quality_service = EarningsQualityService()
        self.shareholder_service = ShareholderAnalysisService()
        self.valuation_service = ValuationAnalysisService()
        self.factory = FundamentalEvidenceFactory()
        self.synthesizer = FundamentalOpinionSynthesisService()

    def supported_timeframes(self) -> list[str]:
        return ["1d", "1w", "1mo"]

    def supported_asset_classes(self) -> list[str]:
        return ["equity"]

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        as_of = request.as_of
        fundamentals = self._fundamentals(request)
        self.statement_validator.validate_inputs(fundamentals, as_of)
        normalized = self.normalizer.normalize(fundamentals, as_of)
        self.statement_validator.validate(normalized, as_of)

        metrics: list[FundamentalMetric] = []
        for service in (
            self.growth_service,
            self.profitability_service,
            self.cash_flow_service,
            self.balance_sheet_service,
            self.capital_efficiency_service,
            self.earnings_quality_service,
            self.shareholder_service,
        ):
            found, _ = service.analyze(normalized)
            metrics.extend(found)

        valuation, _ = self.valuation_service.analyze(normalized, fundamentals)
        metrics.extend(valuation)
        metrics.extend(self._data_quality_metrics(normalized, as_of))

        if not metrics:
            return self._insufficient(request, "Insufficient compatible financial inputs")

        source = str(fundamentals.source_metadata.get("source", "supplied financial statements"))
        evidence = self.factory.build(metrics, as_of, source)

        try:
            self.validator.validate(evidence, as_of, allow_stale=self.config.stale_input_allowed)
        except AnalystError as exc:
            return self._insufficient(request, exc.safe_message)

        synthesis = self.synthesizer.synthesize(evidence, as_of)
        evidence_ids = [e.evidence_id for e in evidence]
        material = f"fundamental|{request.ticker}|{as_of.isoformat()}|{synthesis.direction.value}|{','.join(evidence_ids)}"
        opinion_id = str(uuid5(NAMESPACE_URL, material))
        observed = max(e.observed_at for e in evidence)

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
            direction=synthesis.direction,
            confidence=confidence,
            evidence=evidence,
            assumptions=[],
            warnings=[],
            limitations=[],
            generated_at=as_of,
            data_freshness=self.freshness.assess(observed, max(e.available_at for e in evidence), as_of, evidence[0].evidence_type),
            decision_ready=False,
            suitable_for_live_trading=False,
            research_only=True,
        )
        return self._record_trace(opinion, request, source)

    @staticmethod
    def _data_quality_metrics(normalized: NormalizedFinancialStatements, as_of: datetime) -> list[FundamentalMetric]:
        if not normalized.warnings:
            return []
        # Use the latest income statement's period timestamps when available;
        # fall back to as_of when no periods exist.
        if normalized.income_statements:
            latest = normalized.income_statements[-1]
            period_end = latest.period.period_end
            available_at = latest.period.available_at
        else:
            period_end = as_of
            available_at = as_of
        metrics: list[FundamentalMetric] = []
        for warning in normalized.warnings:
            metrics.append(
                FundamentalMetric(
                    metric_id=f"data_quality:{warning[:40]}",
                    name=warning,
                    category="data_quality",
                    value=0.0,
                    units="flag",
                    period_end=period_end,
                    available_at=available_at,
                    source_fingerprint="normalization",
                    formula_version="1.0",
                    valid=True,
                    warnings=[warning],
                    assumptions=["Normalization warnings are converted to data_quality evidence"],
                )
            )
        return metrics

    @staticmethod
    def _fundamentals(request: AnalystRequest) -> CompanyFundamentals:
        raw = request.extra_context.get("fundamentals")
        if raw is None:
            raise AnalystError(
                AnalystErrorCodes.INSUFFICIENT_DATA,
                "fundamental analyst requires 'fundamentals' in extra_context",
            )
        if isinstance(raw, dict):
            return CompanyFundamentals.model_validate_json(json.dumps(raw))
        if isinstance(raw, str):
            return CompanyFundamentals.model_validate_json(raw)
        raise AnalystError(
            AnalystErrorCodes.INVALID_REQUEST,
            "fundamentals must be a JSON object or string",
        )

    def _insufficient(self, request: AnalystRequest, reason: str) -> AnalystOpinion:
        opinion_id = str(uuid5(NAMESPACE_URL, f"fundamental|{request.ticker}|{request.as_of.isoformat()}|INSUFFICIENT_EVIDENCE"))
        confidence = ConfidenceScore(
            value=0.0, capped=False, calibration_note="uncalibrated; confidence cap enforced", has_historical_calibration=False
        )
        opinion = AnalystOpinion(
            opinion_id=opinion_id,
            analyst_id=request.analyst_id,
            analyst_role=self.config.role,
            ticker=request.ticker,
            direction=AnalysisDirection.INSUFFICIENT_EVIDENCE,
            confidence=confidence,
            evidence=[],
            assumptions=[],
            warnings=[],
            limitations=[],
            generated_at=request.as_of,
            data_freshness=self.freshness.assess(request.as_of, request.as_of, request.as_of, EvidenceType.OTHER),
            decision_ready=False,
            suitable_for_live_trading=False,
            research_only=True,
        )
        return self._record_trace(opinion, request, "fundamentals")
