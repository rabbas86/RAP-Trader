"""Phase 8B Macro Economist — deterministic, research-only, offline.

Consumes a ``ResearchDataSnapshot`` from the Phase 8A Unified Research Data
Platform (provided via ``AnalystRequest.extra_context``) and produces a
deterministic ``AnalystOpinion`` through the Phase 5 / 7.5 lifecycle.

The Macro Economist never fetches external data, never generates trades, never
allocates capital, and never calls RiskEngine, PortfolioManager, or
InvestmentCommittee.
"""

from __future__ import annotations

import json
from typing import ClassVar
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.analyst import (
    AnalysisDirection,
    AnalysisLimitation,
    AnalysisWarning,
    AnalystError,
    AnalystErrorCodes,
    AnalystOpinion,
    AnalystRequest,
    Assumption,
    EvidenceItem,
)
from app.domain.models.data_platform import ResearchDataSnapshot
from app.services.analyst.service import Analyst
from app.services.macro_analysis.base import MacroSignal
from app.services.macro_analysis.business_cycle import BusinessCycleService
from app.services.macro_analysis.config import MacroAnalystConfig
from app.services.macro_analysis.credit import CreditAnalysisService
from app.services.macro_analysis.employment import EmploymentAnalysisService
from app.services.macro_analysis.evidence import MacroEvidenceFactory
from app.services.macro_analysis.growth import GrowthAnalysisService
from app.services.macro_analysis.inflation import InflationAnalysisService
from app.services.macro_analysis.liquidity import LiquidityAnalysisService
from app.services.macro_analysis.monetary_policy import MonetaryPolicyAnalysisService
from app.services.macro_analysis.observations import ObservationExtractor
from app.services.macro_analysis.regime import MacroRegimeService
from app.services.macro_analysis.synthesis import MacroOpinionSynthesisService
from app.services.macro_analysis.yield_curve import YieldCurveAnalysisService


class MacroAnalyst(Analyst):
    """Deterministic, offline, research-only macro-economic analyst."""

    display_name = "Macro Economist"
    description = "Deterministic, offline macro-economic analyst using the Phase 8A Research Data Platform"
    health_detail = "deterministic offline macro formulas over ResearchDataSnapshot"

    # Service → (series whitelist categories the service reads from).
    _SERVICE_SERIES: ClassVar[dict[str, tuple[str, ...]]] = {
        "inflation": ("CPI", "CORE_CPI", "PCE", "CORE_PCE"),
        "growth": (
            "GDP",
            "GDP_TREND",
            "PMI",
            "INDUSTRIAL_PRODUCTION",
            "RETAIL_SALES",
            "HOUSING_STARTS",
            "CONSUMER_CONFIDENCE",
            "BUSINESS_SURVEY",
        ),
        "employment": ("UNEMPLOYMENT", "NONFARM_PAYROLLS"),
        "liquidity": ("MONEY_SUPPLY",),
        "policy": ("POLICY_RATE",),
        "yield_curve": ("YIELD_2Y", "YIELD_10Y", "YIELD_SPREAD"),
        "credit": ("CREDIT_SPREAD",),
    }

    def __init__(self, config: MacroAnalystConfig | None = None) -> None:
        self.config = config or MacroAnalystConfig()
        self._initialize_framework(self.config.uncalibrated_confidence_cap)
        self.extractor = ObservationExtractor(self.config)
        self.inflation_service = InflationAnalysisService(self.config)
        self.growth_service = GrowthAnalysisService(self.config)
        self.employment_service = EmploymentAnalysisService(self.config)
        self.liquidity_service = LiquidityAnalysisService(self.config)
        self.policy_service = MonetaryPolicyAnalysisService(self.config)
        self.yield_curve_service = YieldCurveAnalysisService(self.config)
        self.credit_service = CreditAnalysisService(self.config)
        self.business_cycle_service = BusinessCycleService(self.config)
        self.regime_service = MacroRegimeService(self.config)
        self.factory = MacroEvidenceFactory(self.config)
        self.synthesizer = MacroOpinionSynthesisService(self.config)

    def supported_timeframes(self) -> list[str]:
        return ["1d", "1w", "1mo", "1q"]

    def supported_asset_classes(self) -> list[str]:
        return ["macro"]

    def analyze(self, request: AnalystRequest) -> AnalystOpinion:
        self.validate_input(request)
        as_of = request.as_of
        snapshot = self._snapshot(request)
        observations = self.extractor.extract(snapshot)

        # --- Run each specialist service ---------------------------------
        signals_raw: list[MacroSignal | None] = []
        signals_raw.append(self.inflation_service.classify(observations.get("CPI", []), as_of))
        signals_raw.append(self.inflation_service.classify(observations.get("PCE", []), as_of))
        signals_raw.append(self.growth_service.classify(observations.get("GDP", []), as_of))
        signals_raw.append(self.growth_service.classify(observations.get("PMI", []), as_of))
        signals_raw.append(self.employment_service.classify(observations.get("UNEMPLOYMENT", []), as_of))
        signals_raw.append(self.liquidity_service.classify(observations.get("MONEY_SUPPLY", []), as_of))
        signals_raw.append(self.policy_service.classify(observations.get("POLICY_RATE", []), as_of))
        signals_raw.append(self.yield_curve_service.classify(observations.get("YIELD_SPREAD", []), as_of))
        signals_raw.append(self.credit_service.classify(observations.get("CREDIT_SPREAD", []), as_of))

        # Filter out None signals.
        signals: list[MacroSignal] = [s for s in signals_raw if s is not None]

        # --- Business-cycle signal (aggregates growth + employment + inflation) ---
        # Use GDP observations as the basis, passing aggregate trends.
        from app.services.macro_analysis.domain import (
            EmploymentTrend,
            GrowthTrend,
            InflationTrend,
        )

        growth_signal = next((s for s in signals if s.category == "growth"), None)
        employment_signal = next((s for s in signals if s.category == "employment"), None)
        inflation_signal = next((s for s in signals if s.category == "inflation"), None)

        growth_trend = GrowthTrend(growth_signal.trend_enum) if growth_signal else GrowthTrend.UNKNOWN
        employment_trend = EmploymentTrend(employment_signal.trend_enum) if employment_signal else EmploymentTrend.UNKNOWN
        inflation_trend = InflationTrend(inflation_signal.trend_enum) if inflation_signal else InflationTrend.UNKNOWN

        bc_observations = observations.get("GDP", []) or observations.get("PMI", [])
        bc_signal = self.business_cycle_service.classify(
            bc_observations,
            as_of,
            growth=growth_trend,
            employment=employment_trend,
            inflation=inflation_trend,
        )
        if bc_signal is not None:
            signals.append(bc_signal)

        # --- Regime classification ---------------------------------------
        regime_result = self.regime_service.classify(signals)

        # --- Insufficient data check -------------------------------------
        if not signals or regime_result.signals.signal_count < 2:
            return self._insufficient(
                request,
                "Insufficient macro data in snapshot for regime classification",
                warnings=[AnalysisWarning(code="INSUFFICIENT_DATA", message="Too few macro series available in snapshot")],
                limitations=[
                    AnalysisLimitation(
                        code="NO_MACRO_DATA",
                        message="Macro economist requires macroeconomic series in a ResearchDataSnapshot",
                    )
                ],
                source=snapshot.provenance.snapshot_id,
            )

        # --- Evidence construction ---------------------------------------
        evidence = self.factory.build(signals, as_of, snapshot.provenance.snapshot_id)

        try:
            self.validator.validate(evidence, as_of, allow_stale=self.config.stale_input_allowed)
        except AnalystError as exc:
            return self._insufficient(request, exc.safe_message)

        # --- Synthesis ---------------------------------------------------
        synthesis = self.synthesizer.synthesize(signals, as_of, regime_result.regime)

        material = (
            f"macro|{request.ticker}|{as_of.isoformat()}|{synthesis.regime.value}|"
            f"{synthesis.direction}|{','.join(e.evidence_id for e in evidence)}"
        )
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
            direction=_parse_direction(synthesis.direction),
            confidence=confidence,
            evidence=evidence,
            assumptions=[
                Assumption(description="Macro observations are point-in-time deterministic snapshots from the Research Data Platform"),
                Assumption(description="Regime thresholds are fixed, deterministic, and documented in MacroAnalystConfig"),
            ],
            warnings=[AnalysisWarning(code="RESEARCH_ONLY", message="Macro economist output is research-only, not trading advice")],
            limitations=[
                AnalysisLimitation(
                    code="MACRO_LIMITATION",
                    message="Macro regimes are based on deterministic thresholds and may not capture structural shifts",
                ),
                AnalysisLimitation(
                    code="NO_TRADING",
                    message="This opinion does not generate trades or allocate capital",
                ),
            ],
            generated_at=as_of,
            model_identity=None,
            data_freshness=self.freshness.assess(
                observed,
                max(e.available_at for e in evidence),
                as_of,
                evidence[0].evidence_type,
            ),
            decision_ready=False,
            suitable_for_live_trading=False,
            research_only=True,
        )
        return self._record_trace(opinion, request, snapshot.provenance.snapshot_id)

    @staticmethod
    def _snapshot(request: AnalystRequest) -> ResearchDataSnapshot:
        raw = request.extra_context.get("snapshot")
        if raw is None:
            raise AnalystError(
                AnalystErrorCodes.INSUFFICIENT_DATA,
                "macro analyst requires 'snapshot' (ResearchDataSnapshot JSON) in extra_context",
            )
        if isinstance(raw, str):
            return ResearchDataSnapshot.model_validate_json(raw)
        if isinstance(raw, dict):
            # Use JSON round-trip so that lists are properly coerced to tuples
            # and datetime strings are parsed (strict mode rejects bare lists).
            return ResearchDataSnapshot.model_validate_json(json.dumps(raw))
        if isinstance(raw, ResearchDataSnapshot):
            return raw
        raise AnalystError(
            AnalystErrorCodes.INVALID_REQUEST,
            "snapshot must be a ResearchDataSnapshot JSON object or string",
        )

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
                    code="NO_MACRO_CONCLUSION",
                    message="No macro regime was classified from the supplied snapshot",
                )
            ]
        return super()._insufficient(
            request,
            reason,
            warnings=warnings,
            limitations=limitations,
            assumptions=assumptions,
            evidence=evidence,
            source=source or "macro",
        )


def _parse_direction(value: str) -> AnalysisDirection:
    """Convert a synthesis direction string into the AnalysisDirection enum."""
    mapping: dict[str, AnalysisDirection] = {
        "BULLISH": AnalysisDirection.BULLISH,
        "BEARISH": AnalysisDirection.BEARISH,
        "NEUTRAL": AnalysisDirection.NEUTRAL,
        "MIXED": AnalysisDirection.MIXED,
        "INSUFFICIENT_EVIDENCE": AnalysisDirection.INSUFFICIENT_EVIDENCE,
    }
    return mapping.get(value.upper(), AnalysisDirection.NEUTRAL)
