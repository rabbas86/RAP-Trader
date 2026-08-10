"""Safe portfolio input validation."""

from datetime import datetime

from app.domain.models.analyst import AnalysisDirection, AnalystOpinion


class PortfolioValidationError(ValueError):
    """Public, safe validation failure."""

    def __init__(self, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class PortfolioInputValidationService:
    def validate_opinions(self, opinions: list[AnalystOpinion], as_of: datetime) -> list[AnalystOpinion]:
        ids = [opinion.opinion_id for opinion in opinions]
        if len(ids) != len(set(ids)):
            raise PortfolioValidationError("DUPLICATE_OPINION", "Opinion IDs must be unique")
        for opinion in opinions:
            if opinion.generated_at > as_of or opinion.data_freshness.available_at > as_of:
                raise PortfolioValidationError("FUTURE_OPINION", "Future analyst opinions are forbidden")
            if not 0 <= opinion.confidence.value <= 1:
                raise PortfolioValidationError("MALFORMED_CONFIDENCE", "Opinion confidence must be between zero and one")
        return [
            opinion for opinion in opinions if opinion.direction is not AnalysisDirection.INSUFFICIENT_EVIDENCE and bool(opinion.evidence)
        ]
