# Analyst Platform

Phase 7.5 consolidates the research-analyst lifecycle in `app/services/analyst/framework/`. `BaseAnalyst` owns common request validation, service wiring, health and metadata defaults, deterministic trace construction, fail-safe insufficient output, and error translation. Domain analysts retain their specialist normalization, formulas, evidence, and synthesis.

The lifecycle is: Validation → Normalization → Analysis → Evidence → Confidence → Opinion → Trace → Output. Technical normalization uses the feature platform; Fundamental normalization uses caller-supplied point-in-time statements; Mock uses deterministic synthetic context.

`DataFreshnessService`, `EvidenceValidationService`, and `ConfidenceAssessmentService` each have one canonical implementation. Imports from `app.services.analyst.service` remain supported. Every analysis result, including insufficient evidence, has a retrievable `AnalysisTrace` DAG through `trace_for(opinion_id)`.

The platform is offline, deterministic, research-only, never decision-ready, and
unsuitable for live trading. It does not route decisions or invoke execution,
portfolio, risk, committee, network, or model services. Future analysts can consume
`ResearchDataSnapshot` from the Phase 8A Unified Research Data Platform
(see `docs/DATA_PLATFORM.md`) as an additional point-in-time-safe data source,
without duplicating data-platform normalization or quality logic.

## Analysts

| Analyst | Role | Input | Status |
|---|---|---|---|
| Mock | MOCK | Synthetic | Phase 7.5 |
| Technical | TECHNICAL | FeatureSnapshot (MIFP) | Phase 6 |
| Fundamental | FUNDAMENTAL | CompanyFundamentals | Phase 7 |
| Macro | MACRO | ResearchDataSnapshot (Phase 8A) | Phase 8B |

The Macro Analyst (`app/services/macro_analysis/`) is a deterministic, offline,
research-only specialist that classifies the macro-economic regime from a
`ResearchDataSnapshot`. See `docs/MACRO_ECONOMIST.md`.
# Portfolio-manager consumer

Phase 10 is a downstream, research-only consumer of `AnalystOpinion`. It preserves accepted opinion IDs in proposal provenance and the construction trace; insufficient-evidence opinions are ignored and future or duplicate opinions are rejected.
