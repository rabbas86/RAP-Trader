"""Deterministic portfolio constraint projection."""

from collections import defaultdict

from app.domain.models.portfolio import PortfolioConstraintSet, PortfolioPosition


class ConstraintEngine:
    def apply(
        self, weights: dict[str, float], metadata: dict[str, PortfolioPosition], constraints: PortfolioConstraintSet
    ) -> tuple[dict[str, float], list[str]]:
        result = dict(weights)
        adjustments: list[str] = []
        for symbol in sorted(result):
            original = result[symbol]
            if not constraints.allow_shorts and result[symbol] < 0:
                result[symbol] = 0.0
                adjustments.append(f"{symbol}:shorts_disabled")
            elif result[symbol] < -constraints.max_short_position_weight:
                result[symbol] = -constraints.max_short_position_weight
                adjustments.append(f"{symbol}:short_cap")
            if result[symbol] > constraints.max_position_weight:
                result[symbol] = constraints.max_position_weight
                adjustments.append(f"{symbol}:position_cap")
            if 0 < abs(result[symbol]) < constraints.min_position_weight:
                result[symbol] = 0.0
                adjustments.append(f"{symbol}:below_minimum")
            if original != result[symbol] and not adjustments:
                adjustments.append(f"{symbol}:bounded")
        active = sorted((symbol for symbol, weight in result.items() if weight != 0), key=lambda symbol: (-abs(result[symbol]), symbol))
        for symbol in active[constraints.max_positions :]:
            result[symbol] = 0.0
            adjustments.append(f"{symbol}:max_positions")
        self._group_caps(result, metadata, "sector", constraints.max_sector_weight, constraints.sector_caps, adjustments)
        self._group_caps(result, metadata, "industry", constraints.max_industry_weight, constraints.industry_caps, adjustments)
        self._group_caps(result, metadata, "asset_class", constraints.max_asset_class_weight, constraints.asset_class_caps, adjustments)
        gross = sum(abs(weight) for weight in result.values())
        allowed_gross = min(constraints.max_gross_exposure, 1.0 - constraints.min_cash_weight)
        if gross > allowed_gross:
            factor = allowed_gross / gross
            result = {symbol: weight * factor for symbol, weight in result.items()}
            adjustments.append("portfolio:gross_exposure_cash_floor")
        net = sum(result.values())
        if net > constraints.max_net_exposure and net > 0:
            factor = constraints.max_net_exposure / net
            result = {symbol: weight * factor for symbol, weight in result.items()}
            adjustments.append("portfolio:max_net_exposure")
        return result, adjustments

    @staticmethod
    def _group_caps(
        weights: dict[str, float],
        metadata: dict[str, PortfolioPosition],
        field: str,
        default_cap: float,
        specific_caps: dict[str, float],
        adjustments: list[str],
    ) -> None:
        groups: dict[str, list[str]] = defaultdict(list)
        for symbol in sorted(weights):
            value = getattr(metadata.get(symbol), field, None)
            groups[value or "UNKNOWN"].append(symbol)
        for group, symbols in groups.items():
            if group == "UNKNOWN" and field in {"sector", "industry"}:
                continue
            exposure = sum(abs(weights[symbol]) for symbol in symbols)
            cap = specific_caps.get(group, default_cap)
            if exposure > cap and exposure > 0:
                factor = cap / exposure
                for symbol in symbols:
                    weights[symbol] *= factor
                adjustments.append(f"{field}:{group}:cap")
