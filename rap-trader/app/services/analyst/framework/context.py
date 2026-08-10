"""Analyst request context normalization."""

from typing import Any

from app.domain.models.analyst import AnalystRequest


def normalized_context(request: AnalystRequest) -> dict[str, Any]:
    return dict(request.extra_context)
