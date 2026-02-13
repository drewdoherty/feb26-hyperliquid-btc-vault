#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hv_btc_vault.flow_data import IbitFlowRepository
from hv_btc_vault.forecast_provider import ForecastProvider
from hv_btc_vault.model_nn import forecast_from_model, train_and_save


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate forecast.json from local NN model")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--model-path", default="models/nn_model.joblib")
    p.add_argument("--horizon-days", type=int, default=2)
    p.add_argument("--allow-heuristic", action="store_true")
    p.add_argument("--train-if-missing", action="store_true")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    model_file = Path(args.model_path)

    try:
        if not model_file.exists() and args.train_if_missing:
            train_and_save(
                flow_csv=args.flow_csv,
                price_csv=args.price_csv,
                model_path=args.model_path,
                horizon_days=args.horizon_days,
            )

        payload = forecast_from_model(
            flow_csv=args.flow_csv,
            price_csv=args.price_csv,
            model_path=args.model_path,
            horizon_days=args.horizon_days,
        )
        print(json.dumps(payload))
        return
    except Exception:
        if not args.allow_heuristic:
            raise

    flow = IbitFlowRepository(args.flow_csv).latest()
    heuristic = ForecastProvider(horizon_hours=args.horizon_days * 24).heuristic_from_flow(flow)
    print(
        json.dumps(
            {
                "horizon_hours": heuristic.horizon_hours,
                "expected_return_pct": heuristic.expected_return_pct,
                "confidence": heuristic.confidence,
            }
        )
    )


if __name__ == "__main__":
    main()
