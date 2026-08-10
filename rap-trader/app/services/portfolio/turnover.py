"""Deterministic one-way turnover measurement and limiting."""


def compute_turnover(current: dict[str, float], proposed: dict[str, float]) -> float:
    return 0.5 * sum(abs(proposed.get(symbol, 0.0) - current.get(symbol, 0.0)) for symbol in current.keys() | proposed.keys())


def scale_to_turnover(current: dict[str, float], proposed: dict[str, float], maximum: float) -> tuple[dict[str, float], float, bool]:
    turnover = compute_turnover(current, proposed)
    if turnover <= maximum or turnover == 0:
        return dict(proposed), turnover, False
    scale = maximum / turnover
    scaled = {
        symbol: current.get(symbol, 0.0) + scale * (proposed.get(symbol, 0.0) - current.get(symbol, 0.0))
        for symbol in current.keys() | proposed.keys()
    }
    return scaled, compute_turnover(current, scaled), True
