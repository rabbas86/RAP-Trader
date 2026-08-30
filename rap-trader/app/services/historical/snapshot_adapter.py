"""Snapshot-backed read-only adapter for the historical decision pipeline.

The adapter exposes only data contained in the selected
``PointInTimeDataSnapshot``. It never reaches around the snapshot to query
future or live market data directly.
"""

from __future__ import annotations

from app.domain.models.decision import AgentEvidence
from app.domain.models.prediction import KronosPrediction
from app.services.historical.snapshot import PointInTimeDataSnapshot


class HistoricalSnapshotDecisionAdapter:
    """Read-only decision-pipeline input bound to a single point-in-time snapshot."""

    def __init__(self, *, snapshot: PointInTimeDataSnapshot, producer_version: str = "phase16c-1.0") -> None:
        self.snapshot = snapshot
        self.producer_version = producer_version

    def prediction(self, *, ticker: str, timeframe: str = "1d") -> KronosPrediction:
        """Return a deterministic prediction identity derived from the snapshot."""
        return KronosPrediction(
            ticker=ticker,
            direction="FLAT",
            confidence=0.0,
            expected_return=0.0,
            time_horizon=timeframe,
            model_version=f"historical:{self.snapshot.snapshot_id}",
            timeframe=timeframe,
            source_provider="historical_decision_pipeline",
            data_start=self.snapshot.clock_start,
            data_end=self.snapshot.clock_end,
            generated_at=self.snapshot.simulated_at,
        )

    def agent_evidence(self, *, ticker: str, analyst_id: str, summary: str) -> AgentEvidence:
        """Return deterministic analyst evidence derived from the snapshot time."""
        return AgentEvidence(
            source=f"historical:{analyst_id}",
            ticker=ticker,
            recommendation="HOLD",
            confidence=0.0,
            reasoning_summary=summary,
            generated_at=self.snapshot.simulated_at,
        )

    def to_decision_engine_inputs(self, *, ticker: str, timeframe: str = "1d") -> dict[str, object]:
        """Assemble deterministic inputs for the canonical decision boundary."""
        return {
            "prediction": self.prediction(ticker=ticker, timeframe=timeframe),
            "technical": self.agent_evidence(ticker=ticker, analyst_id="technical", summary="Snapshot-contained technical evidence"),
            "fundamental": self.agent_evidence(ticker=ticker, analyst_id="fundamental", summary="Snapshot-contained fundamental evidence"),
            "news": self.agent_evidence(ticker=ticker, analyst_id="news", summary="Snapshot-contained news evidence"),
        }


__all__ = ["HistoricalSnapshotDecisionAdapter"]
