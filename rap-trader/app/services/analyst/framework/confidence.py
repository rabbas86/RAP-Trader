"""Canonical analyst confidence assessment."""

from app.domain.models.analyst import ConfidenceScore


class ConfidenceAssessmentService:
    def __init__(self, uncalibrated_cap: float = 0.65) -> None:
        self.uncalibrated_cap = uncalibrated_cap

    def assess(
        self, value: float, *, calibrated: bool = False, stale_fraction: float = 0.0, conflict_fraction: float = 0.0
    ) -> ConfidenceScore:
        adjusted = max(0.0, min(1.0, value * (1 - 0.5 * stale_fraction) * (1 - 0.5 * conflict_fraction)))
        capped = not calibrated and adjusted > self.uncalibrated_cap
        adjusted = self.uncalibrated_cap if capped else adjusted
        return ConfidenceScore(
            value=round(adjusted, 6),
            capped=capped,
            calibration_note="historical calibration available" if calibrated else "uncalibrated; confidence cap enforced",
            has_historical_calibration=calibrated,
        )
