"""Deterministic simplified stress scenarios; these are not forecasts."""

from __future__ import annotations

from app.domain.models.portfolio import PortfolioProposal
from app.domain.models.risk import StressResult, StressScenario


class StressTestingService:
    VERSION = "phase-11-stress-v2"

    def scenarios(self) -> tuple[StressScenario, ...]:
        definitions = (
            ("market_down_10", {"market": -0.10}),
            ("market_down_20", {"market": -0.20}),
            ("top_position_down_25", {"top_position": -0.25}),
            ("sector_down_20", {"largest_sector": -0.20}),
            ("approximate_volatility_spike", {"volatility_sensitivity": -0.05}),
            ("correlation_to_one", {"correlation": -0.08}),
            ("credit_spreads_widen", {"credit": -0.08}),
            ("rates_up_100bps", {"rates": -0.04}),
            ("liquidity_haircut_50", {"liquidity": -0.10}),
            ("combined_risk_off", {"market": -0.20, "correlation": -0.05, "liquidity": -0.05}),
        )
        return tuple(
            StressScenario(
                scenario_id=name,
                name=name.replace("_", " "),
                description="Deterministic approximate research sensitivity; hypothetical, not a forecast",
                shocks=shocks,
                source="RAP-Trader Phase 11",
                version=self.VERSION,
                assumptions=("Linear impact approximation", "Hypothetical, not a forecast"),
            )
            for name, shocks in definitions
        )

    def run(self, proposal: PortfolioProposal, illiquid_weight: float) -> tuple[StressResult, ...]:
        positions = sorted(proposal.positions, key=lambda item: (-abs(item.proposed_weight), item.symbol))
        largest = abs(positions[0].proposed_weight) if positions else 0.0
        sectors: dict[str, float] = {}
        for item in positions:
            sectors[item.sector or "unknown"] = sectors.get(item.sector or "unknown", 0.0) + abs(item.proposed_weight)
        largest_sector = max(sectors.values(), default=0.0)
        results: list[StressResult] = []
        for scenario in self.scenarios():
            market = scenario.shocks.get("market", 0.0) * proposal.net_exposure
            concentration = scenario.shocks.get("top_position", 0.0) * largest + scenario.shocks.get("largest_sector", 0.0) * largest_sector
            liquidity = scenario.shocks.get("liquidity", 0.0) * illiquid_weight
            other = (
                sum(value for key, value in scenario.shocks.items() if key not in {"market", "top_position", "largest_sector", "liquidity"})
                * proposal.gross_exposure
            )
            impact = market + concentration + liquidity + other
            affected = {item.symbol: impact * abs(item.proposed_weight) for item in positions}
            results.append(
                StressResult(
                    scenario_id=scenario.scenario_id,
                    estimated_portfolio_impact=impact,
                    affected_positions=affected,
                    concentration_effect=concentration,
                    liquidity_effect=liquidity,
                    assumptions=scenario.assumptions,
                )
            )
        return tuple(results)
