#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from hv_btc_vault.strategy import make_signal


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward simulation for BTC strategy")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--out-dir", default="reports")
    p.add_argument("--max-position", type=float, default=1.0)
    p.add_argument("--confidence-threshold", type=float, default=0.55)
    p.add_argument("--min-abs-return-pct", type=float, default=0.10)
    p.add_argument("--min-train", type=int, default=120)
    p.add_argument("--retrain-every", type=int, default=7)
    return p.parse_args()


def _dataset(flow_csv: str, price_csv: str) -> tuple[pd.DataFrame, list[str]]:
    f = pd.read_csv(flow_csv)
    p = pd.read_csv(price_csv)
    f = f[["date", "net_flow_usd"]].copy()
    p = p[["date", "close"]].copy()
    f["date"] = pd.to_datetime(f["date"]).dt.date
    p["date"] = pd.to_datetime(p["date"]).dt.date

    df = pd.merge(f, p, on="date", how="inner").sort_values("date")
    df["flow_b"] = df["net_flow_usd"] / 1_000_000_000
    df["flow_lag1"] = df["flow_b"].shift(1)
    df["flow_lag2"] = df["flow_b"].shift(2)
    df["flow_ma3"] = df["flow_b"].rolling(3).mean()
    df["flow_ma7"] = df["flow_b"].rolling(7).mean()
    df["flow_std7"] = df["flow_b"].rolling(7).std()
    df["ret_1d"] = df["close"].pct_change() * 100.0
    df["target_48h_return_pct"] = (df["close"].shift(-2) / df["close"] - 1.0) * 100.0
    df["next_day_ret_pct"] = df["close"].shift(-1) / df["close"] - 1.0

    feature_cols = ["flow_b", "flow_lag1", "flow_lag2", "flow_ma3", "flow_ma7", "flow_std7", "ret_1d"]
    df = df.dropna(subset=feature_cols + ["target_48h_return_pct", "next_day_ret_pct"]).reset_index(drop=True)
    return df, feature_cols


def _fit_model(x: np.ndarray, y: np.ndarray) -> tuple[Pipeline, float]:
    model = Pipeline(
        steps=[
            ("scale", StandardScaler()),
            (
                "mlp",
                MLPRegressor(
                    hidden_layer_sizes=(32, 16),
                    activation="relu",
                    random_state=42,
                    max_iter=1500,
                    early_stopping=True,
                    validation_fraction=0.2,
                ),
            ),
        ]
    )
    model.fit(x, y)
    resid_std = float(np.std(y - model.predict(x)))
    return model, max(resid_std, 0.05)


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df, feature_cols = _dataset(args.flow_csv, args.price_csv)
    if len(df) < args.min_train + 10:
        raise RuntimeError(f"Not enough rows for simulation: {len(df)}")

    model: Pipeline | None = None
    resid_std = 0.2
    records = []

    for i in range(args.min_train, len(df)):
        if model is None or (i - args.min_train) % args.retrain_every == 0:
            train = df.iloc[:i]
            x_train = train[feature_cols].to_numpy(dtype=float)
            y_train = train["target_48h_return_pct"].to_numpy(dtype=float)
            model, resid_std = _fit_model(x_train, y_train)

        row = df.iloc[i]
        x = row[feature_cols].to_numpy(dtype=float).reshape(1, -1)
        expected = float(model.predict(x)[0])
        confidence = min(0.5 + 0.1 * (abs(expected) / resid_std), 0.95)

        signal = make_signal(
            forecast=type("F", (), {"expected_return_pct": expected, "confidence": confidence})(),
            max_abs_position_btc=args.max_position,
            confidence_threshold=args.confidence_threshold,
            min_abs_return_pct=args.min_abs_return_pct,
        )

        position = signal.target_position_btc
        strat_ret = float(position * row["next_day_ret_pct"])
        bench_ret = float(row["next_day_ret_pct"])

        records.append(
            {
                "date": row["date"].isoformat(),
                "expected_return_pct": expected,
                "confidence": confidence,
                "signal_side": signal.side,
                "position_btc": position,
                "strategy_ret": strat_ret,
                "benchmark_ret": bench_ret,
            }
        )

    bt = pd.DataFrame(records)
    bt["strategy_equity"] = (1.0 + bt["strategy_ret"]).cumprod()
    bt["benchmark_equity"] = (1.0 + bt["benchmark_ret"]).cumprod()

    bt.to_csv(out_dir / "backtest_timeseries.csv", index=False)

    # Build trade blotter from position transitions.
    blotter_rows = []
    prev_pos = 0.0
    for _, row in bt.iterrows():
        pos = float(row["position_btc"])
        if abs(pos - prev_pos) < 1e-12:
            prev_pos = pos
            continue

        if prev_pos == 0.0 and pos != 0.0:
            event = "ENTER"
        elif prev_pos != 0.0 and pos == 0.0:
            event = "EXIT"
        elif prev_pos * pos < 0:
            event = "FLIP"
        else:
            event = "RESIZE"

        blotter_rows.append(
            {
                "date": row["date"],
                "event": event,
                "from_position_btc": prev_pos,
                "to_position_btc": pos,
                "signal_side": row["signal_side"],
                "expected_return_pct": row["expected_return_pct"],
                "confidence": row["confidence"],
                "strategy_ret_that_day": row["strategy_ret"],
                "benchmark_ret_that_day": row["benchmark_ret"],
                "strategy_equity_after": row["strategy_equity"],
            }
        )
        prev_pos = pos

    blotter = pd.DataFrame(blotter_rows)
    blotter.to_csv(out_dir / "trade_blotter.csv", index=False)

    summary = {
        "rows": int(len(bt)),
        "n_trade_events": int(len(blotter)),
        "confidence_threshold": float(args.confidence_threshold),
        "min_abs_return_pct": float(args.min_abs_return_pct),
        "max_position": float(args.max_position),
        "retrain_every": int(args.retrain_every),
        "strategy_final": float(bt["strategy_equity"].iloc[-1]),
        "benchmark_final": float(bt["benchmark_equity"].iloc[-1]),
        "strategy_daily_vol": float(bt["strategy_ret"].std()),
        "benchmark_daily_vol": float(bt["benchmark_ret"].std()),
        "pct_days_in_market": float((bt["position_btc"].abs() > 0).mean()),
    }
    pd.Series(summary).to_json(out_dir / "backtest_summary.json", indent=2)

    plt.figure(figsize=(10, 5))
    plt.plot(pd.to_datetime(bt["date"]), bt["strategy_equity"], label="Strategy")
    plt.plot(pd.to_datetime(bt["date"]), bt["benchmark_equity"], label="Buy & Hold")
    plt.title("Walk-forward Equity Curve")
    plt.xlabel("Date")
    plt.ylabel("Growth of $1")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "equity_curve.png", dpi=150)
    plt.close()

    plt.figure(figsize=(10, 4))
    plt.scatter(bt["expected_return_pct"], bt["benchmark_ret"] * 100, alpha=0.5, s=12)
    plt.axhline(0, color="black", linewidth=0.8)
    plt.axvline(0, color="black", linewidth=0.8)
    plt.title("Predicted 48h Return vs Next-day BTC Return")
    plt.xlabel("Predicted return (%)")
    plt.ylabel("Realized next-day return (%)")
    plt.tight_layout()
    plt.savefig(out_dir / "prediction_scatter.png", dpi=150)
    plt.close()

    print((out_dir / "backtest_summary.json").read_text())


if __name__ == "__main__":
    main()
