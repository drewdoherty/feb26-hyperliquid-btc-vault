#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from pathlib import Path

import pandas as pd


def _parse_float_list(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run V2 out-of-sample variant sweep")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--out-dir", default="reports/v2_variants")
    p.add_argument("--test-start-date", default="2025-09-01")

    p.add_argument("--confidence-thresholds", default="0.50,0.52,0.55")
    p.add_argument("--min-abs-return-pcts", default="0.00,0.02,0.05")
    p.add_argument("--retrain-every-options", default="1,3,7")
    p.add_argument("--train-lookback-days-options", default="0,365")
    p.add_argument("--tx-cost-bps-options", default="0,2")

    p.add_argument("--max-position", type=float, default=1.0)
    p.add_argument("--min-train", type=int, default=120)
    p.add_argument("--start-value", type=float, default=100.0)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    out_dir = (root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    confs = _parse_float_list(args.confidence_thresholds)
    mins = _parse_float_list(args.min_abs_return_pcts)
    retrains = _parse_int_list(args.retrain_every_options)
    lookbacks = _parse_int_list(args.train_lookback_days_options)
    txs = _parse_float_list(args.tx_cost_bps_options)

    rows: list[dict[str, str | int | float]] = []

    for conf, min_ret, retrain, lookback, tx in itertools.product(confs, mins, retrains, lookbacks, txs):
        name = f"c{conf:.2f}_m{min_ret:.2f}_r{retrain}_lb{lookback}_tx{tx:.1f}"
        variant_dir = out_dir / name
        variant_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "./scripts/simulate_strategy_v2.py",
            "--flow-csv",
            args.flow_csv,
            "--price-csv",
            args.price_csv,
            "--out-dir",
            str(variant_dir),
            "--test-start-date",
            args.test_start_date,
            "--max-position",
            str(args.max_position),
            "--confidence-threshold",
            str(conf),
            "--min-abs-return-pct",
            str(min_ret),
            "--min-train",
            str(args.min_train),
            "--retrain-every",
            str(retrain),
            "--train-lookback-days",
            str(lookback),
            "--tx-cost-bps",
            str(tx),
            "--start-value",
            str(args.start_value),
        ]

        try:
            subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True)
            summary = json.loads((variant_dir / "v2_summary.json").read_text(encoding="utf-8"))
            summary["variant"] = name
            rows.append(summary)
        except Exception as exc:
            rows.append(
                {
                    "variant": name,
                    "test_start_date": args.test_start_date,
                    "confidence_threshold": conf,
                    "min_abs_return_pct": min_ret,
                    "retrain_every": retrain,
                    "train_lookback_days": lookback,
                    "tx_cost_bps": tx,
                    "error": str(exc),
                }
            )

    df = pd.DataFrame(rows)
    df.to_csv(out_dir / "v2_variant_results.csv", index=False)

    if "error" in df.columns:
        good = df[df["error"].isna()].copy()
    else:
        good = df.copy()

    if good.empty:
        print("No successful variants. See v2_variant_results.csv")
        return

    leaderboard = good.sort_values(
        ["strategy_final_value", "strategy_max_drawdown", "n_trade_events"],
        ascending=[False, False, False],
    )
    leaderboard.to_csv(out_dir / "v2_leaderboard.csv", index=False)

    view_cols = [
        "variant",
        "strategy_final_value",
        "benchmark_final_value",
        "strategy_max_drawdown",
        "n_trade_events",
        "pct_days_in_market",
    ]
    print(leaderboard[view_cols].head(20).to_string(index=False))


if __name__ == "__main__":
    main()
