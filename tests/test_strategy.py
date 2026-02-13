from hv_btc_vault.strategy import make_signal
from hv_btc_vault.types import Forecast


def test_long_signal_when_positive_and_confident() -> None:
    f = Forecast(horizon_hours=48, expected_return_pct=0.8, confidence=0.9)
    s = make_signal(f, max_abs_position_btc=1.0, confidence_threshold=0.55)
    assert s.side == "long"
    assert s.target_position_btc > 0


def test_flat_on_low_confidence() -> None:
    f = Forecast(horizon_hours=48, expected_return_pct=1.5, confidence=0.5)
    s = make_signal(f, max_abs_position_btc=1.0, confidence_threshold=0.55)
    assert s.side == "flat"
    assert s.target_position_btc == 0.0
