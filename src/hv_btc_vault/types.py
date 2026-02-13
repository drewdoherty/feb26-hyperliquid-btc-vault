from dataclasses import dataclass
from datetime import date
from typing import Literal


@dataclass(frozen=True)
class DailyFlow:
    dt: date
    net_flow_usd: float


@dataclass(frozen=True)
class Forecast:
    horizon_hours: int
    expected_return_pct: float
    confidence: float


@dataclass(frozen=True)
class Signal:
    side: Literal["long", "short", "flat"]
    target_position_btc: float
    reason: str
