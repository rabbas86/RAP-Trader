"""Causal market-structure detection using only confirmed swing points."""

from __future__ import annotations

from itertools import pairwise
from typing import Literal

from app.domain.models.analyst import EvidenceStrength
from app.domain.models.market_data import OHLCVBar
from app.domain.models.technical import MarketStructureState, SwingPoint


def confirmed_swings(bars: list[OHLCVBar], confirmation_bars: int = 2) -> list[SwingPoint]:
    """Return five-bar fractals confirmed two bars after their pivot."""
    if confirmation_bars != 2:
        raise ValueError("Phase 6 swing confirmation requires exactly two bars")
    swings: list[SwingPoint] = []
    for index in range(2, len(bars) - confirmation_bars):
        window = bars[index - 2 : index + 3]
        bar = bars[index]
        if bar.high > max(item.high for offset, item in enumerate(window) if offset != 2):
            swings.append(
                SwingPoint(
                    timestamp=bar.timestamp,
                    price=bar.high,
                    type="high",
                    confirmed_at=bars[index + 2].timestamp,
                    strength=EvidenceStrength.MODERATE,
                    bar_index=index,
                )
            )
        if bar.low < min(item.low for offset, item in enumerate(window) if offset != 2):
            swings.append(
                SwingPoint(
                    timestamp=bar.timestamp,
                    price=bar.low,
                    type="low",
                    confirmed_at=bars[index + 2].timestamp,
                    strength=EvidenceStrength.MODERATE,
                    bar_index=index,
                )
            )
    return sorted(swings, key=lambda item: (item.confirmed_at, item.bar_index, item.type))


def classify_structure(bars: list[OHLCVBar], swings: list[SwingPoint] | None = None) -> MarketStructureState:
    points = confirmed_swings(bars) if swings is None else swings
    highs = [point for point in points if point.type == "high"]
    lows = [point for point in points if point.type == "low"]
    hh = sum(current.price > previous.price for previous, current in pairwise(highs))
    lh = sum(current.price < previous.price for previous, current in pairwise(highs))
    hl = sum(current.price > previous.price for previous, current in pairwise(lows))
    ll = sum(current.price < previous.price for previous, current in pairwise(lows))
    regime: Literal["uptrend", "downtrend", "range_bound"] = "range_bound"
    if hh > lh and hl > ll:
        regime = "uptrend"
    elif lh > hh and ll > hl:
        regime = "downtrend"

    bos = None
    choch = None
    active_regime = "range_bound"
    for bar_index, bar in enumerate(bars):
        available = [point for point in points if point.confirmed_at <= bar.timestamp and point.bar_index < bar_index]
        prior_highs = [point for point in available if point.type == "high"]
        prior_lows = [point for point in available if point.type == "low"]
        if prior_highs and bar.close > prior_highs[-1].price:
            if active_regime == "downtrend":
                choch = bar.timestamp
            else:
                bos = bar.timestamp
            active_regime = "uptrend"
        elif prior_lows and bar.close < prior_lows[-1].price:
            if active_regime == "uptrend":
                choch = bar.timestamp
            else:
                bos = bar.timestamp
            active_regime = "downtrend"
    return MarketStructureState(
        regime=regime,
        last_confirmed_timestamp=points[-1].confirmed_at if points else None,
        higher_highs=hh,
        higher_lows=hl,
        lower_highs=lh,
        lower_lows=ll,
        bos_timestamp=bos,
        choch_timestamp=choch,
    )
