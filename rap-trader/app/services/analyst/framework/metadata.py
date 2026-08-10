"""Shared analyst metadata construction."""

from app.domain.models.analyst import AnalystMetadata, AnalystRole


def build_metadata(
    analyst_id: str, display_name: str, role: AnalystRole, timeframes: list[str], asset_classes: list[str], description: str
) -> AnalystMetadata:
    return AnalystMetadata(
        analyst_id=analyst_id,
        display_name=display_name,
        role=role,
        supported_timeframes=timeframes,
        supported_asset_classes=asset_classes,
        description=description,
    )
