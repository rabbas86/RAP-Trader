# Analyst Framework

The `technical` analyst adds six evidence categories, optional forecast/backtest evidence, confidence-capped deterministic synthesis, and a validated provenance DAG.

Phase 5 defines a common, strict contract for research opinions. It does not implement specialist intelligence or create trades. An analyst reports a direction, bounded confidence, timestamped evidence, assumptions, warnings, limitations, freshness, and provenance.

The included `MockAnalyst` is deterministic and offline. Confidence is not certainty: uncalibrated values are capped, stale or conflicting evidence reduces confidence, and the framework never invents historical accuracy. Evidence records observation, availability, evaluation, and expiry times so lookahead and stale inputs can be rejected.

Opinion aggregation is descriptive only. It reports agreement, disagreement, direction counts, orientation, overlap, freshness, missing roles, and minority views. Every opinion and aggregate is research-only, unsuitable for live trading, and not decision-ready. Analysts cannot create trades.

Technical Analyst work begins in Phase 6. The Risk Officer, Investment Committee, and Chairman remain future phases. A prior committee-fusion idea is isolated under `app/experimental/committee_fusion` and is not production code.
