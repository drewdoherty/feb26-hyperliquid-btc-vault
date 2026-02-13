from __future__ import annotations

from .types import Forecast, Signal


def make_signal(
    forecast: Forecast,
    max_abs_position_btc: float,
    confidence_threshold: float,
    min_abs_return_pct: float = 0.10,
) -> Signal:
    if forecast.confidence < confidence_threshold:
        return Signal(side="flat", target_position_btc=0.0, reason="confidence below threshold")

    if abs(forecast.expected_return_pct) < min_abs_return_pct:
        return Signal(side="flat", target_position_btc=0.0, reason="expected return too small")

    intensity = min(abs(forecast.expected_return_pct) / 1.0, 1.0)
    target = max_abs_position_btc * intensity

    if forecast.expected_return_pct > 0:
        return Signal(side="long", target_position_btc=target, reason="positive expected return")

    return Signal(side="short", target_position_btc=-target, reason="negative expected return")
