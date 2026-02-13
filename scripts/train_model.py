#!/usr/bin/env python3
from __future__ import annotations

import argparse

from hv_btc_vault.model_nn import train_and_save


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train BTC 48h neural-net baseline")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--model-path", default="models/nn_model.joblib")
    p.add_argument("--horizon-days", type=int, default=2)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    summary = train_and_save(
        flow_csv=args.flow_csv,
        price_csv=args.price_csv,
        model_path=args.model_path,
        horizon_days=args.horizon_days,
    )
    print(
        f"trained model with {summary.n_samples} rows, train_r2={summary.train_r2:.4f}, target_std={summary.target_std:.4f}"
    )


if __name__ == "__main__":
    main()
