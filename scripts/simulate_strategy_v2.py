#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from hv_btc_vault.strategy import make_signal


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Version 2 walk-forward simulation with regime-adaptive flow features")
    p.add_argument("--flow-csv", default="data/ibit_flows.csv")
    p.add_argument("--price-csv", default="data/btc_prices.csv")
    p.add_argument("--out-dir", default="reports/v2")
    p.add_argument("--test-start-date", default="2025-09-01")

    p.add_argument("--max-position", type=float, default=1.0)
    p.add_argument("--confidence-threshold", type=float, default=0.52)
    p.add_argument("--min-abs-return-pct", type=float, default=0.02)

    p.add_argument("--min-train", type=int, default=120)
    p.add_argument("--retrain-every", type=int, default=3)
    p.add_argument("--train-lookback-days", type=int, default=0)
    p.add_argument("--tx-cost-bps", type=float, default=0.0)
    p.add_argument("--start-value", type=float, default=100.0)
    return p.parse_args()


def _dataset(flow_csv: str, price_csv: str) -> tuple[pd.DataFrame, list[str]]:
    f = pd.read_csv(flow_csv)[["date", "net_flow_usd"]].copy()
    p = pd.read_csv(price_csv)[["date", "close"]].copy()

    f["date"] = pd.to_datetime(f["date"]).dt.date
    p["date"] = pd.to_datetime(p["date"]).dt.date

    df = pd.merge(f, p, on="date", how="inner").sort_values("date")
    df["flow_b"] = df["net_flow_usd"] / 1_000_000_000

    # Use lagged flow so decisions at t do not use same-day post-close data.
    df["flow_lag1"] = df["flow_b"].shift(1)

    eps = 1e-9
    df["flow_mean20"] = df["flow_lag1"].rolling(20).mean()
    df["flow_std20"] = df["flow_lag1"].rolling(20).std()
    df["flow_mean60"] = df["flow_lag1"].rolling(60).mean()
    df["flow_std60"] = df["flow_lag1"].rolling(60).std()
    df["flow_abs_med60"] = df["flow_lag1"].abs().rolling(60).median()

    # Regime-adaptive (scale-invariant) flow features.
    df["flow_z20"] = (df["flow_lag1"] - df["flow_mean20"]) / (df["flow_std20"] + eps)
    df["flow_z60"] = (df["flow_lag1"] - df["flow_mean60"]) / (df["flow_std60"] + eps)
    df["flow_rel60"] = df["flow_lag1"] / (df["flow_abs_med60"] + eps)
    df["flow_trend_5_20"] = (
        df["flow_lag1"].rolling(5).mean() - df["flow_lag1"].rolling(20).mean()
    ) / (df["flow_std60"] + eps)

    # Price regime/context features (lagged).
    df["ret_1d_pct"] = df["close"].pct_change() * 100.0
    df["ret_1d_lag1"] = df["ret_1d_pct"].shift(1)
    df["ret_5d_lag1"] = ((df["close"] / df["close"].shift(5)) - 1.0).shift(1) * 100.0
    df["ret_std20_lag1"] = df["ret_1d_pct"].rolling(20).std().shift(1)
    df["flow_x_mom"] = df["flow_z60"] * np.sign(df["ret_5d_lag1"].fillna(0.0))

    # Targets and realized returns.
    df["target_48h_return_pct"] = (df["close"].shift(-2) / df["close"] - 1.0) * 100.0
    df["next_day_ret_pct"] = df["close"].shift(-1) / df["close"] - 1.0

    feature_cols = [
        "flow_z20",
        "flow_z60",
        "flow_rel60",
        "flow_trend_5_20",
        "ret_1d_lag1",
        "ret_5d_lag1",
        "ret_std20_lag1",
        "flow_x_mom",
    ]

    df = df.dropna(subset=feature_cols + ["target_48h_return_pct", "next_day_ret_pct"]).reset_index(drop=True)
    return df, feature_cols


def _fit_model(x: np.ndarray, y: np.ndarray) -> tuple[Pipeline, float]:
    model = Pipeline(
        steps=[
            ("scale", StandardScaler()),
            (
                "mlp",
                MLPRegressor(
                    hidden_layer_sizes=(48, 24),
                    activation="relu",
                    random_state=42,
                    max_iter=2000,
                    early_stopping=True,
                    validation_fraction=0.2,
                ),
            ),
        ]
    )
    model.fit(x, y)
    resid_std = float(np.std(y - model.predict(x)))
    return model, max(resid_std, 0.05)


def _max_drawdown(equity: pd.Series) -> float:
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    return float(dd.min())


def _annualized_return(equity: pd.Series, periods_per_year: int = 365) -> float:
    n = len(equity)
    if n < 2:
        return 0.0
    total = float(equity.iloc[-1] / equity.iloc[0])
    years = n / periods_per_year
    if years <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def main() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    df, feature_cols = _dataset(args.flow_csv, args.price_csv)

    test_start = pd.to_datetime(args.test_start_date).date()
    start_candidates = df.index[df["date"] >= test_start]
    if len(start_candidates) == 0:
        raise RuntimeError(f"No rows found on/after test start date {args.test_start_date}")
    start_idx = int(start_candidates[0])

    if start_idx < args.min_train:
        raise RuntimeError(
            f"Not enough pre-test training rows before {args.test_start_date}. Have {start_idx}, need >= {args.min_train}."
        )

    model: Pipeline | None = None
    resid_std = 0.2
    records: list[dict[str, float | str]] = []

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        row_date = pd.to_datetime(row["date"])

        retrain_step = i - start_idx
        if model is None or retrain_step % args.retrain_every == 0:
            train = df.iloc[:i].copy()
            if args.train_lookback_days > 0:
                cutoff = (row_date - pd.Timedelta(days=args.train_lookback_days)).date()
                train = train[train["date"] >= cutoff]

            if len(train) < args.min_train:
                continue

            x_train = train[feature_cols].to_numpy(dtype=float)
            y_train = train["target_48h_return_pct"].to_numpy(dtype=float)
            model, resid_std = _fit_model(x_train, y_train)

        if model is None:
            continue

        x = row[feature_cols].to_numpy(dtype=float).reshape(1, -1)
        expected = float(model.predict(x)[0])

        z_conf = abs(expected) / max(resid_std, 0.05)
        confidence = min(0.5 + 0.1 * z_conf, 0.95)

        signal = make_signal(
            forecast=type("F", (), {"expected_return_pct": expected, "confidence": confidence})(),
            max_abs_position_btc=args.max_position,
            confidence_threshold=args.confidence_threshold,
            min_abs_return_pct=args.min_abs_return_pct,
        )

        prev_pos = 0.0 if not records else float(records[-1]["position_btc"])
        position = float(signal.target_position_btc)
        turnover = abs(position - prev_pos)
        tx_cost = turnover * (args.tx_cost_bps / 10_000.0)

        gross_ret = position * float(row["next_day_ret_pct"])
        net_ret = gross_ret - tx_cost

        records.append(
            {
                "date": row["date"].isoformat(),
                "expected_return_pct": expected,
                "confidence": confidence,
                "signal_side": signal.side,
                "signal_reason": signal.reason,
                "position_btc": position,
                "turnover": turnover,
                "tx_cost": tx_cost,
                "gross_strategy_ret": gross_ret,
                "strategy_ret": net_ret,
                "benchmark_ret": float(row["next_day_ret_pct"]),
            }
        )

    bt = pd.DataFrame(records)
    if bt.empty:
        raise RuntimeError("No out-of-sample rows were simulated. Check date/min-train settings.")

    bt["strategy_equity"] = (1.0 + bt["strategy_ret"]).cumprod()
    bt["benchmark_equity"] = (1.0 + bt["benchmark_ret"]).cumprod()
    bt["strategy_value"] = args.start_value * bt["strategy_equity"]
    bt["benchmark_value"] = args.start_value * bt["benchmark_equity"]

    bt.to_csv(out_dir / "v2_timeseries.csv", index=False)

    # Trade blotter.
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
        elif prev_pos * pos < 0.0:
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
                "signal_reason": row["signal_reason"],
                "expected_return_pct": row["expected_return_pct"],
                "confidence": row["confidence"],
                "strategy_ret_that_day": row["strategy_ret"],
                "benchmark_ret_that_day": row["benchmark_ret"],
                "strategy_value_after": row["strategy_value"],
                "benchmark_value_after": row["benchmark_value"],
            }
        )
        prev_pos = pos

    blotter = pd.DataFrame(blotter_rows)
    blotter.to_csv(out_dir / "v2_trade_blotter.csv", index=False)

    summary = {
        "test_start_date": args.test_start_date,
        "train_lookback_days": int(args.train_lookback_days),
        "rows": int(len(bt)),
        "n_trade_events": int(len(blotter)),
        "confidence_threshold": float(args.confidence_threshold),
        "min_abs_return_pct": float(args.min_abs_return_pct),
        "max_position": float(args.max_position),
        "retrain_every": int(args.retrain_every),
        "tx_cost_bps": float(args.tx_cost_bps),
        "strategy_final_value": float(bt["strategy_value"].iloc[-1]),
        "benchmark_final_value": float(bt["benchmark_value"].iloc[-1]),
        "strategy_ann_return": _annualized_return(bt["strategy_value"]),
        "benchmark_ann_return": _annualized_return(bt["benchmark_value"]),
        "strategy_max_drawdown": _max_drawdown(bt["strategy_value"]),
        "benchmark_max_drawdown": _max_drawdown(bt["benchmark_value"]),
        "pct_days_in_market": float((bt["position_btc"].abs() > 0).mean()),
    }
    (out_dir / "v2_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Portfolio evolution chart ($100 basis by default).
    fig, ax = plt.subplots(figsize=(11, 5))
    dates = pd.to_datetime(bt["date"])
    ax.plot(dates, bt["strategy_value"], label="Strategy V2", linewidth=2.0)
    ax.plot(dates, bt["benchmark_value"], label="Buy & Hold", linewidth=1.8)
    ax.set_title(f"Out-of-Sample From {args.test_start_date}: $ {args.start_value:.0f} Start")
    ax.set_xlabel("Date")
    ax.set_ylabel("Portfolio Value ($)")
    ax.grid(alpha=0.2)
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "v2_portfolio_100usd.png", dpi=160)
    plt.close(fig)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
