"""Historical drawdown review."""

from __future__ import annotations

from typing import Any

from app.domain.models.market_data import HistoricalBarsResult


class DrawdownRiskService:
    def calculate(self, history: list[HistoricalBarsResult]) -> dict[str, Any]:
        per_asset: dict[str, float] = {}
        recoveries: dict[str, int | None] = {}
        recent: dict[str, float] = {}
        unavailable: list[str] = []
        for item in history:
            symbol = str(item.symbol)
            if not item.bars:
                unavailable.append(f"{symbol}: drawdown unavailable because no bars were supplied")
                continue
            if len(item.bars) < 2:
                unavailable.append(f"{symbol}: drawdown unavailable because at least two bars are required")
                continue
            peak = item.bars[0].close
            maximum = 0.0
            trough_index = 0
            peak_index = 0
            recovery: int | None = None
            for index, bar in enumerate(item.bars):
                if bar.close >= peak:
                    if index > trough_index and recovery is None:
                        recovery = index - trough_index
                    peak, peak_index = bar.close, index
                drawdown = 1.0 - bar.close / peak
                if drawdown > maximum:
                    maximum, trough_index = drawdown, index
            per_asset[symbol] = maximum
            recent[symbol] = 1.0 - item.bars[-1].close / max(bar.close for bar in item.bars)
            recoveries[symbol] = recovery if trough_index > peak_index else 0
        return {
            "per_asset_max_drawdown": per_asset,
            "max_drawdown": max(per_asset.values()) if per_asset else None,
            "recent_drawdown": recent,
            "recovery_duration": recoveries,
            "valid": bool(per_asset),
            "limitations": tuple(unavailable),
        }
