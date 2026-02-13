#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
from pathlib import Path

import pandas as pd


def parse_float_list(raw: str) -> list[float]:
    return [float(x.strip()) for x in raw.split(",") if x.strip()]


def parse_int_list(raw: str) -> list[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run multiple strategy variants and rank results")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--out-dir", default="reports/variants")
    p.add_argument("--confidence-thresholds", default="0.50,0.52,0.55")
    p.add_argument("--min-abs-return-pcts", default="0.02,0.05,0.10")
    p.add_argument("--max-positions", default="0.5,1.0")
    p.add_argument("--retrain-every-options", default="1,3,7")
    p.add_argument("--min-train", type=int, default=120)
    return p.parse_args()


def run_variant(
    root: Path,
    flow_csv: str,
    price_csv: str,
    variant_dir: Path,
    confidence_threshold: float,
    min_abs_return_pct: float,
    max_position: float,
    retrain_every: int,
    min_train: int,
) -> dict:
    variant_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "./scripts/simulate_strategy.py",
        "--flow-csv",
        flow_csv,
        "--price-csv",
        price_csv,
        "--out-dir",
        str(variant_dir),
        "--confidence-threshold",
        str(confidence_threshold),
        "--min-abs-return-pct",
        str(min_abs_return_pct),
        "--max-position",
        str(max_position),
        "--retrain-every",
        str(retrain_every),
        "--min-train",
        str(min_train),
    ]

    subprocess.run(cmd, cwd=root, check=True, capture_output=True, text=True)

    summary_path = variant_dir / "backtest_summary.json"
    summary = json.loads(summary_path.read_text())
    summary["variant"] = variant_dir.name
    return summary


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    out_dir = (root / args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    confs = parse_float_list(args.confidence_thresholds)
    mins = parse_float_list(args.min_abs_return_pcts)
    max_pos = parse_float_list(args.max_positions)
    retrains = parse_int_list(args.retrain_every_options)

    rows = []
    for conf, min_ret, mx, re in itertools.product(confs, mins, max_pos, retrains):
        name = f"c{conf:.2f}_m{min_ret:.2f}_x{mx:.2f}_r{re}"
        variant_dir = out_dir / name
        try:
            summary = run_variant(
                root=root,
                flow_csv=args.flow_csv,
                price_csv=args.price_csv,
                variant_dir=variant_dir,
                confidence_threshold=conf,
                min_abs_return_pct=min_ret,
                max_position=mx,
                retrain_every=re,
                min_train=args.min_train,
            )
            rows.append(summary)
        except Exception as exc:
            rows.append(
                {
                    "variant": name,
                    "confidence_threshold": conf,
                    "min_abs_return_pct": min_ret,
                    "max_position": mx,
                    "retrain_every": re,
                    "error": str(exc),
                }
            )

    results = pd.DataFrame(rows)
    results.to_csv(out_dir / "variant_results.csv", index=False)

    successful = results[results.get("error").isna()] if "error" in results.columns else results
    if not successful.empty:
        leaderboard = successful.sort_values(["strategy_final", "n_trade_events"], ascending=[False, False]).head(20)
        leaderboard.to_csv(out_dir / "leaderboard_top20.csv", index=False)
        print(leaderboard[["variant", "strategy_final", "benchmark_final", "n_trade_events", "pct_days_in_market"]].to_string(index=False))
    else:
        print("No successful variants. Check variant_results.csv for errors.")


if __name__ == "__main__":
    main()
