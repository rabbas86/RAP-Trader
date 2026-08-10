"""Risk limit construction and evaluation."""

from __future__ import annotations

import operator
from typing import ClassVar, Literal, cast
from uuid import NAMESPACE_URL, uuid5

from app.domain.models.risk import RiskBreach, RiskCategory, RiskConstraintSet, RiskLimit, RiskMetric, RiskSeverity


class RiskLimitEvaluator:
    OPERATORS: ClassVar = {
        "gt": operator.gt,
        "gte": operator.ge,
        "lt": operator.lt,
        "lte": operator.le,
        "eq": operator.eq,
        "ne": operator.ne,
    }

    def limits(self, constraints: RiskConstraintSet) -> tuple[RiskLimit, ...]:
        definitions = (
            (
                "single-position",
                RiskCategory.CONCENTRATION,
                "max_single_position_weight",
                constraints.max_single_position_weight,
                RiskSeverity.CRITICAL,
                True,
            ),
            ("sector", RiskCategory.SECTOR, "max_sector_weight", constraints.max_sector_weight, RiskSeverity.HIGH, False),
            ("industry", RiskCategory.INDUSTRY, "max_industry_weight", constraints.max_industry_weight, RiskSeverity.HIGH, False),
            (
                "asset-class",
                RiskCategory.ASSET_CLASS,
                "max_asset_class_weight",
                constraints.max_asset_class_weight,
                RiskSeverity.HIGH,
                False,
            ),
            ("hhi", RiskCategory.CONCENTRATION, "hhi", constraints.max_hhi, RiskSeverity.HIGH, False),
            (
                "effective-positions",
                RiskCategory.DIVERSIFICATION,
                "effective_positions",
                constraints.min_effective_positions,
                "lt",
                RiskSeverity.MODERATE,
                False,
            ),
            ("volatility", RiskCategory.VOLATILITY, "portfolio_volatility", constraints.max_portfolio_volatility, RiskSeverity.HIGH, False),
            (
                "average-correlation",
                RiskCategory.CORRELATION,
                "weighted_average_correlation",
                constraints.maximum_average_correlation,
                RiskSeverity.HIGH,
                False,
            ),
            (
                "pair-correlation",
                RiskCategory.CORRELATION,
                "max_pairwise_correlation",
                constraints.max_pairwise_correlation,
                RiskSeverity.HIGH,
                False,
            ),
            ("drawdown", RiskCategory.DRAWDOWN, "max_drawdown", constraints.max_drawdown, RiskSeverity.HIGH, False),
            ("var95", RiskCategory.VAR, "var_95", constraints.max_var_95, RiskSeverity.HIGH, False),
            ("cvar95", RiskCategory.CVAR, "cvar_95", constraints.max_cvar_95, RiskSeverity.HIGH, False),
            ("var99", RiskCategory.VAR, "var_99", constraints.max_var_99, RiskSeverity.HIGH, False),
            ("cvar99", RiskCategory.CVAR, "cvar_99", constraints.max_cvar_99, RiskSeverity.HIGH, False),
            (
                "liquidity-score",
                RiskCategory.LIQUIDITY,
                "liquidity_score",
                constraints.minimum_liquidity_score,
                "lt",
                RiskSeverity.HIGH,
                False,
            ),
            ("illiquid", RiskCategory.LIQUIDITY, "illiquid_weight", constraints.max_illiquid_weight, RiskSeverity.CRITICAL, True),
            (
                "unknown-metadata",
                RiskCategory.DATA_QUALITY,
                "unknown_metadata_weight",
                constraints.maximum_unknown_metadata_weight,
                RiskSeverity.HIGH,
                False,
            ),
            ("gross", RiskCategory.GROSS_EXPOSURE, "gross_exposure", constraints.max_gross_exposure, RiskSeverity.CRITICAL, True),
            ("net", RiskCategory.NET_EXPOSURE, "net_exposure", constraints.max_net_exposure, RiskSeverity.CRITICAL, True),
            ("short", RiskCategory.SHORT_EXPOSURE, "short_exposure", constraints.max_short_exposure, RiskSeverity.CRITICAL, True),
            ("cash", RiskCategory.CASH, "cash_weight", constraints.min_cash_weight, "lt", RiskSeverity.HIGH, False),
            ("turnover", RiskCategory.TURNOVER, "turnover", constraints.max_turnover, RiskSeverity.MODERATE, False),
        )
        return tuple(
            RiskLimit(
                limit_id=identifier,
                category=category,
                metric=metric,
                threshold=threshold,
                comparator=cast(Literal["gt", "gte", "lt", "lte", "eq", "ne"], comparator),
                severity=severity,
                hard_limit=hard,
                description=f"{metric} must not exceed {threshold}",
            )
            for definition in definitions
            for identifier, category, metric, threshold, comparator, severity, hard in (
                definition if len(definition) == 7 else (*definition[:4], "gt", *definition[4:]),
            )
        )

    def evaluate(self, metrics: tuple[RiskMetric, ...], constraints: RiskConstraintSet, provenance: str) -> tuple[RiskBreach, ...]:
        by_name = {item.name: item for item in metrics if item.valid}
        breaches: list[RiskBreach] = []
        for limit in self.limits(constraints):
            metric = by_name.get(limit.metric)
            if metric is None or not self.OPERATORS[limit.comparator](metric.value, limit.threshold):
                continue
            breach_id = str(uuid5(NAMESPACE_URL, f"{limit.limit_id}|{metric.value}|{provenance}"))
            breaches.append(
                RiskBreach(
                    breach_id=breach_id,
                    limit_id=limit.limit_id,
                    category=limit.category,
                    metric_name=metric.name,
                    observed_value=metric.value,
                    threshold=limit.threshold,
                    severity=limit.severity,
                    hard_limit=limit.hard_limit,
                    description=limit.description,
                    recommended_action=(
                        f"Increase {metric.name} to at least {limit.threshold}"
                        if limit.comparator in {"lt", "lte"}
                        else f"Reduce {metric.name} to at most {limit.threshold}"
                    ),
                    provenance=provenance,
                )
            )
        return tuple(breaches)
