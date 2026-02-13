#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hv_btc_vault.flow_data import IbitFlowRepository
from hv_btc_vault.forecast_provider import ForecastProvider
from hv_btc_vault.hyperliquid_executor import HyperliquidExecutor
from hv_btc_vault.risk import clamp_target
from hv_btc_vault.settings import settings
from hv_btc_vault.strategy import make_signal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Daily Hyperliquid BTC strategy run")
    parser.add_argument("--flow-csv", default="data/ibit_flows.csv", help="CSV with columns: date,net_flow_usd")
    parser.add_argument("--forecast-json", default="data/forecast.json", help="JSON with expected_return_pct, confidence")
    parser.add_argument(
        "--allow-heuristic",
        action="store_true",
        help="Use fallback flow->forecast heuristic if forecast JSON is missing",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    flow_repo = IbitFlowRepository(args.flow_csv)
    latest_flow = flow_repo.latest()

    forecast_provider = ForecastProvider(horizon_hours=settings.prediction_horizon_hours)
    if Path(args.forecast_json).exists():
        forecast = forecast_provider.from_json(args.forecast_json)
    elif args.allow_heuristic:
        forecast = forecast_provider.heuristic_from_flow(latest_flow)
    else:
        raise FileNotFoundError(
            f"Forecast not found: {args.forecast_json}. Pass --allow-heuristic for a temporary fallback."
        )

    signal = make_signal(
        forecast=forecast,
        max_abs_position_btc=settings.max_abs_position_btc,
        confidence_threshold=settings.confidence_threshold,
    )

    target = clamp_target(signal.target_position_btc, settings.max_abs_position_btc)

    executor = HyperliquidExecutor(settings)
    result = executor.rebalance_to_target(
        asset=settings.hl_asset,
        target_btc=target,
        min_trade_notional_usd=settings.min_trade_notional_usd,
    )

    output = {
        "flow": {"date": latest_flow.dt.isoformat(), "net_flow_usd": latest_flow.net_flow_usd},
        "forecast": {
            "horizon_hours": forecast.horizon_hours,
            "expected_return_pct": forecast.expected_return_pct,
            "confidence": forecast.confidence,
        },
        "signal": {
            "side": signal.side,
            "target_position_btc": signal.target_position_btc,
            "reason": signal.reason,
        },
        "execution": {
            "asset": result.asset,
            "mark_price": result.mark_price,
            "current_position_btc": result.current_position_btc,
            "target_position_btc": result.target_position_btc,
            "delta_btc": result.delta_btc,
            "dry_run": result.dry_run,
            "exchange_response": result.exchange_response,
        },
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
