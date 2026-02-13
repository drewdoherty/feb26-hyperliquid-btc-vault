from hv_btc_vault.risk import clamp_target, trade_passes_min_notional


def test_clamp_target() -> None:
    assert clamp_target(1.5, 1.0) == 1.0
    assert clamp_target(-1.5, 1.0) == -1.0
    assert clamp_target(0.2, 1.0) == 0.2


def test_min_notional_gate() -> None:
    assert trade_passes_min_notional(delta_btc=0.001, mark_price=60000, min_trade_notional_usd=25)
    assert not trade_passes_min_notional(delta_btc=0.0001, mark_price=60000, min_trade_notional_usd=25)
