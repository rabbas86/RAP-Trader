"""Stable fingerprints and source revision metadata."""

from __future__ import annotations

from app.services.portfolio.provenance import PortfolioProvenanceService


class RiskProvenanceService(PortfolioProvenanceService):
    """Risk-specific name for the established offline provenance implementation."""
