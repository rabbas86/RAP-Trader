"""Historical-simulation value-at-risk and conditional value-at-risk."""

from __future__ import annotations

import math
from typing import Any


class VaRCVaRService:
    @staticmethod
    def calculate(values: list[float], confidence: float, minimum: int) -> dict[str, Any]:
        if len(values) < minimum:
            return {"var": None, "cvar": None, "sample_size": len(values), "valid": False}
        ordered = sorted(values)
        index = max(0, math.ceil((1.0 - confidence) * len(ordered)) - 1)
        cutoff = ordered[index]
        tail = [value for value in ordered if value <= cutoff]
        return {"var": max(0.0, -cutoff), "cvar": max(0.0, -sum(tail) / len(tail)), "sample_size": len(values), "valid": True}
