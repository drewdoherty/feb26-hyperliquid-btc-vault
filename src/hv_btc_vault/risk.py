from __future__ import annotations


def clamp_target(target_btc: float, max_abs_position_btc: float) -> float:
    return max(min(target_btc, max_abs_position_btc), -max_abs_position_btc)


def trade_passes_min_notional(delta_btc: float, mark_price: float, min_trade_notional_usd: float) -> bool:
    return abs(delta_btc) * mark_price >= min_trade_notional_usd
