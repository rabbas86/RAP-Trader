# Analyst Platform

Phase 7.5 consolidates the research-analyst lifecycle in `app/services/analyst/framework/`. `BaseAnalyst` owns common request validation, service wiring, health and metadata defaults, deterministic trace construction, fail-safe insufficient output, and error translation. Domain analysts retain their specialist normalization, formulas, evidence, and synthesis.

The lifecycle is: Validation → Normalization → Analysis → Evidence → Confidence → Opinion → Trace → Output. Technical normalization uses the feature platform; Fundamental normalization uses caller-supplied point-in-time statements; Mock uses deterministic synthetic context.

`DataFreshnessService`, `EvidenceValidationService`, and `ConfidenceAssessmentService` each have one canonical implementation. Imports from `app.services.analyst.service` remain supported. Every analysis result, including insufficient evidence, has a retrievable `AnalysisTrace` DAG through `trace_for(opinion_id)`.

The platform is offline, deterministic, research-only, never decision-ready, and unsuitable for live trading. It does not route decisions or invoke execution, portfolio, risk, committee, network, or model services.
