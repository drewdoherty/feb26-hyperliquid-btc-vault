from __future__ import annotations

import json
from pathlib import Path

from .types import DailyFlow, Forecast


class ForecastProvider:
    def __init__(self, horizon_hours: int) -> None:
        self.horizon_hours = horizon_hours

    def from_json(self, path: str) -> Forecast:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Forecast file not found: {path}")

        payload = json.loads(p.read_text(encoding="utf-8"))
        return Forecast(
            horizon_hours=int(payload.get("horizon_hours", self.horizon_hours)),
            expected_return_pct=float(payload["expected_return_pct"]),
            confidence=float(payload["confidence"]),
        )

    def heuristic_from_flow(self, flow: DailyFlow) -> Forecast:
        # Lightweight fallback when model output is not provided.
        expected = max(min(flow.net_flow_usd / 1_000_000_000, 2.0), -2.0)
        confidence = 0.5 + min(abs(expected) / 8.0, 0.35)
        return Forecast(
            horizon_hours=self.horizon_hours,
            expected_return_pct=expected,
            confidence=confidence,
        )
